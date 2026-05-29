// v0.23.0 — zentrale UTC-Zeit-Helfer.
//
// Funkbetrieb läuft IMMER in UTC — Logs, Decodes, QSO-Zeiten, alles.
// Vorher war's ein Chaos: manche Backend-Endpoints schickten naive
// datetimes ohne Suffix ("2026-05-29T06:39:44"), andere mit +00:00.
// Im Frontend nutzten manche Komponenten getUTC* (korrekt), andere
// toLocaleTimeString (= zeigte CEST, +2h). Resultat: jede Ansicht
// zeigte eine andere falsche Zeit.
//
// Diese Util macht beides robust:
//   1. parseUtc() hängt defensiv 'Z' an wenn das Backend keinen
//      Timezone-Suffix mitschickt (naive datetime = per Konvention UTC).
//      Hat der String schon Z oder +HH:MM, wird er unverändert geparst.
//   2. Alle Formatter rendern via getUTC* / toISOString — IMMER UTC,
//      NIE Lokalzeit. Egal in welcher Zeitzone das Handy steht.

/**
 * Parst einen ISO-Timestamp als UTC. Naive Strings (ohne TZ-Suffix)
 * werden als UTC interpretiert — das Backend speichert alle Zeiten in
 * UTC, gibt sie aber teils ohne Suffix raus.
 * @returns {Date|null}
 */
export function parseUtc(iso) {
  if (!iso) return null;
  const hasTz = /([Zz]|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasTz ? iso : iso + 'Z');
  return isNaN(d.getTime()) ? null : d;
}

/** HH:MM:SS in UTC. */
export function fmtUtcTime(iso) {
  const d = parseUtc(iso);
  if (!d) return '—';
  return d.toISOString().slice(11, 19);
}

/** HH:MM in UTC. */
export function fmtUtcHm(iso) {
  const d = parseUtc(iso);
  if (!d) return '—';
  return d.toISOString().slice(11, 16);
}

/** YYYY-MM-DD HH:MM UTC. */
export function fmtUtcDateTime(iso) {
  const d = parseUtc(iso);
  if (!d) return '—';
  return d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
}

/** YYYY-MM-DD (UTC). */
export function fmtUtcDate(iso) {
  const d = parseUtc(iso);
  if (!d) return '—';
  return d.toISOString().slice(0, 10);
}

/** Millisekunden seit Epoch — für Diff-/Sortier-Berechnungen.
 *  Nutzt parseUtc damit naive Strings konsistent als UTC zählen. */
export function utcMillis(iso) {
  const d = parseUtc(iso);
  return d ? d.getTime() : NaN;
}
