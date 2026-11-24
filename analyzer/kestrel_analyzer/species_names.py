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
from functools import lru_cache

from .config import MODELS_DIR

ZH_NAMES_PATH = MODELS_DIR / "birds" / "birds_global_zh.csv"
FAMILY_ZH_PATH = MODELS_DIR / "birds" / "birds_family_zh.csv"

# Languages the ``species_name_language`` setting may hold. ``en`` keeps the
# catalog's canonical (IOC/eBird) English names; ``zh`` shows the simplified
# Chinese names from the overlay.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "zh")


def normalize_language(value) -> str:
    """Sanitise a stored language preference into ``"en"`` or ``"zh"``."""
    s = str(value or "").strip().lower()
    return s if s in SUPPORTED_LANGUAGES else "en"


def normalize_scientific_name(name: str) -> str:
    """Lowercase and drop the catalog's extinct dagger marks."""
    return " ".join(str(name or "").replace("†", " ").split()).lower()


@lru_cache(maxsize=1)
def zh_name_map() -> dict[str, str]:
    """``{ scientific_name(lower): zh name }``. Empty when the overlay is absent."""
    out: dict[str, str] = {}
    try:
        with open(ZH_NAMES_PATH, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sci = normalize_scientific_name(row.get("scientific_name") or "")
                zh = (row.get("zh_name") or "").strip()
                if sci and zh:
                    out[sci] = zh
    except OSError:
        pass
    return out


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


def zh_name_for_scientific(scientific_name: str) -> str:
    """Chinese name for a scientific binomial, or ``""`` when unmapped."""
    return zh_name_map().get(normalize_scientific_name(scientific_name), "")


def zh_family_for_scientific(family_sci: str) -> str:
    """Chinese family name for a scientific family, or ``""`` when unmapped."""
    return family_zh_map().get(normalize_scientific_name(family_sci), "")
