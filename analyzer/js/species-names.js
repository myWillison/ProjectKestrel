/* Localised species / family names — a display-only overlay.
 *
 * Storage is always canonical English: the folder CSV's ``species`` column,
 * scene tags, folder settings and XMP sidecars keep the catalog's IOC/eBird
 * names, so switching the display language never rewrites a folder or breaks
 * compatibility with the hosted add-ons. When the ``species_name_language``
 * setting is ``zh`` we merely *render* the Chinese names that
 * ``get_species_zh_map`` returns (built from analyzer/models/birds/
 * birds_global_zh.csv, which mirrors eBird's official Chinese name table).
 *
 * Every element that carries a ``data-*`` attribute keeps the canonical
 * English name so the remove/apply handlers keep working no matter which
 * language the pill is drawn in.
 */

let _zhSpeciesLower = null;   // "american robin" -> "旅鸫"
let _zhFamiliesLower = null;  // "thrush sp." -> "鸫科"
let _enByZhSpecies = null;    // "旅鸫" -> "American Robin" (for typed/pasted tags)
let _enByZhFamily = null;     // "鸫科" -> "Thrush sp."
let _zhMapLoaded = false;
let _zhMapPromise = null;

/** True when the user asked for Chinese species/family names. */
function zhSpeciesNamesEnabled() {
  try {
    return String(getSetting('species_name_language', 'en')) === 'zh';
  } catch (_) {
    return false;
  }
}

/** True once the zh overlay map has been fetched at least once. */
function zhMapReady() {
  return _zhMapLoaded;
}

/** Fetch the { species, families } zh overlay from the backend (idempotent).
 *
 *  Called at startup when zh is the selected language, and lazily the first
 *  time a Chinese query is typed so English-mode users pay nothing.
 */
function loadSpeciesZhMap() {
  if (_zhMapLoaded) return Promise.resolve();
  if (_zhMapPromise) return _zhMapPromise;
  if (!hasPywebviewApi || !window.pywebview?.api?.get_species_zh_map) {
    _zhSpeciesLower = _zhSpeciesLower || {};
    _zhFamiliesLower = _zhFamiliesLower || {};
    return Promise.resolve();
  }
  _zhMapPromise = (async () => {
    try {
      const res = await window.pywebview.api.get_species_zh_map();
      const species = (res && res.success && res.species) ? res.species : {};
      const families = (res && res.success && res.families) ? res.families : {};
      _zhSpeciesLower = {};
      _enByZhSpecies = {};
      for (const [en, zh] of Object.entries(species)) {
        if (!zh) continue;
        _zhSpeciesLower[String(en).toLowerCase()] = zh;
        if (!(zh in _enByZhSpecies)) _enByZhSpecies[zh] = en;
      }
      _zhFamiliesLower = {};
      _enByZhFamily = {};
      for (const [en, zh] of Object.entries(families)) {
        if (!zh) continue;
        _zhFamiliesLower[String(en).toLowerCase()] = zh;
        if (!(zh in _enByZhFamily)) _enByZhFamily[zh] = en;
      }
      _zhMapLoaded = true;
      kdebug(`[loadSpeciesZhMap] ${Object.keys(_zhSpeciesLower).length} species, ${Object.keys(_zhFamiliesLower).length} families`);
    } catch (e) {
      console.warn('[loadSpeciesZhMap] failed:', e);
      _zhSpeciesLower = _zhSpeciesLower || {};
      _zhFamiliesLower = _zhFamiliesLower || {};
    } finally {
      _zhMapPromise = null;
    }
  })();
  return _zhMapPromise;
}

/** Ensure the map is on its way; resolves immediately when already loaded. */
function ensureSpeciesZhMap() {
  return _zhMapLoaded ? Promise.resolve() : loadSpeciesZhMap();
}

function _zhLookup(map, name) {
  if (!map || !name) return '';
  return map[String(name).trim().toLowerCase()] || '';
}

/** Chinese name for a canonical English species name ("" when unmapped). */
function _speciesZhName(name) {
  return zhSpeciesNamesEnabled() ? _zhLookup(_zhSpeciesLower, name) : '';
}

/** Chinese name for a family display name, e.g. "Hawk/Eagle/Kite sp." ("" when unmapped). */
function _familyZhName(name) {
  return zhSpeciesNamesEnabled() ? _zhLookup(_zhFamiliesLower, name) : '';
}

/** The label a species pill should show ("American Robin" -> "旅鸫" in zh mode). */
function speciesDisplayName(name) {
  return _speciesZhName(name) || name || '';
}

/** The English name to show beneath a translated species pill ("" in en mode). */
function speciesAltName(name) {
  return _speciesZhName(name) ? (name || '') : '';
}

/** The label a family pill should show ("Thrush sp." -> "鸫科" in zh mode). */
function familyDisplayName(name) {
  return _familyZhName(name) || name || '';
}

/** The English name to show beneath a translated family pill ("" in en mode). */
function familyAltName(name) {
  return _familyZhName(name) ? (name || '') : '';
}

/** Display name for a label whose type isn't known (card pills, Perch chips).
 *  Species win over families because a scene tag can only be one of the two
 *  and the species map is the one users type against. */
function labelDisplayName(name) {
  if (!zhSpeciesNamesEnabled()) return name || '';
  return _speciesZhName(name) || _familyZhName(name) || name || '';
}

/** Does a lowercased query match this species in either language? */
function speciesNameMatches(name, lcQuery) {
  if (!name || !lcQuery) return false;
  if (String(name).toLowerCase().includes(lcQuery)) return true;
  const zh = _zhLookup(_zhSpeciesLower, name);
  return !!zh && zh.includes(lcQuery);
}

/** Does a lowercased query match this family in either language? */
function familyNameMatches(name, lcQuery) {
  if (!name || !lcQuery) return false;
  if (String(name).toLowerCase().includes(lcQuery)) return true;
  const zh = _zhLookup(_zhFamiliesLower, name);
  return !!zh && zh.includes(lcQuery);
}

/** Does a lowercased query match the label in either language? */
function labelNameMatches(name, lcQuery) {
  return speciesNameMatches(name, lcQuery) || familyNameMatches(name, lcQuery);
}

/** True when the string contains CJK ideographs (i.e. a Chinese search). */
function hasCjkText(text) {
  return /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(String(text || ''));
}

/** Canonical English species name for a Chinese name the user typed or pasted.
 *
 *  Keeps storage language-independent: the folder CSV's ``species`` column,
 *  scene tags and XMP sidecars must never depend on ``species_name_language``.
 *  Returns ``""`` when the text isn't a known Chinese species name.
 */
function canonicalSpeciesFromZh(text) {
  const t = String(text || '').trim();
  if (!t || !_enByZhSpecies) return '';
  if (_enByZhSpecies[t]) return _enByZhSpecies[t];
  // Tolerate a trailing "sp."/whitespace difference and the traditional
  // variants the parser folds onto the same entry.
  const stripped = t.replace(/\s*(sp\.?|spp\.?)$/i, '').trim();
  return _enByZhSpecies[stripped] || '';
}

/** Canonical English family display name for a Chinese family name ("" when unknown). */
function canonicalFamilyFromZh(text) {
  const t = String(text || '').trim();
  if (!t || !_enByZhFamily) return '';
  return _enByZhFamily[t] || '';
}
