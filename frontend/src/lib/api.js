// Thin fetch wrapper around the FastAPI backend.

async function request(path, { method = 'GET', body, query } = {}) {
  const init = { method, headers: { 'Accept': 'application/json' } };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let url = `/api${path}`;
  if (query) {
    const qs = new URLSearchParams(
      Object.entries(query).filter(([_, v]) => v !== null && v !== undefined && v !== '')
    ).toString();
    if (qs) url += `?${qs}`;
  }
  const r = await fetch(url, init);
  const ct = r.headers.get('content-type') || '';
  const payload = ct.includes('application/json') ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = typeof payload === 'string' ? payload : (payload.detail || JSON.stringify(payload));
    throw new Error(`${r.status} ${r.statusText}: ${msg}`);
  }
  return payload;
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
  switchBand:   (band)   => request('/control/band', { method: 'POST', body: { band } }),
  pileUp:       ()       => request('/pile-up'),
  activeHours:  ()       => request('/active-hours'),
  operatingLocation:    ()        => request('/operating-location'),
  setOperatingLocation: (country) => request('/operating-location', { method: 'POST', body: { country } }),
  countryList:          ()        => request('/operating-location/countries'),
  stats:        ()       => request('/stats'),
  systemInfo:   ()       => request('/system/info'),
  swrTrend:     (hours = 24) => request('/stats/swr-trend', { query: { hours } }),
  whoHeardMe:   (hours=24) => request(`/psk/who-heard-me?hours=${hours}`),
  bandSuggestions: ()    => request('/stats/band-suggestions'),
  bestTime:     (band)   => request(`/stats/best-time/${encodeURIComponent(band)}`),
  callsignInfo: (call)   => request(`/callsign/${encodeURIComponent(call)}`),
  adifUrl:      (operator) => operator
                                ? `/api/log/adif?operator=${encodeURIComponent(operator)}`
                                : '/api/log/adif',
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
  operatorDelete:  (callsign, force=false) =>
                    request(`/operators/${encodeURIComponent(callsign)}${force ? '?force=true' : ''}`,
                            { method: 'DELETE' }),
};
