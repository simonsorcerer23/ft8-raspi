// Browser Notification API + audio chimes for QSO milestones.
//
// Der QSO-Ton haengt am Zaehler der geloggten QSOs (qso_total aus
// /api/stats, gefiltert auf den aktiven Operator), nicht an einer
// Zustandsfolge. Bis zum 14.11.2026 galt "QSO_REPORT/QSO_LOG → IDLE" als
// fertiges QSO. Ein geloggtes QSO laeuft aber ueber QSO_GRACE, der Ton kam
// also nie bei Erfolg — dafuer bei jedem Timeout und jedem Skip aus
// QSO_REPORT (Audit F4). Ein Zustandswechsel auf /sse/status ist jetzt nur
// noch der Anlass nachzuzaehlen.
// Sound is generated on-the-fly via Web Audio (no asset to ship).
import { api } from './api.js';
import { t } from './i18n.svelte.js';

let lastState = null;
let lastPartner = null;
// Stand des QSO-Zaehlers und der Operator, zu dem er gehoert. Wechselt der
// Operator, ist der neue Stand nur die neue Basis, kein Anlass fuer einen Ton.
let qsoCall = null;
let qsoCount = null;
let qsoCheckRunning = false;
let qsoCheckTimers = [];

function utcHm() { return new Date().toISOString().slice(11, 16); }

async function checkQsoCount() {
  if (qsoCheckRunning) return;
  qsoCheckRunning = true;
  try {
    const s = await api.stats();
    const n = s?.qso_total;
    if (typeof n !== 'number') return;
    const call = s.callsign ?? null;
    if (qsoCount !== null && call === qsoCall && n > qsoCount) {
      chime(880, 150);
      setTimeout(() => chime(1320, 150), 180);
      notify(t('sound.qso_title'), t('sound.qso_body', { total: n, time: utcHm() }));
    }
    qsoCall = call;
    qsoCount = n;
  } catch { /* Box nicht erreichbar — beim naechsten Anlass wieder */ }
  finally { qsoCheckRunning = false; }
}

function scheduleQsoCheck() {
  // Zweimal nachsehen: kurz nach dem Wechsel und noch einmal spaeter, falls
  // der Log-Eintrag beim ersten Blick noch nicht geschrieben war.
  qsoCheckTimers.forEach(clearTimeout);
  qsoCheckTimers = [setTimeout(checkQsoCount, 1_000), setTimeout(checkQsoCount, 8_000)];
}

let soundEnabled = true;

export function setSoundEnabled(on) { soundEnabled = on; }
export function isSoundEnabled() { return soundEnabled; }

function chime(freqHz = 880, durationMs = 240) {
  if (!soundEnabled) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = freqHz;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + durationMs / 1000);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + durationMs / 1000);
  } catch { /* AudioContext blocked until user gesture — ok */ }
}

function notify(title, body) {
  if (!soundEnabled) return;
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    new Notification(title, { body });
  }
}

export async function requestNotificationPermission() {
  if (!('Notification' in window)) return false;
  if (Notification.permission === 'granted') return true;
  if (Notification.permission === 'denied') return false;
  const p = await Notification.requestPermission();
  return p === 'granted';
}

function _sseUrl(path) {
  // EventSource kann keine Header setzen → Token + Sprache als Query-Param.
  let tok = '', lang = 'de';
  try { tok = localStorage.getItem('ft8_api_token') || ''; } catch { /* ignore */ }
  try { const l = localStorage.getItem('ft8_lang'); if (l === 'de' || l === 'en') lang = l; } catch { /* ignore */ }
  const qs = new URLSearchParams();
  if (tok) qs.set('token', tok);
  qs.set('lang', lang);
  return `${path}?${qs.toString()}`;
}

export function attachDecodeStream(decodeStore) {
  const es = new EventSource(_sseUrl('/sse/decodes'));
  es.addEventListener('decode', (ev) => {
    try {
      const d = JSON.parse(ev.data);
      decodeStore.push(d);
    } catch {}
  });
  return () => es.close();
}

export function attachStatusStream() {
  const es = new EventSource(_sseUrl('/sse/status'));
  checkQsoCount();   // Basis fuer den QSO-Zaehler
  es.addEventListener('status', (ev) => {
    try {
      const s = JSON.parse(ev.data);
      const prev = lastState; lastState = s.state;
      const prevPartner = lastPartner; lastPartner = s.current_qso_call ?? null;
      // Anlass zum Nachzaehlen: Zustand oder Gegenstation hat gewechselt.
      if (prev && (prev !== s.state || prevPartner !== lastPartner)) scheduleQsoCheck();
      if (prev && prev !== s.state) {
        // -> TX_LOCKED = alarm
        if (s.state === 'TX_LOCKED') {
          chime(330, 600);
          notify(t('sound.tx_locked_title'), s.last_lock_reason ?? '');
        }
      }
    } catch {}
  });
  return () => {
    es.close();
    qsoCheckTimers.forEach(clearTimeout);
    qsoCheckTimers = [];
    lastState = null;
    lastPartner = null;
    qsoCall = null;
    qsoCount = null;
  };
}
