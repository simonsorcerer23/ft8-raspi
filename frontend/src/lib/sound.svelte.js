// Browser Notification API + audio chimes for QSO milestones.
//
// Listens to /sse/status — when state transitions to IDLE coming from
// QSO_REPORT/CLOSING, that's a QSO complete: chime + browser notification.
// Sound is generated on-the-fly via Web Audio (no asset to ship).

let lastState = null;
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

export function attachDecodeStream(decodeStore) {
  const es = new EventSource('/sse/decodes');
  es.addEventListener('decode', (ev) => {
    try {
      const d = JSON.parse(ev.data);
      decodeStore.push(d);
    } catch {}
  });
  return () => es.close();
}

export function attachStatusStream() {
  const es = new EventSource('/sse/status');
  es.addEventListener('status', (ev) => {
    try {
      const s = JSON.parse(ev.data);
      const prev = lastState; lastState = s.state;
      if (prev && prev !== s.state) {
        // QSO_REPORT -> IDLE (or QSO_LOG -> IDLE) = QSO complete
        if ((prev === 'QSO_REPORT' || prev === 'QSO_LOG') && s.state === 'IDLE') {
          chime(880, 150);
          setTimeout(() => chime(1320, 150), 180);
          notify('QSO complete', `Logged at ${new Date().toLocaleTimeString()}`);
        }
        // -> TX_LOCKED = alarm
        if (s.state === 'TX_LOCKED') {
          chime(330, 600);
          notify('TX gesperrt', s.last_lock_reason ?? '');
        }
      }
    } catch {}
  });
  return () => es.close();
}
