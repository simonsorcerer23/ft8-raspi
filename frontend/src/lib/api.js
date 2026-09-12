// Thin fetch wrapper around the FastAPI backend.

// v0.37.0 — API-Token-Auth. Token liegt in localStorage (pro Origin), wird
// als Authorization: Bearer mitgeschickt. Bei 401 feuern wir ein Event,
// damit die App den Login-Screen zeigt.
import { getLang } from './i18n.svelte.js';

const TOKEN_KEY = 'ft8_api_token';
export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}
export function setToken(t) {
  try { localStorage.setItem(TOKEN_KEY, (t || '').trim()); } catch { /* ignore */ }
}
export function clearToken() {
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
}
function _requireLogin() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('ft8-auth-required'));
  }
}

async function request(path, { method = 'GET', body, query } = {}) {
  const init = { method, headers: { 'Accept': 'application/json' } };
  const tok = getToken();
  if (tok) init.headers['Authorization'] = `Bearer ${tok}`;
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let url = `/api${path}`;
  // Backend localizes its own strings (lock reasons, hints) per the UI
  // language — pass it on every request. Merged with any explicit query.
  const params = Object.entries(query || {})
    .filter(([_, v]) => v !== null && v !== undefined && v !== '');
  params.push(['lang', getLang()]);
  const qs = new URLSearchParams(params).toString();
  if (qs) url += `${path.includes('?') ? '&' : '?'}${qs}`;
  const r = await fetch(url, init);
  if (r.status === 401) {
    _requireLogin();
    throw new Error('401 unauthorized — Token erforderlich');
  }
  const ct = r.headers.get('content-type') || '';
  const payload = ct.includes('application/json') ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = typeof payload === 'string' ? payload : (payload.detail || JSON.stringify(payload));
    throw new Error(`${r.status} ${r.statusText}: ${msg}`);
  }
  return payload;
}

function _apiUrl(pathOrUrl) {
  let url = pathOrUrl.startsWith('/api') ? pathOrUrl : `/api${pathOrUrl}`;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}lang=${encodeURIComponent(getLang())}`;
}

function _filenameFromDisposition(header) {
  if (!header) return null;
  const utf = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf) return decodeURIComponent(utf[1].replace(/"/g, ''));
  const ascii = header.match(/filename="?([^";]+)"?/i);
  return ascii ? ascii[1] : null;
}

async function download(pathOrUrl, filename) {
  const init = { method: 'GET', headers: { 'Accept': 'text/plain, */*' } };
  const tok = getToken();
  if (tok) init.headers['Authorization'] = `Bearer ${tok}`;
  const r = await fetch(_apiUrl(pathOrUrl), init);
  if (r.status === 401) {
    _requireLogin();
    throw new Error('401 unauthorized — Token erforderlich');
  }
  if (!r.ok) {
    const ct = r.headers.get('content-type') || '';
    const payload = ct.includes('application/json') ? await r.json() : await r.text();
    const msg = typeof payload === 'string' ? payload : (payload.detail || JSON.stringify(payload));
    throw new Error(`${r.status} ${r.statusText}: ${msg}`);
  }
  const blob = await r.blob();
  const name = filename
            || _filenameFromDisposition(r.headers.get('content-disposition'))
            || 'download.adif';
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 30_000);
  return { filename: name, size: blob.size };
}

async function blobUrl(pathOrUrl) {
  // Fuer Bilder, die hinter der Token-Pruefung liegen. Ein <img src=...>
  // kann keinen Authorization-Header setzen und bekaeme 401; Leaflets
  // ImageOverlay benutzt aber genau so ein Element. Also holen wir die
  // Daten selbst und reichen eine blob:-Adresse weiter.
  // Der Aufrufer MUSS sie mit URL.revokeObjectURL wieder freigeben.
  const init = { method: 'GET', headers: {} };
  const tok = getToken();
  if (tok) init.headers['Authorization'] = `Bearer ${tok}`;
  const r = await fetch(_apiUrl(pathOrUrl), init);
  if (r.status === 401) { _requireLogin(); throw new Error('401 unauthorized'); }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return URL.createObjectURL(await r.blob());
}

function adifPath(operator) {
  return operator
    ? `/log/adif?operator=${encodeURIComponent(operator)}`
    : '/log/adif';
}

export const api = {
  status:       () => request('/status'),
  healthcheck:  () => request('/healthcheck'),
  startCq:      () => request('/control/cq',         { method: 'POST' }),
  stop:         () => request('/control/stop',       { method: 'POST' }),
  panic:        () => request('/control/panic',      { method: 'POST' }),
  shutdown:     () => request('/control/shutdown',   { method: 'POST' }),
  reboot:       () => request('/control/reboot',     { method: 'POST' }),
  setHuntFilter:(opts) => request('/control/hunt-filter', { method: 'POST', body: opts }),
  resetLock:    () => request('/control/reset-lock', { method: 'POST' }),
  restoreRig:   () => request('/control/restore-rig', { method: 'POST' }),
  setAutoAnswer:(enabled) => request('/control/auto-answer', {
                              method: 'POST', body: { enabled }
                            }),
  skipQso:      ()       => request('/control/skip', { method: 'POST' }),
  reply:        (decode) => request('/control/reply', {
                              method: 'POST',
                              body: {
                                call_from:      decode.call_from,
                                call_to:        decode.call_to ?? null,
                                grid:           decode.grid ?? null,
                                message:        decode.message,
                                snr_db:         decode.snr_db ?? null,
                                dt_s:           decode.dt_s ?? null,
                                freq_offset_hz: decode.freq_offset_hz ?? null,
                                band:           decode.band ?? '20m',
                              },
                            }),
  tailEnd:      (decode) => request('/control/tail-end', {
                              method: 'POST',
                              body: {
                                call_from:      decode.call_from,
                                call_to:        decode.call_to ?? null,
                                grid:           decode.grid ?? null,
                                message:        decode.message,
                                snr_db:         decode.snr_db ?? null,
                                dt_s:           decode.dt_s ?? null,
                                freq_offset_hz: decode.freq_offset_hz ?? null,
                                band:           decode.band ?? '20m',
                              },
                            }),
  setTxPower:   (watts)  => request('/control/tx-power', { method: 'POST', body: { watts } }),
  setCqDirected: (value) => request('/control/cq-directed', { method: 'POST', body: { value } }),
  setAntenna:   (name)   => request('/control/antenna', { method: 'POST', body: { name } }),
  blacklist:    ()       => request('/blacklist'),
  blacklistAdd: (call, reason) => request('/control/blacklist', {
                              method: 'POST', body: { call, reason }
                            }),
  blacklistRemove: (call) => request(`/control/blacklist/${encodeURIComponent(call)}`, {
                              method: 'DELETE'
                            }),
  watchlist:    ()       => request('/watchlist'),
  watchlistAdd: (call, note) => request('/control/watchlist', {
                              method: 'POST', body: { call, note }
                            }),
  watchlistRemove: (call) => request(`/control/watchlist/${encodeURIComponent(call)}`, {
                              method: 'DELETE'
                            }),
  reputation:   ()       => request('/reputation'),
  reputationReset: (call) => request(`/control/reputation/${encodeURIComponent(call)}`, {
                              method: 'DELETE'
                            }),
  dxpedition:   ()       => request('/dxpedition-schedule'),
  dxpeditionAdd: (call, start_date, end_date, note) => request('/control/dxpedition-schedule', {
                              method: 'POST',
                              body: { call, start_date, end_date, note }
                            }),
  dxpeditionRemove: (call) => request(`/control/dxpedition-schedule/${encodeURIComponent(call)}`, {
                              method: 'DELETE'
                            }),
  log:          (opts)   => request('/log',     { query: opts }),
  heard:        (opts)   => request('/heard',   { query: opts }),
  decodes:      (opts)   => request('/decodes', { query: opts }),
  map:          (opts)   => request('/map',     { query: opts }),
  config:       ()       => request('/config'),
  saveConfig:   (yaml_text) => request('/config', { method: 'PUT', body: { yaml_text } }),
  detectRig:    ()       => request('/rig/detect'),

  // WLAN management
  wifiOverview:    ()       => request('/network/wifi'),
  wifiConnections: ()       => request('/network/wifi/connections'),
  wifiScan:        ()       => request('/network/wifi/scan'),
  wifiAdd:         (body)   => request('/network/wifi/connections', { method: 'POST', body }),
  wifiDelete:      (name)   => request(`/network/wifi/connections/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  wifiSetPriority: (name, priority) => request(`/network/wifi/connections/${encodeURIComponent(name)}/priority`,
                                               { method: 'PUT', body: { priority } }),
  wifiActivate:    (name)   => request(`/network/wifi/connections/${encodeURIComponent(name)}/activate`,
                                       { method: 'POST' }),
  apFallbackGet:   ()       => request('/network/ap-fallback'),
  apFallbackSet:   (body)   => request('/network/ap-fallback', { method: 'PUT', body }),
  apFallbackStart: ()       => request('/network/ap-fallback/start', { method: 'POST' }),
  apFallbackStop:  ()       => request('/network/ap-fallback/stop', { method: 'POST' }),
  switchBand:   (band)   => request('/control/band', { method: 'POST', body: { band } }),
  pileUp:       ()       => request('/pile-up'),
  activeHours:  ()       => request('/active-hours'),
  operatingLocation:    ()        => request('/operating-location'),
  setOperatingLocation: (country) => request('/operating-location', { method: 'POST', body: { country } }),
  setOperatingSuffix:   (suffix)  => request('/operating-suffix', { method: 'POST', body: { suffix } }),
  countryList:          ()        => request('/operating-location/countries'),
  stats:        ()       => request('/stats'),
  systemInfo:   ()       => request('/system/info'),
  swrTrend:     (hours = 24) => request('/stats/swr-trend', { query: { hours } }),
  whoHeardMe:   (hours=24) => request(`/psk/who-heard-me?hours=${hours}`),
  bandSuggestions: ()    => request('/stats/band-suggestions'),
  bestTime:     (band)   => request(`/stats/best-time/${encodeURIComponent(band)}`),
  callsignInfo: (call)   => request(`/callsign/${encodeURIComponent(call)}`),
  adifUrl:      (operator) => `/api${adifPath(operator)}`,
  downloadAdif: (operator, filename) => download(adifPath(operator), filename),
  clublogManualStatus: (operator) =>
                    request('/log/clublog-manual/status', { query: { operator } }),
  clublogManualCreateExport: (operator) =>
                    request('/log/clublog-manual/export', {
                      method: 'POST', body: { operator },
                    }),
  clublogManualConfirm: (batchId, operator) =>
                    request(`/log/clublog-manual/${encodeURIComponent(batchId)}/confirm`, {
                      method: 'POST', body: { operator },
                    }),
  download:     (pathOrUrl, filename) => download(pathOrUrl, filename),
  dxCluster:    (opts)   => request('/dx-cluster',           { query: opts }),
  operatingLocations: () => request('/operating-locations'),
  heatmap:      (opts)   => request('/heard/heatmap',         { query: opts }),

  // Version / Self-Update — futtert die SystemUpdateCard auf der Konfig-Seite.
  systemVersion:    ()       => request('/system/version'),
  triggerSelfUpdate:()       => request('/system/self-update', { method: 'POST' }),

  // Multi-Operator-Profile (Sebastian 2026-05-23)
  operatorsList:   ()       => request('/operators'),
  operatorActive:  ()       => request('/operators/active'),
  operatorSelect:  (callsign) => request('/operators/select', {
                                method: 'POST', body: { callsign },
                              }),
  operatorCreate:  (body)   => request('/operators', { method: 'POST', body }),
  // v0.67.0 — Zugangsdaten eines bestehenden Profils aendern. Nur die
  // mitgesendeten Felder werden angefasst, leerer String loescht eins.
  operatorUpdate:  (callsign, body) =>
                    request(`/operators/${encodeURIComponent(callsign)}`,
                            { method: 'PATCH', body }),
  operatorDelete:  (callsign, force=false) =>
                    request(`/operators/${encodeURIComponent(callsign)}${force ? '?force=true' : ''}`,
                            { method: 'DELETE' }),
  // v0.28.0 — Pre-Flight: ist der On-Air-Call in QRZ/ClubLog upload-bereit?
  operatorPreflight: (callsign) =>
                    request(`/operators/preflight?callsign=${encodeURIComponent(callsign)}`),
  // v0.29.0 — Sende-Call-Logbuecher (Prefix/Suffix → eigener QRZ-Key)
  operatorAddLogbook: (callsign, on_air_call, qrz_logbook_api_key) =>
                    request(`/operators/${encodeURIComponent(callsign)}/logbook`,
                            { method: 'PUT', body: { on_air_call, qrz_logbook_api_key } }),
  operatorDeleteLogbook: (callsign, on_air_call) =>
                    request(`/operators/${encodeURIComponent(callsign)}/logbook?on_air_call=${encodeURIComponent(on_air_call)}`,
                            { method: 'DELETE' }),

  // v0.37.0 — API-Auth
  blobUrl,                                        // Bild hinter der Token-Pruefung
  authToken:    ()       => getToken(),           // fuer SSE ?token=
  authTokens:   ()       => request('/auth/token'),  // {api_token, ntfy_action_token}
  // v0.39.0 — Master-Token auf merkbares Passwort setzen
  setAuthPassword: (token) => request('/auth/token', { method: 'POST', body: { token } }),

  // v0.44.1 — generischer GET (haengt Token + 401-Handling an). Damit
  // Panels NIE wieder rohes fetch() nutzen, das den Auth-Header umgeht.
  get:          (path, query) => request(path, { query }),

  // v0.48.0 — Demo-Modus (Simulator statt ALSA) umschalten → Dienst-Neustart.
  setDemoMode:  (enabled) => request('/control/demo-mode',
                                     { method: 'POST', body: { enabled } }),
};
