// Frontend-i18n-Gate gegen die "Übersetzung fehlt / Platzhalter inkonsistent"-
// Bugklasse. Ergänzt den t()-Import-Guard in check_frontend_api.sh.
//
// t(key) liefert bei fehlendem Key still den Key-String zurück (kein Crash),
// und bei inkonsistenten Platzhaltern zeigt eine Sprache rohe {Klammern}.
// Beides ist still → genau die Klasse, die ein Gate fangen muss.
//
// Prüft (fatal):
//   1. Key-Parität DE↔EN.
//   2. Platzhalter-Parität pro Key ({x} in DE == {x} in EN).
//   3. Jeder literale t('key') im Code existiert im Katalog.
// Orphan-Keys (definiert, nirgends literal genutzt) sind nur Warnung
// (dynamische Keys wie t(`statusbar.state.${s}`) sind legitim).
//
// Aufruf: node scripts/i18n_audit_frontend.mjs
import { readFileSync, readdirSync, statSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));
const FE = join(HERE, '..', 'frontend');

const { de } = await import(pathToFileURL(join(FE, 'src/lib/locales/de.js')));
const { en } = await import(pathToFileURL(join(FE, 'src/lib/locales/en.js')));

const dk = new Set(Object.keys(de));
const ek = new Set(Object.keys(en));
const errors = [];
const warnings = [];

// 1. Parität
const onlyDe = [...dk].filter((k) => !ek.has(k));
const onlyEn = [...ek].filter((k) => !dk.has(k));
if (onlyDe.length) errors.push(`Keys nur in DE (EN fehlt): ${onlyDe.join(', ')}`);
if (onlyEn.length) errors.push(`Keys nur in EN (DE fehlt): ${onlyEn.join(', ')}`);

// 2. Platzhalter-Parität
const ph = (s) => new Set([...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]));
const eqSet = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));
for (const k of dk) {
  if (!ek.has(k)) continue;
  const a = ph(de[k]);
  const b = ph(en[k]);
  if (!eqSet(a, b)) {
    errors.push(`Platzhalter-Mismatch ${k}: de=[${[...a]}] en=[${[...b]}]`);
  }
}

// Quelle einsammeln
function walk(d, acc) {
  for (const f of readdirSync(d)) {
    const p = join(d, f);
    const s = statSync(p);
    if (s.isDirectory()) {
      if (!/node_modules|dist/.test(p)) walk(p, acc);
    } else if (/\.(svelte|js)$/.test(f) && !/locales|i18n\.svelte/.test(p)) {
      acc.push(p);
    }
  }
  return acc;
}
const files = walk(join(FE, 'src'), []);
const used = new Set();
for (const f of files) {
  const t = readFileSync(f, 'utf8');
  for (const m of t.matchAll(/[^a-zA-Z0-9_]t\(\s*[`"']([\w.]+)[`"']/g)) {
    const key = m[1];
    used.add(key);
    if (!dk.has(key) || !ek.has(key)) {
      errors.push(`t('${key}') ohne Katalog-Eintrag @ ${f.replace(FE + '/', '')}`);
    }
  }
}

// Orphans (nur Warnung; dynamische Keys ausnehmen)
const DYNAMIC_PREFIXES = ['statusbar.state.', 'oploc.', 'system.', 'solar.', 'wiz.'];
const orphans = [...dk].filter(
  (k) => !used.has(k) && !DYNAMIC_PREFIXES.some((p) => k.startsWith(p))
);
if (orphans.length) warnings.push(`Orphan-Keys (definiert, nicht literal genutzt): ${orphans.join(', ')}`);

for (const w of warnings) console.error(`⚠ ${w}`);
if (errors.length) {
  console.error('✗ Frontend-i18n-Audit: Findings');
  for (const e of errors) console.error(`    ${e}`);
  process.exit(1);
}
console.log(`✓ Frontend-i18n sauber (${dk.size} Keys, DE/EN paritätisch, alle t()-Keys vorhanden)`);
