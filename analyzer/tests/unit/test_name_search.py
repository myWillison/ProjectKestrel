"""Unit tests for the Google-style name search (``kestrel_analyzer.name_search``).

Covers the contract the species / family tag comboboxes depend on:

  * Every way of naming a bird resolves: Chinese (simplified + traditional),
    English, aliases, scientific, pinyin, pinyin initials, eBird / alpha codes.
  * Ranking is by specificity (exact > prefix > token prefix > substring >
    subsequence > typo), Chinese first, with deterministic tie-breaking.
  * Recall is index-driven: the real 11k-row catalog answers in milliseconds.
"""

from pathlib import Path
import statistics
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from kestrel_analyzer import name_search
from kestrel_analyzer.bird_catalog import (
    BirdCatalog,
    BirdRecord,
    family_entry_to_dict,
    record_to_dict,
    score_record,
)
from kestrel_analyzer.query_parser import QueryIndex, parse_query
from kestrel_analyzer.species_names import (
    char_pinyin_map,
    pinyin_compact_variants,
    pinyin_initials,
    zh_species_info,
)

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------------
# Synthetic catalog: one in-region bird and two kingfishers from abroad
# ----------------------------------------------------------------------------

def _rec(common, sci, regions, zh="", trad="", pinyin="", code="", alpha="", aliases=()):
    return BirdRecord(
        canonical_common_name=common, scientific_name=sci, family_sci="Alcedinidae",
        family_common="Kingfisher sp.", order="Coraciiformes", regions=frozenset(regions),
        alpha_4=alpha, aliases=tuple(aliases), is_model_species=True,
        zh_name=zh, zh_name_traditional=trad, pinyin=pinyin, ebird_code=code,
    )


@pytest.fixture(scope="module")
def tiny() -> BirdCatalog:
    return BirdCatalog([
        _rec("American Robin", "Turdus migratorius", {"NA"}, zh="旅鸫", trad="旅鶇",
             pinyin="lǚ dōng", code="amerob", alpha="AMRO"),
        _rec("Common Kingfisher", "Alcedo atthis", {"PAL", "OR"}, zh="普通翠鸟", trad="普通翠鳥",
             pinyin="pǔ tōng cuì niǎo", code="comkin1"),
        _rec("White-throated Kingfisher", "Halcyon smyrnensis", {"OR"}, zh="白胸翡翠", trad="蒼翡翠",
             pinyin="bái xiōng fěi cuì", code="whakin1", aliases=("White-breasted Kingfisher",)),
        _rec("Bank Swallow", "Riparia riparia", {"NA"}, aliases=("Sand Martin",)),
    ])


ALL = ("NA", "PAL", "OR")
NA = ("NA",)


def _names(cat, q, regions=ALL, **kw):
    return [r.canonical_common_name for r in cat.search(q, regions, **{"limit": 10, **kw})]


class TestPinyinHelpers:

    def test_unaccent_and_initials(self):
        assert pinyin_compact_variants("pǔ tōng cuì niǎo") == ("putongcuiniao",)
        assert pinyin_initials("pǔ tōng cuì niǎo") == "ptcn"

    def test_u_umlaut_yields_both_spellings(self):
        # lǜ can be typed "lv" (IME) or "lu"; both must resolve.
        assert pinyin_compact_variants("lǜ tóu yā") == ("lutouya", "lvtouya")

    def test_empty(self):
        assert pinyin_compact_variants("") == ()
        assert pinyin_initials("") == ""


class TestEverySpellingResolves:

    def test_chinese_exact(self, tiny):
        assert _names(tiny, "普通翠鸟") == ["Common Kingfisher"]

    def test_chinese_with_a_stray_space(self, tiny):
        assert _names(tiny, "普通 翠鸟") == ["Common Kingfisher"]

    def test_traditional_chinese_is_an_alias(self, tiny):
        assert _names(tiny, "蒼翡翠") == ["White-throated Kingfisher"]
        assert _names(tiny, "旅鶇") == ["American Robin"]

    def test_chinese_substring(self, tiny):
        # "翠" runs inside both 普通翠鸟 and 白胸翡翠; the shorter name wins the tie.
        assert set(_names(tiny, "翠")) == {"Common Kingfisher", "White-throated Kingfisher"}
        assert _names(tiny, "翠")[0] == "Common Kingfisher"  # shorter name first
        assert set(_names(tiny, "翠鸟")) == {"Common Kingfisher"}

    def test_pinyin_compact_exact(self, tiny):
        assert _names(tiny, "putongcuiniao") == ["Common Kingfisher"]

    def test_pinyin_spaced_by_word(self, tiny):
        assert _names(tiny, "putong cui") == ["Common Kingfisher"]
        assert _names(tiny, "putong cui niao") == ["Common Kingfisher"]

    def test_pinyin_prefix(self, tiny):
        assert _names(tiny, "putong") == ["Common Kingfisher"]

    def test_pinyin_initials(self, tiny):
        assert _names(tiny, "ptcn") == ["Common Kingfisher"]

    def test_pinyin_with_tone_marks(self, tiny):
        assert _names(tiny, "pǔtōng") == ["Common Kingfisher"]

    def test_ebird_code(self, tiny):
        assert _names(tiny, "comkin1") == ["Common Kingfisher"]
        assert _names(tiny, "COMKIN1") == ["Common Kingfisher"]

    def test_alpha_code(self, tiny):
        assert _names(tiny, "amro") == ["American Robin"]

    def test_english_exact_and_prefix(self, tiny):
        assert _names(tiny, "American Robin") == ["American Robin"]
        assert _names(tiny, "amer") == ["American Robin"]

    def test_english_glued(self, tiny):
        assert _names(tiny, "americanrobin") == ["American Robin"]

    def test_token_prefix(self, tiny):
        assert _names(tiny, "amer rob") == ["American Robin"]

    def test_subsequence_alpha_habit(self, tiny):
        assert _names(tiny, "amrob") == ["American Robin"]

    def test_subsequence_without_any_other_hit(self, tiny):
        # Regression: when prefix / substring / typo recall found nothing, the
        # scattered-letter recall used to be skipped by an early return.
        assert _names(tiny, "bnkswl") == ["Bank Swallow"]
        assert _names(tiny, "snipe") == []

    def test_english_alias(self, tiny):
        assert _names(tiny, "sand martin") == ["Bank Swallow"]
        assert _names(tiny, "white breasted") == ["White-throated Kingfisher"]

    def test_scientific_name(self, tiny):
        assert _names(tiny, "alcedo") == ["Common Kingfisher"]
        assert _names(tiny, "Alcedo atthis") == ["Common Kingfisher"]

    def test_typo_in_english(self, tiny):
        assert _names(tiny, "amerian robin") == ["American Robin"]

    def test_typo_in_pinyin(self, tiny):
        assert _names(tiny, "putongcuinio") == ["Common Kingfisher"]

    def test_no_match(self, tiny):
        assert _names(tiny, "zzzzzzzz") == []


class TestRanking:

    def test_chinese_exact_beats_english_exact(self):
        rec = _rec("House Sparrow", "Passer domesticus", {"NA"}, zh="家麻雀", pinyin="jiā má què")
        assert score_record("家麻雀", rec) == name_search.SCORE_ZH_EXACT
        assert score_record("家麻雀", rec) > score_record("house sparrow", rec)

    def test_exact_beats_prefix_beats_typo(self, tiny):
        r = tiny.lookup("American Robin")
        assert score_record("American Robin", r) > score_record("Ameri", r)
        assert score_record("Ameri", r) > score_record("amerian", r) > 0

    def test_pinyin_exact_sits_between_chinese_and_english_exact(self):
        rec = _rec("Common Gull", "Larus canus", {"PAL"}, zh="普通海鸥", pinyin="pǔ tōng hǎi ōu")
        assert score_record("putonghaiou", rec) == name_search.SCORE_PINYIN_EXACT
        assert (name_search.SCORE_ZH_EXACT > name_search.SCORE_PINYIN_EXACT
                > name_search.SCORE_COMMON_EXACT > name_search.SCORE_SCI_EXACT)

    def test_substring_run_beats_scattered_subsequence(self, tiny):
        # "fisher" runs inside Kingfisher; a scattered match must rank lower.
        r = tiny.lookup("Common Kingfisher")
        assert score_record("fisher", r) == name_search.SCORE_SUBSTRING
        assert name_search.SCORE_SUBSTRING > name_search.SCORE_SUBSEQUENCE

    def test_code_prefix_does_not_outrank_a_name_prefix(self):
        rec = _rec("Common Ground Dove", "Columbina passerina", {"NA"}, code="zebdov")
        assert score_record("ze", rec) == name_search._PREFIX["code"]
        assert score_record("ze", rec) < name_search.SCORE_COMMON_PREFIX

    def test_code_typo_is_not_a_match(self):
        # "amrob" is one edit from the code AMRO; codes are identifiers, not names.
        rec = _rec("American Robin", "Turdus migratorius", {"NA"}, alpha="AMRO")
        assert score_record("amrob", rec) == name_search.SCORE_SUBSEQUENCE


class TestRegions:

    def test_geofence_hides_off_region_birds(self, tiny):
        assert _names(tiny, "普通翠鸟", NA) == []
        assert _names(tiny, "putong", NA) == []

    def test_geofence_retry_finds_them(self, tiny):
        assert _names(tiny, "普通翠鸟", NA, ignore_regions=True) == ["Common Kingfisher"]

    def test_in_region_match_is_not_swamped_by_the_whole_catalog(self, tiny):
        assert _names(tiny, "amer", NA) == ["American Robin"]

    def test_empty_query_head_stays_region_scoped(self, tiny):
        assert _names(tiny, "", NA) == ["American Robin", "Bank Swallow"]
        assert _names(tiny, "", ()) == []


class TestFamilies:

    @pytest.fixture(scope="class")
    @classmethod
    def cat(cls):
        return BirdCatalog([
            _rec("Mallard", "Anas platyrhynchos", {"NA"}),
            _rec("Common Kingfisher", "Alcedo atthis", {"OR"}, zh="普通翠鸟", pinyin="pǔ tōng cuì niǎo"),
        ])

    def _fams(self, cat, q, regions=ALL, **kw):
        return [f.family_common for f in cat.search_families(q, regions, limit=10, **kw)]

    def test_english_part(self, cat):
        assert self._fams(cat, "kingfisher") == ["Kingfisher sp."]

    def test_chinese_and_pinyin(self, cat):
        # The family name's pinyin is derived from its characters' readings in
        # the species names (翠 / 鸟 from 普通翠鸟) plus the 科 fallback.
        kf = next(f for f in cat.families if f.family_common == "Kingfisher sp.")
        assert kf.family_zh == "翠鸟科"
        assert kf.family_pinyin == "cuì niǎo kē"
        assert self._fams(cat, "翠鸟科") == ["Kingfisher sp."]
        assert self._fams(cat, "cui niao ke") == ["Kingfisher sp."]
        assert self._fams(cat, "cnk") == ["Kingfisher sp."]

    def test_payload_carries_pinyin(self, cat):
        kf = next(f for f in cat.families if f.family_common == "Kingfisher sp.")
        assert family_entry_to_dict(kf)["family_pinyin"] == "cuì niǎo kē"


# ----------------------------------------------------------------------------
# Real catalog: data coverage and index performance
# ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real() -> BirdCatalog:
    return BirdCatalog()


class TestRealCatalog:

    def test_every_species_has_aligned_pinyin(self):
        info = zh_species_info()
        assert len(info) > 11000
        for v in info.values():
            syls = v.pinyin.split()
            assert len(syls) == len([c for c in v.zh if c.strip()]), v
            assert pinyin_compact_variants(v.pinyin), v

    def test_every_family_with_a_chinese_name_gets_pinyin(self, real):
        with_zh = [f for f in real.families if f.family_zh]
        assert len(with_zh) >= 200
        assert all(f.family_pinyin for f in with_zh), [f.family_zh for f in with_zh if not f.family_pinyin]

    def test_char_pinyin_fallback_covers_the_family_suffix(self):
        assert char_pinyin_map(zh_species_info())["科"] == "kē"

    def test_common_kingfisher_by_every_spelling(self, real):
        regions = ("NA", "PAL", "OR")
        for q in ("普通翠鸟", "putongcuiniao", "ptcn", "comkin1", "alcedo atthis", "Common Kingfisher"):
            assert _names(real, q, regions)[0] == "Common Kingfisher", q

    def test_default_region_still_finds_home_birds_by_pinyin(self, real):
        rec = real.lookup("Mallard")
        assert _names(real, pinyin_compact_variants(rec.pinyin)[0], NA)[0] == "Mallard"

    def test_soft_regions_finds_off_region_birds_under_default_na(self, real):
        # Regression: with the default NA selection each of these queries also
        # matched in-region birds (Common-*/White-breasted-* names, "Puerto
        # Rican ..." as a subsequence of "ptcn"), so the old zero-result retry
        # never fired and the real match stayed hidden.
        def soft(q):
            return _names(real, q, NA, limit=30, soft_regions=True)
        assert soft("ptcn")[0] == "Common Kingfisher"
        assert "Common Kingfisher" in soft("普通")
        assert "White-throated Kingfisher" in soft("白胸")
        # Without the flag the geofence is still a wall.
        assert "Common Kingfisher" not in _names(real, "ptcn", NA, limit=30)

    def test_payload_carries_search_fields(self, real):
        d = record_to_dict(real.lookup("Common Kingfisher"))
        assert d["zh_name"] == "普通翠鸟"
        assert d["zh_name_traditional"] == "普通翠鳥"
        assert d["pinyin"] == "pǔ tōng cuì niǎo"
        assert d["ebird_code"] == "comkin1"

    def test_timeline_index_accepts_pinyin(self, real):
        idx = QueryIndex(real, wildlife_labels=[])
        parsed = parse_query("putongcuiniao", idx)
        assert [m.label for m in parsed.matches] == ["Common Kingfisher"]


class TestPerformance:
    """万级数据毫秒级响应: the index must never degrade into a catalog scan."""

    def test_index_build_and_query_latency(self, real):
        t0 = time.perf_counter()
        real._name_index()
        build_ms = (time.perf_counter() - t0) * 1000
        assert build_ms < 5000, f"index build took {build_ms:.0f} ms"

        recs = real.records[::60]
        queries = []
        for r in recs:
            en = r.canonical_common_name.lower()
            queries += [en[:4], en.split()[-1][:5], r.zh_name[:2],
                        pinyin_compact_variants(r.pinyin)[0][:6], r.pinyin.split()[0]]
        times = []
        for q in queries:
            t = time.perf_counter()
            real.search(q, ("NA", "PAL", "OR", "AU"), limit=20)
            times.append((time.perf_counter() - t) * 1000)
        times.sort()
        median = statistics.median(times)
        p95 = times[int(len(times) * 0.95)]
        # The pre-index implementation scanned every row per keystroke (~30-50 ms
        # on this catalog). Leave CI plenty of slack while still catching a
        # regression back to a linear scan.
        assert median < 5.0, f"median {median:.1f} ms"
        assert p95 < 40.0, f"p95 {p95:.1f} ms"
