// Frontend-Gate gegen die "hartcodierter deutscher String ohne t()-Wrapper"-
// Bugklasse. Die anderen i18n-Gates prüfen nur, dass GENUTZTE t()-Keys
// existieren — sie sehen NICHT, wenn ein String gar nicht erst durch t()
// läuft. Genau so blieben "Lade…", "Blacklist ist leer.", title-Attribute
// etc. nach der Migration deutsch (Audit 2026-06-01).
//
// Strategie: DEUTSCHE SIGNALE erkennen (Umlaute ODER kuratierte DE-Wortliste),
// nicht "beliebiger Text". Englische Fachbegriffe (FT8, SWR, dB, NG3K, Reply,
// Refresh, SPLIT, PWR…) matchen damit gar nicht → Allowlist bleibt minimal.
//
// Geprüft wird im Template-Bereich von .svelte (ohne <script>/<style>/Kommentare):
//   - Textknoten zwischen > und < (Interpolationen {…} entfernt)
//   - literale Attribute title= placeholder= aria-label= alt= (NICHT {…}-dynamisch)
//
// Aufruf: node scripts/i18n_check_hardcoded.mjs
import { readdirSync, statSync, readFileSync } from 'fs';
import { join, dirname, relative } from 'path';
import { fileURLToPath } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, '..', 'frontend', 'src');

// Unzweideutig deutsche Wörter (KEINE validen EN-Wörter — sonst False Positives).
const DE_WORDS = [
  'und', 'oder', 'nicht', 'kein', 'keine', 'für', 'von', 'mit', 'aus', 'bei',
  'noch', 'wird', 'sind', 'zeigen', 'leer', 'eingetragen', 'manuell',
  'insgesamt', 'getrackt', 'bänder', 'stunden', 'tage', 'versuche', 'erfolge',
  'grund', 'quelle', 'beobachten', 'zurück', 'weiter', 'speichere', 'speichern',
  'lade', 'lädt', 'laden', 'fehlgeschlagen', 'gesperrt', 'sperre', 'verstellt',
  'geändert', 'antenne', 'rufzeichen', 'erkannt', 'mindestens', 'passwort',
  'bisher', 'empfangsbericht', 'neutrale', 'schließen', 'abschließen', 'löschen',
  'hinzufügen', 'entfernen', 'gesamt', 'aktiv', 'inaktiv', 'verfügbar', 'nötig',
  'zuletzt', 'gehört', 'wähle', 'fertig', 'setzen', 'gesetzt', 'wieder',
];
const UMLAUT = /[äöüÄÖÜß]/;
const WORD_RE = new RegExp(`\\b(${DE_WORDS.join('|')})\\b`, 'i');

// Strings, die wir bewusst so lassen (z.B. Eigennamen/Beispielwerte). Exakt-Match
// gegen den bereinigten Textknoten/Attributwert. Bewusst klein halten!
const ALLOW = new Set([
  // (derzeit leer — alle DE-Strings laufen über t())
]);

function isGerman(s) {
  const clean = s.trim();
  if (!clean || ALLOW.has(clean)) return false;
  return UMLAUT.test(clean) || WORD_RE.test(clean);
}

function stripInterp(s) {
  // {…} (auch leicht verschachtelt) iterativ entfernen.
  let prev;
  do {
    prev = s;
    s = s.replace(/\{[^{}]*\}/g, '');
  } while (s !== prev);
  return s;
}

function walk(d, acc) {
  for (const f of readdirSync(d)) {
    const p = join(d, f);
    if (statSync(p).isDirectory()) {
      if (!/node_modules|dist/.test(p)) walk(p, acc);
    } else if (f.endsWith('.svelte')) {
      acc.push(p);
    }
  }
  return acc;
}

const findings = [];
for (const file of walk(SRC, [])) {
  const raw = readFileSync(file, 'utf8');
  // <script>/<style>-Blöcke + HTML-Kommentare entfernen → nur Template bleibt.
  let tpl = raw
    .replace(/<script[\s\S]*?<\/script>/gi, (m) => '\n'.repeat((m.match(/\n/g) || []).length))
    .replace(/<style[\s\S]*?<\/style>/gi, (m) => '\n'.repeat((m.match(/\n/g) || []).length))
    .replace(/<!--[\s\S]*?-->/g, (m) => '\n'.repeat((m.match(/\n/g) || []).length));

  const rel = relative(join(HERE, '..'), file);
  const lineOf = (idx) => tpl.slice(0, idx).split('\n').length;

  // a) Textknoten zwischen > und <
  for (const m of tpl.matchAll(/>([^<>]+)</g)) {
    const text = stripInterp(m[1]);
    if (isGerman(text)) {
      findings.push(`${rel}:${lineOf(m.index)}  Text: "${text.trim().slice(0, 60)}"`);
    }
  }
  // b) literale Attribute (nur title/placeholder/aria-label/alt, NICHT {…}-dynamisch)
  for (const m of tpl.matchAll(/\b(title|placeholder|aria-label|alt)=("([^"]*)"|'([^']*)')/g)) {
    const val = m[3] ?? m[4] ?? '';
    if (isGerman(val)) {
      findings.push(`${rel}:${lineOf(m.index)}  @${m[1]}: "${val.slice(0, 60)}"`);
    }
  }
}

if (findings.length) {
  console.error('✗ Hartcodiertes Deutsch ohne t()-Wrapper:');
  for (const f of findings) console.error(`    ${f}`);
  console.error('');
  console.error('→ String über t(\'key\') leiten (DE+EN-Eintrag), oder — falls bewusst');
  console.error('  so gewollt (Eigenname/Beispiel) — in ALLOW in diesem Skript aufnehmen.');
  process.exit(1);
}
console.log('✓ Frontend: kein hartcodiertes Deutsch in .svelte-Templates');
