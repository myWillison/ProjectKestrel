"""Google-style name search over the bird catalog: species and families.

One index answers every way a bird can be named -- Chinese (simplified and
traditional), English, English aliases, scientific name, pinyin, pinyin
initials, eBird / alpha codes -- with prefix, substring, token-prefix,
subsequence and typo tolerance, ranked by how specific the hit is. Chinese
comes first: a Chinese query can only hit the Chinese fields, and the pinyin
tiers sit just under the English exact tier, so "putong" finds 普通翠鸟 ahead
of any English name that merely contains those letters.

Recall (who is considered) is index-driven, so a query never scans the 11k
rows, and it records *which key* matched so the scorer only looks at those:

* Latin keys -- a word-level inverted index. Every key is split into words
  ("white throated kingfisher" -> white / throated / kingfisher; compact keys
  such as "putongcuiniao" or "comkin1" are one word each). A query's tokens
  address words by prefix (bisect over the sorted word list), by substring
  (bigram index over the words) or by typo (one edit at 5+ letters, two at 10+,
  only for tokens that matched nothing otherwise); the per-token hit sets are
  intersected. The alpha-code habit of typing a subsequence ("amrob") is served
  from a per-first-letter bucket of the compact English names.
* Chinese keys -- unigram and bigram indexes over the compact names, since
  ideographs have no word boundaries.

Adding another identifier (an IOC id, a local-language name list, ...) is one
more ``Key`` with a weight in ``_EXACT`` / ``_PREFIX`` / ``_SUBSTRING``; the
index and the scorer need no other change.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from itertools import product
from typing import Iterable

from .query_parser import _lev_within, _typo_budget, family_parts
from .species_names import PINYIN_UNACCENT

_CJK_RANGES = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK = re.compile(f"[{_CJK_RANGES}]")
# Anything that is not a letter, digit or ideograph separates words. Apostrophes
# are deleted rather than turned into spaces so "Wilson's" and "wilsons" agree.
_ASCII_SEPARATORS = re.compile(r"[^0-9a-z]+")
_SEPARATORS = re.compile(f"[^0-9a-z{_CJK_RANGES}]+")


def _strip_accents(text: str) -> str:
    """Typed pinyin sometimes keeps its tone marks ("zé"); fold them to the
    plain letter (Unicode decomposition, drop the combining marks) instead of
    treating the accent as a word separator."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))

# ----------------------------------------------------------------------------
# Score tiers. Higher wins; ties break on shorter label, then A-Z. The ladder
# keeps the historical ordering (alpha code > exact > prefix > token prefix >
# substring) and slots the Chinese / pinyin tiers in.
# ----------------------------------------------------------------------------
SCORE_CODE_EXACT = 1000      # eBird code ("comkin1") or alpha-4 ("amro")
SCORE_ZH_EXACT = 960         # 中文优先: a Chinese name outranks an English one
SCORE_ZH_TRAD_EXACT = 950    # traditional spelling (蒼翡翠) as an alias
SCORE_PINYIN_EXACT = 945     # full untoned pinyin, "putongcuiniao"
SCORE_INITIALS_EXACT = 940   # "ptcn"
SCORE_COMMON_EXACT = 900
SCORE_ALIAS_EXACT = 880
SCORE_SCI_EXACT = 860
SCORE_COMMON_PREFIX = 800
SCORE_ZH_PREFIX = 795
SCORE_ZH_TRAD_PREFIX = 785
SCORE_ALIAS_PREFIX = 770
SCORE_SCI_PREFIX = 750
SCORE_PINYIN_PREFIX = 745
SCORE_INITIALS_PREFIX = 740
SCORE_TOKEN_PREFIX = 700     # every typed token starts a word of the name
SCORE_ZH_SUBSTRING = 515
SCORE_SUBSTRING = 500        # the typed letters run consecutively somewhere
SCORE_SUBSEQUENCE = 480      # "amrob" -> americanrobin (looser than a run)
SCORE_PINYIN_SUBSTRING = 470
SCORE_SCI_SUBSTRING = 450
SCORE_FUZZY_PENALTY = 150    # a typo costs this much off the field's exact score
_FUZZY_FIELDS = frozenset({"en", "alias", "sci", "pinyin"})

_EXACT = {
    "code": SCORE_CODE_EXACT, "zh": SCORE_ZH_EXACT, "zh_trad": SCORE_ZH_TRAD_EXACT,
    "pinyin": SCORE_PINYIN_EXACT, "initials": SCORE_INITIALS_EXACT, "en": SCORE_COMMON_EXACT,
    "alias": SCORE_ALIAS_EXACT, "sci": SCORE_SCI_EXACT,
}
_PREFIX = {
    # A code prefix is weak evidence until most of the code is typed, so it
    # sits under an English or scientific prefix (the exact code stays on top).
    "code": 760, "zh": SCORE_ZH_PREFIX, "zh_trad": SCORE_ZH_TRAD_PREFIX, "en": SCORE_COMMON_PREFIX,
    "alias": SCORE_ALIAS_PREFIX, "sci": SCORE_SCI_PREFIX, "pinyin": SCORE_PINYIN_PREFIX,
    "initials": SCORE_INITIALS_PREFIX,
}
_SUBSTRING = {
    "zh": SCORE_ZH_SUBSTRING, "zh_trad": 505, "en": SCORE_SUBSTRING, "alias": 495,
    "pinyin": SCORE_PINYIN_SUBSTRING, "sci": SCORE_SCI_SUBSTRING,
}

# Shortest Latin fragment worth a substring / subsequence lookup; below this
# the hit sets are the whole catalog and the dropdown would be noise anyway.
_MIN_SUBSTRING = 4
_MAX_SUBSEQUENCE = 6


def normalize(text: str) -> str:
    """Lowercase, drop apostrophes, fold every other separator to one space.

    A string containing ideographs loses its spaces entirely: Chinese names
    are indexed and queried compact ("大白鹭"), so a stray space between two
    characters ("大白 鹭") cannot split the match.
    """
    t = _strip_accents(str(text or "").lower()).replace("'", "").replace("’", "")
    if t.isascii():
        return " ".join(_ASCII_SEPARATORS.sub(" ", t).split())
    t = " ".join(_SEPARATORS.sub(" ", t).split())
    if _CJK.search(t):
        t = t.replace(" ", "")
    return t


def compact(text: str) -> str:
    """``normalize`` without spaces ("White-throated Kingfisher" -> "whitethroatedkingfisher")."""
    return normalize(text).replace(" ", "")


def _within_one(a: str, b: str) -> bool:
    """Levenshtein(a, b) <= 1 without the DP: after the first differing
    position, one substitution leaves equal tails; one insertion/deletion
    leaves the shorter string's tail equal to the longer's tail minus a char."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    return a[i + 1:] == b[i + 1:] if la == lb else a[i:] == b[i + 1:]


@dataclass(frozen=True)
class Key:
    """One searchable spelling of one entry.

    ``words`` is non-empty only for the spaced form of a multi-word Latin key;
    compact keys (pinyin, codes, glued English) carry ``()`` and are compared
    against the space-less query instead.
    """
    field: str
    text: str
    words: tuple[str, ...] = ()


def _token_prefix_match(query_tokens: list[str], target_tokens: tuple[str, ...]) -> bool:
    """Every query token must be a prefix of some unique target token."""
    if not query_tokens:
        return True
    if len(query_tokens) > len(target_tokens):
        return False
    used = [False] * len(target_tokens)
    for q in query_tokens:
        matched = False
        for i, t in enumerate(target_tokens):
            if not used[i] and t.startswith(q):
                used[i] = True
                matched = True
                break
        if not matched:
            return False
    return True


def _is_subsequence(needle: str, haystack: str) -> bool:
    """True if ``needle``'s letters appear in order within ``haystack``."""
    if not needle:
        return True
    i = 0
    for ch in haystack:
        if ch == needle[i]:
            i += 1
            if i == len(needle):
                return True
    return False


def _add_latin(keys: list[Key], field: str, text: str) -> None:
    spaced = normalize(text)
    if not spaced:
        return
    words = tuple(spaced.split())
    keys.append(Key(field, spaced, words if len(words) > 1 else ()))
    glued = spaced.replace(" ", "")
    if glued != spaced:
        keys.append(Key(field, glued, ()))


def _dedupe(keys: list[Key]) -> tuple[Key, ...]:
    seen: set[tuple[str, str]] = set()
    out: list[Key] = []
    for k in keys:
        marker = (k.field, k.text)
        if marker not in seen:
            seen.add(marker)
            out.append(k)
    return tuple(out)


def _pinyin_keys(keys: list[Key], pinyin: str) -> None:
    """Compact untoned pinyin (``"putongcuiniao"``) and its initials (``"ptcn"``).

    Syllables are joined without separators: people type pinyin by word, not by
    character, and we cannot know where they will put the spaces -- the query
    is folded the same way. Each ``v`` (ü) also yields the ``u`` spelling, so
    both "lvtouya" and "lutouya" resolve.
    """
    syls = [syl.translate(PINYIN_UNACCENT) for syl in str(pinyin or "").split()]
    if not syls:
        return
    choices = [(s, s.replace("v", "u")) if "v" in s else (s,) for s in syls]
    for joined in sorted({"".join(parts) for parts in product(*choices)}):
        keys.append(Key("pinyin", joined, ()))
    initials = "".join(s[0] for s in syls)
    if len(initials) >= 2:
        keys.append(Key("initials", initials, ()))


def species_keys(rec) -> tuple[Key, ...]:
    """Every searchable spelling of a ``BirdRecord``. Pure in the record's
    fields, so it can be cached per record."""
    keys: list[Key] = []
    _add_latin(keys, "en", rec.canonical_common_name or "")
    for alias in getattr(rec, "aliases", ()) or ():
        _add_latin(keys, "alias", alias)
    _add_latin(keys, "sci", rec.scientific_name or "")
    alpha = (getattr(rec, "alpha_4", "") or "").strip().lower()
    if alpha:
        keys.append(Key("code", alpha, ()))
    code = (getattr(rec, "ebird_code", "") or "").strip().lower()
    if code:
        keys.append(Key("code", code, ()))
    # Chinese names carry no separators; only fold the odd stray space.
    zh = (getattr(rec, "zh_name", "") or "").replace(" ", "")
    if zh:
        keys.append(Key("zh", zh, ()))
    trad = (getattr(rec, "zh_name_traditional", "") or "").replace(" ", "")
    if trad and trad != zh:
        keys.append(Key("zh_trad", trad, ()))
    _pinyin_keys(keys, getattr(rec, "pinyin", "") or "")
    return _dedupe(keys)


def family_keys(entry) -> tuple[Key, ...]:
    """Every searchable spelling of a ``FamilyEntry``.

    The display family ("Hawk/Eagle/Kite sp.") is indexed whole and as each of
    its parts, so "eagle" resolves to the family the way "hawk" does.
    """
    keys: list[Key] = []
    _add_latin(keys, "en", entry.family_common or "")
    for part in family_parts(entry.family_common or ""):
        _add_latin(keys, "alias", part)
    _add_latin(keys, "sci", entry.family_sci or "")
    zh = (getattr(entry, "family_zh", "") or "").replace(" ", "")
    if zh:
        keys.append(Key("zh", zh, ()))
    _pinyin_keys(keys, getattr(entry, "family_pinyin", "") or "")
    return _dedupe(keys)


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------

def _score_key(key: Key, qs: str, qc: str, tokens: list[str], cjk: bool, fuzzy: bool) -> int:
    target = qs if key.words else qc
    if not target:
        return 0
    text = key.text
    field = key.field
    if text == target:
        return _EXACT[field]
    if text.startswith(target):
        return _PREFIX.get(field, 0)
    if field in ("en", "alias") and key.words and _token_prefix_match(tokens, key.words):
        return SCORE_TOKEN_PREFIX
    if field in _SUBSTRING and target in text:
        return _SUBSTRING[field]
    if not cjk:
        if field == "en" and not key.words and len(tokens) == 1 and _is_subsequence(qc, text):
            return SCORE_SUBSEQUENCE
        # Typos only make sense for names; codes and initials are short
        # identifiers whose near-misses would swamp real prefix hits.
        if fuzzy and field in _FUZZY_FIELDS:
            # Same typo budget as the smart-search parser: one edit at 5+
            # letters, two at 10+, and a one-edit match must agree on the
            # first letter. Multi-word keys are compared word by word, since
            # the recall found the typo inside one of their words.
            if key.words:
                if any(_near(t, w) for t in tokens for w in key.words):
                    return _EXACT[field] - SCORE_FUZZY_PENALTY
            elif _near(target, text):
                return _EXACT[field] - SCORE_FUZZY_PENALTY
    return 0


def _near(a: str, b: str) -> bool:
    """``b`` within the typo budget of ``a`` (never called below 5 letters)."""
    budget = _typo_budget(a)
    if not budget:
        return False
    if a == b:
        return False
    if budget == 1:
        return b[0] == a[0] and _within_one(a, b)
    return _lev_within(a, b, budget)


def score_keys(query: str, keys: tuple[Key, ...], fuzzy: bool = True) -> int:
    """Best tier any key of one entry reaches for ``query``; ``0`` = no match.

    ``fuzzy=False`` skips typo matching, which the index turns off whenever the
    query already hit something the cheap way."""
    q = (query or "").strip()
    if not q or not keys:
        return 0
    qs = normalize(q)
    qc = qs.replace(" ", "")
    cjk = bool(_CJK.search(qs))
    tokens = qs.split()
    return max((_score_key(k, qs, qc, tokens, cjk, fuzzy) for k in keys), default=0)


# ----------------------------------------------------------------------------
# Index
# ----------------------------------------------------------------------------

@dataclass
class _Entry:
    kind: str                 # "species" | "family"
    obj: object               # BirdRecord | FamilyEntry (owns the region rule)
    keys: tuple[Key, ...]
    label: str                # canonical (English) name used for tie-breaking
    zh: str = ""              # Chinese name, the tie-break label for CJK queries


# A recall hit: (entry position, position of the matched key inside the entry).
_Hit = tuple[int, int]


class NameIndex:
    """Inverted indexes over the catalog's species and family entries.

    Built once per ``BirdCatalog``. All lookups are dict / bisect operations
    over precomputed structures; the only per-query Python loop runs over the
    (small) candidate set, never over the catalog.
    """

    def __init__(self, records: Iterable, families: Iterable = ()):
        self._entries: list[_Entry] = []
        # Latin: word -> hits. Words come from spaced keys (one per word) and
        # from compact keys (the whole key is one word).
        self._word_hits: dict[str, list[_Hit]] = defaultdict(list)
        # Chinese: unigram / bigram -> entry positions (over zh / zh_trad keys,
        # at most two per entry, so the scorer simply looks at both).
        self._cjk_uni: dict[str, set[int]] = defaultdict(set)
        self._cjk_bi: dict[str, set[int]] = defaultdict(set)
        # Compact English names by first letter, for subsequence ("amrob") recall.
        self._compact_by_first: dict[str, list[_Hit]] = defaultdict(list)

        word_hits = self._word_hits
        for kind, obj, keys, label, zh in self._iter(records, families):
            idx = len(self._entries)
            self._entries.append(_Entry(kind, obj, keys, label, zh))
            for pos, key in enumerate(keys):
                text = key.text
                if _CJK.search(text):
                    self._index_cjk(text, idx)
                elif key.words:
                    for w in key.words:
                        word_hits[w].append((idx, pos))
                else:
                    word_hits[text].append((idx, pos))
                    if key.field == "en":
                        self._compact_by_first[text[0]].append((idx, pos))

        self._words = sorted(word_hits)
        self._wid = {w: i for i, w in enumerate(self._words)}
        # Substring recall: bigram -> word ids (two levels keep memory flat even
        # for words shared by hundreds of names).
        self._bigram_wids: dict[str, set[int]] = defaultdict(set)
        # Typo recall: (first letter, length) -> words, like the query parser.
        self._by_len: dict[tuple[str, int], list[str]] = defaultdict(list)
        for w in self._words:
            self._by_len[(w[0], len(w))].append(w)
            wid = self._wid[w]
            for i in range(len(w) - 1):
                self._bigram_wids[w[i:i + 2]].add(wid)

    @staticmethod
    def _iter(records, families):
        for r in records:
            yield "species", r, species_keys(r), r.canonical_common_name, r.zh_name
        for f in families:
            yield "family", f, family_keys(f), f.family_common, f.family_zh

    def _index_cjk(self, text: str, idx: int) -> None:
        uni = self._cjk_uni
        bi = self._cjk_bi
        prev = None
        for ch in text:
            uni[ch].add(idx)
            if prev is not None:
                bi[prev + ch].add(idx)
            prev = ch

    def __len__(self) -> int:
        return len(self._entries)

    @staticmethod
    def _sort_key(score: int, entry: _Entry, cjk: bool) -> tuple:
        # Same tier: shorter name first, then A-Z -- in the language the query
        # was typed in, so two homophone families (莺科 / 鹰科) order sensibly.
        label = entry.zh if cjk and entry.zh else entry.label
        label = label.lower()
        return (-score, len(label), label)

    # -- recall ---------------------------------------------------------------

    def _words_with_prefix(self, token: str) -> set[_Hit]:
        """Hits of every word starting with ``token`` (also the glued query)."""
        words = self._words
        hits = self._word_hits
        out: set[_Hit] = set()
        i = bisect_left(words, token)
        n = len(words)
        while i < n and words[i].startswith(token):
            out.update(hits[words[i]])
            i += 1
        return out

    def _words_containing(self, fragment: str) -> set[_Hit]:
        """Hits of every word containing ``fragment`` (bigram pre-filter)."""
        wids: set[int] | None = None
        for i in range(len(fragment) - 1):
            s = self._bigram_wids.get(fragment[i:i + 2])
            if not s:
                return set()
            wids = set(s) if wids is None else (wids & s)
            if not wids:
                return set()
        out: set[_Hit] = set()
        for wid in wids or ():
            w = self._words[wid]
            if fragment in w:
                out.update(self._word_hits[w])
        return out

    def _words_like(self, token: str) -> set[_Hit]:
        """Hits of words within the typo budget of ``token`` (exact excluded)."""
        budget = _typo_budget(token)
        if not budget:
            return set()
        out: set[_Hit] = set()
        # Buckets are keyed by the first letter, so even a two-edit match still
        # has to agree on it -- the same restriction the query parser makes.
        first = token[0]
        for length in range(max(2, len(token) - budget), len(token) + budget + 1):
            for w in self._by_len.get((first, length), ()):
                if w == token:
                    continue
                if _within_one(token, w) if budget == 1 else _lev_within(token, w, budget):
                    out.update(self._word_hits[w])
        return out

    def _subsequence_hits(self, qc: str) -> set[_Hit]:
        return {(idx, pos) for idx, pos in self._compact_by_first.get(qc[0], ())
                if _is_subsequence(qc, self._entries[idx].keys[pos].text)}

    def _recall(self, qs: str, qc: str) -> tuple[dict[int, set[int]], bool]:
        """``{entry position: {matched key positions}}`` plus whether typo
        matching was needed. Chinese queries mark every key of a hit entry
        (``None``), since there are at most two Chinese keys per entry."""
        if _CJK.search(qs):
            if len(qc) < 2:
                return dict.fromkeys(self._cjk_uni.get(qc, ())), False
            hit: set[int] | None = None
            for i in range(len(qc) - 1):
                s = self._cjk_bi.get(qc[i:i + 2])
                if not s:
                    return {}, False
                hit = set(s) if hit is None else (hit & s)
                if not hit:
                    return {}, False
            return dict.fromkeys(hit or ()), False

        tokens = [t for t in qs.split() if len(t) >= 2]
        if not tokens:
            return {}, False
        per_token: list[set[_Hit]] = []
        fuzzy_needed = False
        single = len(tokens) == 1
        for t in tokens:
            hit = self._words_with_prefix(t)
            if single:
                if len(t) >= _MIN_SUBSTRING:
                    hit |= self._words_containing(t)
                # Scattered-letter habit ("ptcn" -> PuerTo riCan ..., "amrob" ->
                # AMerican ROBin). Part of the cheap recall, so it must not be
                # skipped by the typo fallback's early return below.
                if _MIN_SUBSTRING <= len(qc) <= _MAX_SUBSEQUENCE:
                    hit |= self._subsequence_hits(qc)
            if not hit:
                # Nothing matches this token the cheap way: it may be a typo.
                hit = self._words_like(t)
                fuzzy_needed = True
            if not hit and single:
                return {}, fuzzy_needed
            per_token.append(hit)
        if single:
            hits: dict[int, set[int]] = {}
            for idx, pos in per_token[0]:
                hits.setdefault(idx, set()).add(pos)
            return hits, fuzzy_needed

        # Every token must hit the same entry...
        keep = {idx for idx, _ in per_token[0]}
        for more in per_token[1:]:
            keep &= {idx for idx, _ in more}
            if not keep:
                break
        hits = {}
        for more in per_token:
            for idx, pos in more:
                if idx in keep:
                    hits.setdefault(idx, set()).add(pos)
        # ...while pinyin and glued English are indexed without spaces, so a
        # spaced query ("cui niao ke", "putong cui niao") is also tried as one
        # word -- even when the per-word tokens matched nothing on their own.
        for idx, pos in self._words_with_prefix(qc):
            hits.setdefault(idx, set()).add(pos)
        return hits, fuzzy_needed

    # -- search ---------------------------------------------------------------

    def search(self, query: str, kind: str, regions: Iterable[str] = (),
               ignore_regions: bool = False, limit: int = 20,
               soft_regions: bool = False) -> list:
        """Ranked entries of ``kind`` matching ``query`` inside ``regions``.

        With an empty query, returns the alphabetical head of the region's
        entries (the combobox seeds its dropdown before the user types). An
        empty region selection yields nothing unless ``ignore_regions`` is set.

        ``soft_regions`` turns the geofence into a ranking preference for a
        typed query: off-region entries that match *better* than the best
        in-region hit are listed first, and off-region entries that tie with
        it follow the in-region ones. Off-region entries that match worse than
        the best in-region hit stay hidden. The caller flags the off-region
        rows (see ``Api.search_birds``). The empty query stays region-scoped.
        """
        sel = frozenset(r for r in regions if r)
        if not sel and not ignore_regions:
            return []

        def allowed(obj) -> bool:
            return ignore_regions or obj.matches_any_region(sel)

        q = (query or "").strip()
        if not q:
            head = [e for e in self._entries if e.kind == kind and allowed(e.obj)]
            head.sort(key=lambda e: e.label.lower())
            return [e.obj for e in head[:limit]]

        qs = normalize(q)
        qc = qs.replace(" ", "")
        hits, fuzzy = self._recall(qs, qc)
        if not hits:
            return []
        cjk = bool(_CJK.search(qs))
        tokens = qs.split()
        soft = soft_regions and not ignore_regions and bool(sel)
        in_region: list[tuple[tuple, object]] = []
        off_region: list[tuple[tuple, object]] = []
        for idx, positions in hits.items():
            e = self._entries[idx]
            if e.kind != kind:
                continue
            inside = allowed(e.obj)
            if not inside and not soft:
                continue
            s = max((_score_key(e.keys[p], qs, qc, tokens, cjk, fuzzy)
                     for p in (positions if positions is not None else range(len(e.keys)))), default=0)
            if s:
                (in_region if inside else off_region).append((self._sort_key(s, e, cjk), e.obj))
        in_region.sort(key=lambda t: t[0])
        if not soft or not in_region:
            # No in-region hit: every off-region match is a legitimate answer
            # (the old zero-result retry), so keep the global ranking.
            off_region.sort(key=lambda t: t[0])
            return [obj for _, obj in (in_region + off_region)[:limit]]
        best = in_region[0][0][0]  # == -score of the best in-region hit
        off_region.sort(key=lambda t: t[0])
        merged = ([t for t in off_region if t[0][0] < best] + in_region
                  + [t for t in off_region if t[0][0] == best])
        return [obj for _, obj in merged[:limit]]
