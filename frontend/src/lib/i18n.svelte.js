// Minimale i18n-Schicht (Svelte 5 runes, keine externe Dependency).
// Default Deutsch; Englisch wählbar. lang liegt in localStorage (pro Browser).
// t() liest das reaktive `lang` → Komponenten, die t() im Markup nutzen,
// aktualisieren automatisch beim Sprachwechsel.
import { de } from './locales/de.js';
import { en } from './locales/en.js';

const MESSAGES = { de, en };
const STORAGE_KEY = 'ft8_lang';

function _initial() {
  try {
    const l = localStorage.getItem(STORAGE_KEY);
    if (l === 'de' || l === 'en') return l;
  } catch { /* ignore */ }
  return 'de';
}

let lang = $state(_initial());

export function getLang() { return lang; }

export function setLang(l) {
  if (l !== 'de' && l !== 'en') return;
  lang = l;
  try { localStorage.setItem(STORAGE_KEY, l); } catch { /* ignore */ }
}

export function toggleLang() { setLang(lang === 'de' ? 'en' : 'de'); }

/**
 * Übersetzt key in die aktuelle Sprache. Fallback: Deutsch, dann der Key
 * selbst (so bleibt unmigrierter/fehlender Text sichtbar statt leer).
 * params: { name: 'X' } ersetzt {name} im String.
 */
export function t(key, params) {
  const dict = MESSAGES[lang] || MESSAGES.de;
  let s = dict[key];
  if (s == null) s = MESSAGES.de[key];
  if (s == null) return key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      s = s.replaceAll(`{${k}}`, String(v));
    }
  }
  return s;
}
