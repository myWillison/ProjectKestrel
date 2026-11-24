"""Chinese display names for the bundled bird catalog (display-only overlay).

``analyzer/models/birds/birds_global_zh.csv`` maps the catalog's scientific
names to eBird/AviList Chinese names; ``birds_family_zh.csv`` maps scientific
family names to Chinese family names. Both are built by
``tools/build_zh_names.py`` from the eBird official Chinese name table
(augmented by the ChinaBirds checklist for families and the handful of
species eBird lacks).

Storage everywhere else — the folder CSV's ``species`` column, scene tags,
folder settings, XMP sidecars — stays canonical English. This overlay only
feeds display and search matching when the user picks ``zh`` for the
``species_name_language`` setting, so regenerating it never invalidates a
folder or breaks compatibility with the hosted add-ons.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from itertools import product

from .config import MODELS_DIR

ZH_NAMES_PATH = MODELS_DIR / "birds" / "birds_global_zh.csv"
FAMILY_ZH_PATH = MODELS_DIR / "birds" / "birds_family_zh.csv"

# Languages the ``species_name_language`` setting may hold. ``en`` keeps the
# catalog's canonical (IOC/eBird) English names; ``zh`` shows the simplified
# Chinese names from the overlay.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "zh")

# Toned pinyin vowels -> plain ASCII letters (ü becomes "v", the IME spelling;
# ``pinyin_compact_variants`` also yields the "u" spelling people type instead).
PINYIN_UNACCENT = str.maketrans({
    "ā": "a", "á": "a", "ǎ": "a", "à": "a",
    "ē": "e", "é": "e", "ě": "e", "è": "e",
    "ī": "i", "í": "i", "ǐ": "i", "ì": "i",
    "ō": "o", "ó": "o", "ǒ": "o", "ò": "o",
    "ū": "u", "ú": "u", "ǔ": "u", "ù": "u",
    "ü": "v", "ǖ": "v", "ǘ": "v", "ǚ": "v", "ǜ": "v",
    "ń": "n", "ň": "n", "ǹ": "n", "ḿ": "m",
})


@dataclass(frozen=True)
class ZhSpeciesName:
    """Everything the overlay knows about one species, keyed by scientific name."""
    zh: str
    zh_traditional: str
    pinyin: str          # toned, one syllable per character: "pǔ tōng cuì niǎo"
    ebird_code: str      # eBird species code, e.g. "comkin1"


def unaccent_pinyin(pinyin: str) -> list[str]:
    """Toned syllables -> plain ones (``"lǜ tóu yā"`` -> ``["lv", "tou", "ya"]``)."""
    return [syl.translate(PINYIN_UNACCENT) for syl in str(pinyin or "").split()]


def pinyin_compact_variants(pinyin: str) -> tuple[str, ...]:
    """Every untoned, space-less spelling a user might type for the name.

    Syllables are joined without separators ("putongcuiniao"): people type
    pinyin by word, not by character, and we cannot know where they will put
    the spaces -- the search folds the query the same way. Each ``v`` (ü) also
    yields the ``u`` spelling, so both "lvtouya" and "lutouya" resolve.
    """
    syls = unaccent_pinyin(pinyin)
    if not syls:
        return ()
    choices = [(s, s.replace("v", "u")) if "v" in s else (s,) for s in syls]
    out = {"".join(parts) for parts in product(*choices)}
    return tuple(sorted(out))


def pinyin_initials(pinyin: str) -> str:
    """First letter of every syllable ("pǔ tōng cuì niǎo" -> "ptcn")."""
    return "".join(s[0] for s in unaccent_pinyin(pinyin) if s)



def normalize_language(value) -> str:
    """Sanitise a stored language preference into ``"en"`` or ``"zh"``."""
    s = str(value or "").strip().lower()
    return s if s in SUPPORTED_LANGUAGES else "en"


def normalize_scientific_name(name: str) -> str:
    """Lowercase and drop the catalog's extinct dagger marks."""
    return " ".join(str(name or "").replace("†", " ").split()).lower()


@lru_cache(maxsize=1)
def zh_species_info() -> dict[str, ZhSpeciesName]:
    """``{ scientific_name(lower): ZhSpeciesName }``. Empty when the overlay is absent."""
    out: dict[str, ZhSpeciesName] = {}
    try:
        with open(ZH_NAMES_PATH, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sci = normalize_scientific_name(row.get("scientific_name") or "")
                zh = (row.get("zh_name") or "").strip()
                if sci and zh:
                    out[sci] = ZhSpeciesName(
                        zh=zh,
                        zh_traditional=(row.get("zh_name_traditional") or "").strip(),
                        pinyin=(row.get("pinyin") or "").strip(),
                        ebird_code=(row.get("ebird_code") or "").strip(),
                    )
    except OSError:
        pass
    return out


@lru_cache(maxsize=1)
def zh_name_map() -> dict[str, str]:
    """``{ scientific_name(lower): zh name }``. Empty when the overlay is absent."""
    return {sci: info.zh for sci, info in zh_species_info().items()}


@lru_cache(maxsize=1)
def family_zh_map() -> dict[str, str]:
    """``{ family_sci(lower): zh family name }``. Empty when the overlay is absent."""
    out: dict[str, str] = {}
    try:
        with open(FAMILY_ZH_PATH, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                fam = normalize_scientific_name(row.get("family_sci") or "")
                zh = (row.get("family_zh") or "").strip()
                if fam and zh:
                    out[fam] = zh
    except OSError:
        pass
    return out


# Characters that appear in family names but in no species name, so the
# per-character pinyin derived from species names never sees them.
_FAMILY_PINYIN_FALLBACK = {
    "科": "kē", "儒": "rú", "冢": "zhǒng", "吹": "chuī", "烈": "liè",
    "聊": "liáo", "调": "tiáo", "鳾": "shī", "鴷": "liè", "鷉": "tī", "鸊": "pì",
}


def char_pinyin_map(species: dict[str, ZhSpeciesName]) -> dict[str, str]:
    """``{ hanzi: toned syllable }`` voted from every species name.

    The overlay's pinyin is one syllable per character, so each name is an
    alignment of the two. Polyphonic characters resolve to whichever reading
    the most species use -- good enough to make a family name such as 翠鸟科
    searchable by pinyin.
    """
    votes: dict[str, dict[str, int]] = {}
    for info in species.values():
        chars = [c for c in info.zh if c.strip()]
        syls = info.pinyin.split()
        if len(chars) != len(syls):
            continue
        for c, syl in zip(chars, syls):
            votes.setdefault(c, {})
            votes[c][syl] = votes[c].get(syl, 0) + 1
    out = dict(_FAMILY_PINYIN_FALLBACK)
    for c, tally in votes.items():
        if c not in out:
            out[c] = max(tally.items(), key=lambda kv: kv[1])[0]
    return out


def pinyin_for_zh(zh: str, char_map: dict[str, str]) -> str:
    """Toned pinyin for a Chinese name built from ``char_pinyin_map``, or ``""``
    when any character is unknown."""
    syls = []
    for c in str(zh or ""):
        if not c.strip():
            continue
        syl = char_map.get(c)
        if not syl:
            return ""
        syls.append(syl)
    return " ".join(syls)


def zh_name_for_scientific(scientific_name: str) -> str:
    """Chinese name for a scientific binomial, or ``""`` when unmapped."""
    return zh_name_map().get(normalize_scientific_name(scientific_name), "")


def zh_family_for_scientific(family_sci: str) -> str:
    """Chinese family name for a scientific family, or ``""`` when unmapped."""
    return family_zh_map().get(normalize_scientific_name(family_sci), "")
