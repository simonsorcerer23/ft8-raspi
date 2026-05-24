<script>
  // Compact one-line status. Two distinct indicators:
  //   "Op-Mode" — which mode YOU activated (CQ / Antworten / aus)
  //   "State"   — what the engine is doing RIGHT NOW
  // Both can be different — e.g. Antworten-Mode on but no CQ heard yet,
  // state = idle but op-mode = Antworten.
  import { statusStore } from '../lib/stores.svelte.js';
  import OperatorSwitcher from './OperatorSwitcher.svelte';

  const v = $derived(statusStore.value);
  const state = $derived(v.state ?? 'UNKNOWN');

  // Op-Mode bildet die USER-INTENT ab, nicht den momentanen Engine-State.
  // Vorher zählten wir QSO_RESPOND/QSO_REPORT als "cq", obwohl wir auch
  // im Hunting-Mode in diese States gehen — sobald der State-Machine eine
  // fremde Station antwortet, sah die Anzeige aus als hätte der User
  // selber CQ gerufen. Jetzt: auto_cq=true → CQ, auto_answer=true → Hunt,
  // sonst Off — unabhängig davon was der State gerade tut.
  const opMode = $derived(
    v.auto_cq ? 'cq' :
    v.auto_answer ? 'hunt' : 'off'
  );
  const opModeLabel = $derived(
    opMode === 'cq'   ? '🛰️ CQ-MODE'
  : opMode === 'hunt' ? '🎯 ANTWORTEN'
  : '— OFF —'
  );
  const opModeColor = $derived(
    opMode === 'cq'   ? '#38bdf8'
  : opMode === 'hunt' ? '#a78bfa'
  : '#475569'
  );

  const stateColor = $derived(
    state === 'IDLE' ? '#22c55e'
  : state === 'TX_LOCKED' ? '#ef4444'
  : (state.startsWith('CQ') || state.startsWith('QSO')) ? '#38bdf8'
  : '#94a3b8'
  );
  const stateLabel = $derived({
    IDLE: 'BEREIT', CQ_CALLING: 'sendet CQ', QSO_RESPOND: 'antwortet',
    QSO_REPORT: 'Report', QSO_LOG: 'loggt', TX_LOCKED: 'GESPERRT',
    UNKNOWN: '…',
  }[state] ?? state);

  function freqMHz(hz) { return hz ? (hz / 1_000_000).toFixed(3) : '—'; }
</script>

<div class="bar">
  <div class="call"><OperatorSwitcher /></div>

  <div class="op-mode" style="background: {opModeColor}">{opModeLabel}</div>
  <div class="state-pill" style="background: {stateColor}">{stateLabel}</div>

  <div class="cell"><small>FREQ</small><strong>{freqMHz(v.rig?.freq_hz)}</strong></div>
  <div class="cell"><small>MOD</small><strong>{v.rig?.mode ?? '—'}</strong></div>
  <div class="cell"><small>PWR</small><strong>{v.tx_power_w ?? 10}W</strong></div>
  <div class="cell"><small>ANT</small><strong>{v.active_antenna ?? '—'}</strong></div>
  <div class="cell"><small>GRID</small><strong>{(v.gps?.lat != null) ? gpsLocator(v.gps.lat, v.gps.lon) : '—'}</strong></div>
  <div class="cell"><small>SATS</small><strong>{v.gps?.sats_used ?? 0}/{v.gps?.sats_seen ?? 0}</strong></div>
  <div class="cell"><small>WORKED</small><strong>{v.worked_count ?? 0}</strong></div>

  {#if v.current_qso_call}
    <div class="qso">📡 {v.current_qso_call}</div>
  {/if}
  {#if v.last_lock_reason}
    <div class="reason">⚠ {v.last_lock_reason}</div>
  {/if}
</div>

<script context="module">
  function gpsLocator(lat, lon) {
    if (lat == null || lon == null) return '—';
    const lon180 = lon + 180, lat90 = lat + 90;
    const fLon = Math.floor(lon180 / 20), fLat = Math.floor(lat90 / 10);
    const sLon = Math.floor((lon180 - fLon * 20) / 2);
    const sLat = Math.floor(lat90 - fLat * 10);
    return String.fromCharCode(65 + fLon) + String.fromCharCode(65 + fLat) + sLon + sLat;
  }
</script>

<style>
  .bar {
    display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;
    padding: 0.5rem 0.7rem; background: var(--panel);
    border-radius: 8px; border: 1px solid #1e293b;
    font-size: 0.8rem;
  }
  .call {
    font-family: ui-monospace, monospace;
    font-size: 1rem; font-weight: 700; color: var(--accent);
  }
  .op-mode {
    padding: 0.25rem 0.7rem; border-radius: 999px;
    color: #0f172a; font-weight: 700; font-size: 0.78rem;
    text-transform: uppercase; letter-spacing: 0.06em;
  }
  .state-pill {
    padding: 0.15rem 0.55rem; border-radius: 999px;
    color: #0f172a; font-weight: 700; font-size: 0.72rem;
    text-transform: uppercase; letter-spacing: 0.05em;
  }
  .cell {
    display: flex; flex-direction: column; align-items: flex-start; gap: 0;
    line-height: 1; padding: 0 0.3rem;
  }
  .cell small { color: #94a3b8; font-size: 0.6rem; letter-spacing: 0.05em; }
  .cell strong { font-family: ui-monospace, monospace; font-size: 0.85rem; }
  .qso {
    padding: 0.15rem 0.5rem; background: rgba(56,189,248,0.15);
    color: var(--accent); border-radius: 4px; font-weight: 600;
    font-family: ui-monospace, monospace;
  }
  .reason { color: var(--danger); font-size: 0.8rem; }
</style>
