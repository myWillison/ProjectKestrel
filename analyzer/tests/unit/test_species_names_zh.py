"""Chinese display names for the bundled bird catalog (``species_name_language``).

The overlay is display-only: ``analyzer/models/birds/birds_global_zh.csv`` maps
each catalog scientific name to the eBird/AviList Chinese name, and
``birds_family_zh.csv`` maps scientific families to Chinese family names. These
tests lock in the three things the rest of the app relies on:

  * the shipped data covers the catalog (so no pill falls back unexpectedly);
  * ranked search and the smart-search parser understand Chinese names;
  * the records survive the JS bridge with their Chinese names attached.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from kestrel_analyzer import query_parser as qp
from kestrel_analyzer.bird_catalog import (
    BirdCatalog,
    BirdRecord,
    FamilyEntry,
    family_entry_to_dict,
    record_to_dict,
    score_family,
    score_record,
    _SCORE_COMMON_EXACT,
    _SCORE_SUBSTRING,
    _SCORE_ZH_EXACT,
    _SCORE_ZH_SUBSTRING,
)
from kestrel_analyzer.species_names import (
    FAMILY_ZH_PATH,
    SUPPORTED_LANGUAGES,
    ZH_NAMES_PATH,
    family_zh_map,
    normalize_language,
    normalize_scientific_name,
    zh_family_for_scientific,
    zh_name_for_scientific,
    zh_name_map,
)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def catalog() -> BirdCatalog:
    return BirdCatalog()


@pytest.fixture(scope="module")
def index():
    return qp.shared_index()


# ----------------------------------------------------------------------------
# Shipped overlay data
# ----------------------------------------------------------------------------

class TestOverlayData:

    def test_files_are_shipped(self):
        assert ZH_NAMES_PATH.is_file(), f"missing {ZH_NAMES_PATH}"
        assert FAMILY_ZH_PATH.is_file(), f"missing {FAMILY_ZH_PATH}"
        assert zh_name_map() and family_zh_map()

    def test_every_catalog_species_has_a_chinese_name(self, catalog):
        # 100% coverage is the contract: a species without a zh name would
        # silently fall back to English in the middle of a Chinese UI.
        missing = [r.canonical_common_name for r in catalog.records if not r.zh_name]
        assert not missing, f"{len(missing)} species without a Chinese name, e.g. {missing[:5]}"

    def test_family_coverage(self, catalog):
        # Family names come from the ChinaBirds checklist, which doesn't name
        # the purely Neotropical/Australasian families; those fall back to
        # English. Just guard against a large regression.
        entries = catalog.families
        assert entries
        mapped = sum(1 for e in entries if e.family_zh)
        assert mapped / len(entries) > 0.75

    def test_known_species_and_families(self):
        assert zh_name_for_scientific("Aix galericulata") == "鸳鸯"
        assert zh_name_for_scientific("Passer domesticus") == "家麻雀"
        assert zh_family_for_scientific("Accipitridae") == "鹰科"

    def test_dagger_and_case_are_folded(self):
        assert normalize_scientific_name("†Turdus migratorius") == "turdus migratorius"
        assert zh_name_for_scientific("Aix Galericulata") == "鸳鸯"

    def test_unknown_names_fall_back_to_empty(self):
        assert zh_name_for_scientific("Nonexistent birdus") == ""
        assert zh_family_for_scientific("Nonexistentidae") == ""

    def test_supported_languages(self):
        assert SUPPORTED_LANGUAGES == ("en", "zh")
        for raw, expected in [
            ("zh", "zh"), ("ZH", "zh"), (" zh ", "zh"),
            ("en", "en"), ("fr", "en"), ("", "en"), (None, "en"),
        ]:
            assert normalize_language(raw) == expected


# ----------------------------------------------------------------------------
# Ranked search (combobox: search_birds / search_families)
# ----------------------------------------------------------------------------

class TestRankedSearch:

    def test_chinese_exact_match_finds_the_species(self, catalog):
        hits = catalog.search("家麻雀", ["NA"], limit=3)
        assert hits and hits[0].canonical_common_name == "House Sparrow"

    def test_chinese_partial_names_are_searchable(self, catalog):
        # "鹭" only ever appears inside Chinese names -> substring tier.
        names = [r.canonical_common_name for r in catalog.search("大白鹭", ["NA"], limit=3)]
        assert "Great Egret" in names

    def test_chinese_family_search(self, catalog):
        fams = [f.family_common for f in catalog.search_families("鸭科", ["NA"], limit=5)]
        assert "Waterfowl sp." in fams

    def test_zh_exact_outranks_english_exact(self):
        """中文优先: a typed Chinese name is the strongest name signal."""
        rec = BirdRecord(
            canonical_common_name="House Sparrow", scientific_name="Passer domesticus",
            family_sci="Passeridae", family_common="Old World Sparrow sp.", order="Passeriformes",
            regions=frozenset({"NA"}), alpha_4="HOSF", aliases=(), is_model_species=True,
            zh_name="家麻雀",
        )
        assert score_record("家麻雀", rec) == _SCORE_ZH_EXACT
        assert score_record("house sparrow", rec) == _SCORE_COMMON_EXACT
        assert score_record("家麻雀", rec) > score_record("house sparrow", rec)
        # ...and an exact Chinese hit beats every loose English tier, while a
        # loose Chinese hit ("麻雀") still beats an English substring.
        assert score_record("家麻雀", rec) > score_record("house", rec) > 0
        assert score_record("麻雀", rec) == _SCORE_ZH_SUBSTRING > _SCORE_SUBSTRING

    def test_zh_family_scoring(self):
        entry = FamilyEntry(
            family_common="Hawk/Eagle/Kite sp.", family_sci="Accipitridae",
            order="Accipitriformes", regions=frozenset({"NA"}), family_zh="鹰科",
        )
        assert score_family("鹰科", entry) == _SCORE_ZH_EXACT
        assert score_family("hawk", entry) > 0


# ----------------------------------------------------------------------------
# Smart-search / tag parsing
# ----------------------------------------------------------------------------

class TestQueryParsing:

    @pytest.mark.parametrize("q,expected", [
        ("家麻雀", ("species", "House Sparrow")),
        ("大白鹭", ("species", "Great Egret")),
        ("鸳鸯", ("species", "Mandarin Duck")),      # two-character name
        ("白鹭", ("species", "Little Egret")),        # two-character name
        ("鹰科", ("family", "Hawk/Eagle/Kite sp.")),
    ])
    def test_chinese_terms_resolve_to_english_labels(self, index, q, expected):
        parsed = qp.parse_query(q, index=index, regions=["NA"])
        assert [(m.kind, m.label) for m in parsed.matches] == [expected]
        assert parsed.remainder == ""

    def test_stray_space_between_ideographs_is_folded(self, index):
        parsed = qp.parse_query("大白 鹭", index=index, regions=["NA"])
        assert [(m.kind, m.label) for m in parsed.matches] == [("species", "Great Egret")]

    def test_mixed_query_leaves_the_visual_text_in_the_remainder(self, index):
        parsed = qp.parse_query("大白鹭 在飞", index=index, regions=["NA"])
        assert [(m.kind, m.label) for m in parsed.matches] == [("species", "Great Egret")]
        assert parsed.remainder == "在飞"

    def test_short_english_tokens_stay_ignored(self, index):
        # The two-character floor only relaxes for CJK, so junk like "ed"
        # still can't turn into a filter.
        assert qp.parse_query("ed", index=index, regions=["NA"]).matches == []

    def test_all_chinese_query_skips_the_english_only_text_encoder(self, index):
        # SigLIP's text tower is English-only; with the chip already narrowing
        # the candidates, semantics are dropped so results sort by quality.
        parsed = qp.parse_query("大白鹭 在飞", index=index, regions=["NA"])
        assert qp.semantic_text(parsed) == ""

    def test_english_query_keeps_its_semantic_text(self, index):
        parsed = qp.parse_query("Great Egret flying", index=index, regions=["NA"])
        assert qp.semantic_text(parsed) == "Great Egret flying"

    def test_mixed_query_sends_only_the_english_remainder(self, index):
        # "大白鹭 in flight": the chip filters to Great Egret, and SigLIP gets
        # "in flight" -- never the Chinese name it cannot encode.
        parsed = qp.parse_query("大白鹭 in flight", index=index, regions=["NA"])
        assert [(m.kind, m.label) for m in parsed.matches] == [("species", "Great Egret")]
        assert qp.semantic_text(parsed) == "in flight"

    def test_unmatched_chinese_phrase_skips_the_text_encoder(self, index):
        # No chip and nothing but ideographs left: nothing to embed.
        parsed = qp.parse_query("在飞的鸟", index=index, regions=["NA"])
        assert parsed.matches == []
        assert qp.semantic_text(parsed) == ""

    def test_chinese_query_is_a_pure_filter(self, index):
        route = qp.classify_query("家麻雀", index=index)
        assert route.kind == "filter"
        assert route.labels == ("House Sparrow",)


# ----------------------------------------------------------------------------
# Bridge serialisation
# ----------------------------------------------------------------------------

class TestSerialisation:

    def test_record_payload_carries_the_chinese_name(self, catalog):
        rec = catalog.lookup("House Sparrow")
        assert rec is not None
        payload = record_to_dict(rec)
        assert payload["zh_name"] == "家麻雀"
        assert payload["canonical_common_name"] == "House Sparrow"

    def test_family_payload_carries_the_chinese_name(self, catalog):
        entry = next(e for e in catalog.families if e.family_common == "Hawk/Eagle/Kite sp.")
        payload = family_entry_to_dict(entry)
        assert payload["family_zh"] == "鹰科"
        assert payload["family_common"] == "Hawk/Eagle/Kite sp."
