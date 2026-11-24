# Bundled bird-catalog data sources

The catalog file ``birds_global.csv`` is built from the following
authoritative sources. Attribution required by license / convention is
listed below.

## IOC World Bird List (v15.1)

Frank Gill, David Donsker & Pamela Rasmussen (Eds). 2025. *IOC World Bird List* (v15.1). doi:10.14344/IOC.ML.15.1. https://www.worldbirdnames.org/

Licensed under [Creative Commons Attribution 3.0 Unported](https://creativecommons.org/licenses/by/3.0/).
Common names, scientific binomials, taxonomic order/family/genus, and breeding-range biogeographic codes are derived from this source.

## IBP-AOS Alpha Codes

Pyle, P. and DeSante, D.F. *Four-letter (English Name) and Six-letter (Scientific Name) Alpha Codes for North American Birds.* The Institute for Bird Populations. https://www.birdpop.org/

Per the 66th AOS Supplement (2025). 4-letter codes are reproduced as
factual abbreviations; full attribution is preserved here in lieu of a
publicly documented license.

## ProjectKestrel additions

* Hand-curated AOS-to-IOC name overrides for model species whose
  preferred English name differs between authorities (see
  ``tools/build_bird_catalog.py``, ``AOS_TO_IOC_OVERRIDES``).
* Region codes add ``NA`` for every legacy model species and a short
  curated list of well-established introductions on top of IOC's
  native breeding ranges (``INTRODUCED_RANGES``).
* Family display names (``family_common``) preserve the existing ``analyzer/models/scispecies_dispname.csv`` mapping where present and fall back to IOC's *Family (English)* otherwise.

## Chinese display names (``birds_global_zh.csv``, ``birds_family_zh.csv``)

These two files are a **display-only overlay**: they map the catalog's
scientific names to Simplified Chinese species and family names used when the
``species_name_language`` setting is ``zh``. Nothing in a user's folder, scene
tags, folder settings or exported XMP depends on them, so they can be rebuilt
without invalidating anything. Both are produced by ``tools/build_zh_names.py``:

* **Species names** — the official Chinese (Simplified) name list published by
the Cornell Lab of Ornithology / eBird for the AviList taxonomy
(https://www.birds.cornell.edu/clementschecklist/ ; eBird species accounts at
https://ebird.org/ ). Names are reproduced as factual name translations with
full attribution here.
* **Family names and the species eBird does not list** (13 extinct taxa marked
  ``†`` in the IOC catalog) — China Bird Report / ChinaBirds bird-name list
(https://www.birdreport.cn/ ), via the ``ioc`` dataset distributed with
[SuperPicky](https://github.com/SuperPicky/SuperPicky) (AGPLv3).
* **Toned pinyin** (``pinyin`` column, currently display-unused) — the
  ``pinyin_toned`` table from the same SuperPicky ``ioc`` dataset.

The overlay is keyed on scientific names so it survives IOC/common-name
revisions; re-run ``python tools/build_zh_names.py`` after refreshing
``birds_global.csv``.
