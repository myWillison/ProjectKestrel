"""Build the Chinese display-name overlay for the bundled bird catalog.

Reads the eBird/AviList Chinese names shipped with the SuperPicky project
(``birdid/data/bird_reference.sqlite`` -> ``BirdCountInfo``) and the
ChinaBirds checklist (``ioc/birdname.db``), then joins them onto our IOC
catalog by scientific name. Two display-only CSVs are written next to
``birds_global.csv``:

  * ``birds_global_zh.csv``   scientific_name -> simplified Chinese name
  * ``birds_family_zh.csv``   family_sci      -> Chinese family name

The analysis pipeline never reads these: they only translate what the UI
paints when ``species_name_language`` is ``zh`` (see
``kestrel_analyzer/species_names.py``). Canonical English names remain the
storage format in the folder CSV, scene tags, and XMP sidecars, so the
overlay is safe to regenerate independently of a catalog rebuild.

SuperPicky's data files are not vendored here, so ``--superpicky`` must
point at a checkout of https://github.com/<owner>/SuperPicky (AGPLv3) that
contains ``birdid/data/bird_reference.sqlite`` and ``ioc/birdname.db``.

The CSVs are committed, so this script only needs to run when the catalog
or the upstream reference data changes.

Usage:
    python tools/build_zh_names.py --superpicky /path/to/SuperPicky
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = REPO_ROOT / "analyzer" / "models"
CATALOG_PATH = MODELS_DIR / "birds" / "birds_global.csv"
OUT_SPECIES_PATH = MODELS_DIR / "birds" / "birds_global_zh.csv"
OUT_FAMILY_PATH = MODELS_DIR / "birds" / "birds_family_zh.csv"


def _norm_sci(value: str) -> str:
    """Lowercase a scientific name and drop the catalog's extinct dagger marks."""
    return " ".join(str(value or "").replace("†", " ").split()).lower()


def _load_ebird_names(db_path: Path) -> dict[str, dict]:
    """{ scientific_name(lower): {zh, zh_trad, ebird_code} } from BirdCountInfo."""
    out: dict[str, dict] = {}
    with sqlite3.connect(db_path) as con:
        for sci, zh, zh_trad, code in con.execute(
                "SELECT scientific_name, chinese_simplified, chinese_traditional, ebird_code "
                "FROM BirdCountInfo"):
            key = _norm_sci(sci)
            zh = (zh or "").strip()
            if not key or not zh:
                continue
            out.setdefault(key, {
                "zh": zh,
                "zh_trad": (zh_trad or "").strip(),
                "ebird_code": (code or "").strip(),
            })
    return out


def _load_avilist_sci(db_path: Path) -> dict[str, str]:
    """{ model scientific_name(lower): avilist scientific_name } for cross-taxonomy joins."""
    out: dict[str, str] = {}
    with sqlite3.connect(db_path) as con:
        for sci_model, sci_avilist in con.execute(
                "SELECT scientific_name_model, scientific_name_avilist FROM avilist_map"):
            a = _norm_sci(sci_model)
            b = _norm_sci(sci_avilist)
            if a and b:
                out.setdefault(a, b)
    return out


def _load_chinabirds(db_path: Path) -> dict[str, dict]:
    """{ latin_name(lower): {zh, zh_trad, pinyin_name, family_zh} } from the checklist.

    Newer checklist versions win: rows are iterated oldest-to-newest with
    ``setdefault`` so the latest dump's names survive.
    """
    out: dict[str, dict] = {}
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT latin_name, chinese_name, traditional_name, pinyin_name, family_zh "
            "FROM birds ORDER BY version_id ASC"
        )
        for latin, zh, zh_trad, pinyin, family_zh in rows:
            key = _norm_sci(latin)
            zh = (zh or "").strip()
            if not key or not zh:
                continue
            out[key] = {
                "zh": zh,
                "zh_trad": (zh_trad or "").strip(),
                "pinyin": (pinyin or "").strip(),
                "family_zh": (family_zh or "").strip(),
            }
    return out


def _load_pinyin(path: Path) -> dict[str, str]:
    """{ zh name: toned pinyin } — the checklist's own pinyin field is untoned."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k).strip(): str(v).strip() for k, v in data.items() if k and v}
    except (OSError, ValueError):
        return {}


def _read_catalog() -> list[dict]:
    with open(CATALOG_PATH, encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if (row.get("canonical_common_name") or "").strip()]


def build(superpicky_dir: Path) -> int:
    ref_db = superpicky_dir / "birdid" / "data" / "bird_reference.sqlite"
    ioc_db = superpicky_dir / "ioc" / "birdname.db"
    pinyin_json = superpicky_dir / "ioc" / "pinyin_toned.json"
    for p in (ref_db, ioc_db):
        if not p.exists():
            print(f"error: missing SuperPicky data file: {p}", file=sys.stderr)
            return 1

    ebird = _load_ebird_names(ref_db)
    avilist = _load_avilist_sci(ref_db)
    china = _load_chinabirds(ioc_db)
    pinyin = _load_pinyin(pinyin_json)
    print(f"references: {len(ebird)} eBird names, {len(china)} ChinaBirds rows, "
          f"{len(avilist)} avilist links, {len(pinyin)} pinyin entries")

    catalog = _read_catalog()
    species_rows: list[dict] = []
    family_votes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    family_common_by_sci: dict[str, str] = {}
    stats = collections.Counter()

    for row in catalog:
        sci_raw = (row.get("scientific_name") or "").strip()
        sci = _norm_sci(sci_raw)
        family_sci = (row.get("family_sci") or "").strip()
        if family_sci:
            family_common_by_sci.setdefault(family_sci, (row.get("family_common") or "").strip())

        hit = ebird.get(sci)
        source = ""
        if hit:
            source = "ebird"
        else:
            linked = avilist.get(sci)
            if linked and linked in ebird:
                hit = ebird[linked]
                source = "ebird"
        # Family Chinese names only exist in the ChinaBirds checklist, so
        # vote on every species the checklist knows regardless of which
        # source supplied the species-level name.
        china_hit = china.get(sci) or china.get(avilist.get(sci, ""))
        if family_sci and china_hit and china_hit.get("family_zh"):
            family_votes[family_sci][china_hit["family_zh"]] += 1

        if hit:
            stats["ebird"] += 1
        else:
            hit = china.get(sci)
            if hit:
                source = "chinabirds"
                stats["chinabirds"] += 1
            else:
                stats["miss"] += 1
                continue

        zh = hit["zh"]
        species_rows.append({
            "scientific_name": sci_raw,
            "english_name": (row.get("canonical_common_name") or "").strip(),
            "zh_name": zh,
            "zh_name_traditional": hit.get("zh_trad", ""),
            "pinyin": pinyin.get(zh, hit.get("pinyin", "")),
            "ebird_code": hit.get("ebird_code", ""),
            "source": source,
        })

    family_rows = []
    for family_sci, votes in sorted(family_votes.items()):
        family_rows.append({
            "family_sci": family_sci,
            "family_common": family_common_by_sci.get(family_sci, ""),
            "family_zh": votes.most_common(1)[0][0],
        })

    species_rows.sort(key=lambda r: r["scientific_name"].lower())
    OUT_SPECIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_SPECIES_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["scientific_name", "english_name", "zh_name",
                                          "zh_name_traditional", "pinyin", "ebird_code", "source"])
        w.writeheader()
        w.writerows(species_rows)
    with open(OUT_FAMILY_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["family_sci", "family_common", "family_zh"])
        w.writeheader()
        w.writerows(family_rows)

    total = len(catalog)
    covered = total - stats["miss"]
    print(f"wrote {OUT_SPECIES_PATH.relative_to(REPO_ROOT)}: {covered}/{total} species "
          f"({covered / total:.2%}) — eBird {stats['ebird']}, ChinaBirds {stats['chinabirds']}")
    print(f"wrote {OUT_FAMILY_PATH.relative_to(REPO_ROOT)}: {len(family_rows)} families")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--superpicky", type=Path, required=True,
                    help="SuperPicky checkout providing the Chinese name data "
                         "(birdid/data/bird_reference.sqlite, ioc/birdname.db)")
    args = ap.parse_args()
    return build(args.superpicky)


if __name__ == "__main__":
    raise SystemExit(main())
