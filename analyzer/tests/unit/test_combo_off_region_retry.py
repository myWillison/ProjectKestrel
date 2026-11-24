"""search_birds / search_families: Bird Regions as a soft geofence.

The tag combobox is region-geofenced (Bird Regions, default NA) so the dropdown
shows birds the user can realistically see. A hard geofence made an
out-of-region species untaggable even when the user typed its exact name --
普通翠鸟 / Common Kingfisher is PAL-OR-AU, so with NA selected the dropdown came
back empty. A zero-result-only retry was not enough either: "ptcn" matched the
NA species "Puer**t**o Ri**c**a**n** ..." as a weak subsequence, and "普通" /
"白胸" matched local Common-*/White-breasted-* birds, so the retry never fired
while the real match stayed hidden.

The bridge now searches with ``soft_regions=True``: off-region rows that match
at least as well as the best in-region hit are merged in (better matches first,
ties after the in-region rows) and flagged so the UI can say why the bird
showed up. Weak off-region matches stay hidden and the empty query is still
strictly region-scoped.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import api_bridge
from kestrel_analyzer.bird_catalog import BirdCatalog, BirdRecord

pytestmark = pytest.mark.unit


def _rec(common, sci, fam_common, fam_sci, regions, zh, alpha, model=False, pinyin=""):
    return BirdRecord(
        canonical_common_name=common,
        scientific_name=sci,
        family_sci=fam_sci,
        family_common=fam_common,
        order="Coraciiformes",
        regions=frozenset(regions),
        alpha_4=alpha,
        aliases=(),
        is_model_species=model,
        zh_name=zh,
        pinyin=pinyin,
    )


@pytest.fixture
def api():
    """Bridge instance pinned to a tiny catalog (no 11k-row load)."""
    bridge = api_bridge.Api()
    bridge._bird_catalog_cache = BirdCatalog([
        _rec("Belted Kingfisher", "Megaceryle alcyon", "Kingfisher sp.", "Alcedinidae",
             {"NA"}, "带鱼狗", "BEKI", model=True),
        _rec("Common Kingfisher", "Alcedo atthis", "Kingfisher sp.", "Alcedinidae",
             {"PAL", "OR"}, "普通翠鸟", "COMK", pinyin="pǔ tōng cuì niǎo"),
        # A family that never occurs in the Nearctic, for the family retry.
        _rec("Superb Bird-of-paradise", "Lophorina superba", "Birds-of-paradise sp.",
             "Paradisaeidae", {"AU"}, "", "SBOP"),
        # An in-region bird that "ptcn" only matches as a scattered subsequence
        # (Puer-t-o Ri-c-a-n- ...) -- a weak local hit must not hide the real one.
        _rec("Puerto Rican Owl", "Gymnasio nudiceps", "Owl sp.", "Strigidae",
             {"NA"}, "波多黎各鸮", "PROW"),
    ])
    return bridge


class TestSpeciesComboRetry:

    def test_chinese_exact_name_survives_the_geofence(self, api):
        res = api.search_birds("普通翠鸟", ["NA"])
        assert res["success"] is True
        assert [r["canonical_common_name"] for r in res["results"]] == ["Common Kingfisher"]
        assert res["results"][0]["off_region"] is True

    def test_english_exact_name_survives_the_geofence(self, api):
        # Same gap on the English side -- it was never Chinese-specific.
        res = api.search_birds("common kingfisher", ["NA"])
        assert [r["canonical_common_name"] for r in res["results"]] == ["Common Kingfisher"]
        assert res["results"][0]["off_region"] is True

    def test_in_region_hits_are_not_flagged(self, api):
        res = api.search_birds("belted kingfisher", ["NA"])
        assert [r["canonical_common_name"] for r in res["results"]][:1] == ["Belted Kingfisher"]
        assert "off_region" not in res["results"][0]

    def test_tied_off_region_hit_follows_the_in_region_rows(self, api):
        # "kingfisher" is a token-prefix for both: the local bird keeps its
        # rank, the off-region one is appended with the badge instead of hidden.
        res = api.search_birds("kingfisher", ["NA"])
        names = [r["canonical_common_name"] for r in res["results"]]
        assert names == ["Belted Kingfisher", "Common Kingfisher"]
        assert "off_region" not in res["results"][0]
        assert res["results"][1]["off_region"] is True

    def test_better_off_region_hit_outranks_weak_local_subsequence(self, api):
        # "ptcn" = pinyin initials of 普通翠鸟 (940) vs a scattered subsequence of
        # "Puerto Rican ..." (480): the real match leads, the weak one follows.
        res = api.search_birds("ptcn", ["NA"])
        names = [r["canonical_common_name"] for r in res["results"]]
        assert names == ["Common Kingfisher", "Puerto Rican Owl"]
        assert res["results"][0]["off_region"] is True
        assert "off_region" not in res["results"][1]

    def test_weaker_off_region_hit_stays_hidden(self, api):
        # An in-region prefix hit beats an off-region subsequence: the geofence
        # is a preference, not switched off.
        res = api.search_birds("belted", ["NA"])
        names = [r["canonical_common_name"] for r in res["results"]]
        assert names == ["Belted Kingfisher"]
        assert all("off_region" not in r for r in res["results"])

    def test_unknown_name_stays_empty(self, api):
        res = api.search_birds("not a bird at all", ["NA"])
        assert res["success"] is True
        assert res["results"] == []

    def test_empty_query_stays_region_scoped(self, api):
        # The dropdown seeds itself with the region's alphabetical head; that
        # must not start listing the whole world.
        res = api.search_birds("", ["NA"])
        names = [r["canonical_common_name"] for r in res["results"]]
        assert names == ["Belted Kingfisher", "Puerto Rican Owl"]
        assert all("off_region" not in r for r in res["results"])


class TestFamilyComboRetry:

    def test_family_outside_the_region_is_found_by_name(self, api):
        res = api.search_families("birds-of-paradise", ["NA"])
        assert res["success"] is True
        assert [r["family_common"] for r in res["results"]] == ["Birds-of-paradise sp."]
        assert all(r["off_region"] is True for r in res["results"])

    def test_family_in_region_is_not_flagged(self, api):
        res = api.search_families("kingfisher", ["NA"])
        assert [r["family_common"] for r in res["results"]] == ["Kingfisher sp."]
        assert "off_region" not in res["results"][0]

    def test_family_empty_query_stays_region_scoped(self, api):
        # Only the two Nearctic families (Kingfisher, Owl) -- not Birds-of-paradise.
        res = api.search_families("", ["NA"])
        assert [r["family_common"] for r in res["results"]] == ["Kingfisher sp.", "Owl sp."]
        assert all("off_region" not in r for r in res["results"])


class TestZhMapPinyin:

    def test_map_carries_pinyin_for_tooltips(self, api):
        # The tag tooltip reads the same overlay payload; pinyin must ride along
        # with the Chinese names (species and families alike).
        res = api.get_species_zh_map()
        assert res["success"] is True
        assert res["species"]["Common Kingfisher"] == "普通翠鸟"
        assert res["pinyin"]["Common Kingfisher"] == "pǔ tōng cuì niǎo"
        assert "Belted Kingfisher" not in res["pinyin"]  # record has no pinyin
        assert res["family_pinyin"]["Kingfisher sp."] == "cuì niǎo kē"

    def test_map_is_cached_on_the_bridge(self, api):
        assert api.get_species_zh_map() is api.get_species_zh_map()
