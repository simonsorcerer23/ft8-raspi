<script>
  // IC-705 instrument panel. Reads everything Hamlib will report:
  //   freq · mode · filter bandwidth · VFO · split
  //   S-meter (RX) or TX-power-meter (TX) — mutually exclusive
  //   SWR · ALC · output power
  //   AF · RF gain · NR level
  //   PreAmp · Att · NB · AGC on/off
  //   Battery voltage · internal temperature
  //
  // Until the IC-705 is plugged in, this reads the mock rigctld so the
  // layout is verifiable end-to-end.
  import { statusStore } from '../lib/stores.svelte.js';
  import { api } from '../lib/api.js';
  import { onMount } from 'svelte';

  const rig = $derived(statusStore.value.rig ?? {});
  const connected = $derived(rig.freq_hz != null);
  const isTx = $derived(rig.ptt === true);

  // Display label derived from the configured rig model. Reads once on
  // mount — the config doesn't change behind our backs at runtime.
  let modelLabel = $state('Rig');
  // The rig's stock max output in watts — sets the conversion factor
  // for the RFPOWER_METER reading (Hamlib reports it as 0..1, normalised
  // against the rig's max). Default 10 W matches IC-705 so the dev
  // workstation with the mock rig keeps showing sane numbers.
  let rigMaxW = $state(10);
  const MODEL_LABELS = {
    ic705:  'IC-705',
    ic7300: 'IC-7300',
    ic9700: 'IC-9700',
    ic7610: 'IC-7610',
  };
  const MODEL_MAX_W = {
    ic705: 10, ic7300: 100, ic9700: 100, ic7610: 100,
  };
  onMount(async () => {
    try {
      const c = await api.config();
      const m = c?.rig?.model;
      modelLabel = MODEL_LABELS[m] ?? 'Rig';
      rigMaxW = MODEL_MAX_W[m] ?? 10;
    } catch { /* keep generic defaults */ }
  });
  // ALC closed-loop telemetry — orchestrator-level state, not the rig's
  // instantaneous level. Surfaced so the operator can see what the
  // loop has decided about the TX audio amplitude.
  const audioGain = $derived(statusStore.value.audio_gain);

  // Audio Gain ist KEIN Alarmwert sondern der Stand des Closed-Loops
  // nach dem Trimmen. Roter Text suggerierte faelschlich "kritisch",
  // dabei heisst ein niedriger Wert nur dass der Loop sauber gegen-
  // gesteuert hat. Wir lassen die Schrift neutral (var(--fg)) und
  // graden nur sehr extreme Werte ein:
  //   - bei sehr niedrigem Gain (<0.10) Loop ist am Boden angekommen
  //     → wahrscheinlich Rig-Pegel zu hoch eingestellt, gelb als Hinweis
  //   - sonst neutrale Schriftfarbe, kein Alarm
  function gainColor(g) {
    if (g == null) return '#475569';
    if (g < 0.10) return '#fbbf24';   // Loop nahe am Boden — Hinweis
    return 'var(--fg)';                // normaler Betrieb
  }

  function freqDisplay(hz) {
    if (!hz) return '— . — — —';
    const mhz = Math.floor(hz / 1_000_000);
    const khz = Math.floor((hz / 1_000) % 1_000);
    const rest = hz % 1_000;
    return `${mhz}.${String(khz).padStart(3, '0')}.${String(rest).padStart(3, '0')}`;
  }

  function bwDisplay(hz) {
    if (!hz) return '—';
    if (hz >= 1000) return `${(hz / 1000).toFixed(1)} kHz`;
    return `${hz} Hz`;
  }

  function swrColor(swr) {
    if (swr == null) return '#475569';
    if (swr <= 1.5) return '#22c55e';
    if (swr <= 2.0) return '#fbbf24';
    if (swr <= 3.0) return '#f59e0b';
    return '#ef4444';
  }

  function alcColor(alc) {
    if (alc == null) return '#475569';
    if (alc <= 0.05) return '#22c55e';
    if (alc <= 0.20) return '#fbbf24';
    return '#ef4444';
  }

  function tempColor(t) {
    if (t == null) return '#475569';
    if (t < 50) return '#22c55e';
    if (t < 65) return '#fbbf24';
    return '#ef4444';
  }

  function batColor(v) {
    if (v == null) return '#475569';
    if (v >= 12.5) return '#22c55e';
    if (v >= 11.5) return '#fbbf24';
    return '#ef4444';
  }

  // Audio-Pegel-Mapping. Hintergrund: Hamlib's STRENGTH/RAWSTR ist beim
  // IC-7300 in PKTUSB als kaputt bekannt (liefert konstant 0/-54). Wir
  // beziehen den RX-Pegel stattdessen aus dem ALSA-Capture-Stream als
  // RMS in dBFS (-∞..0). Bar-Mapping: -80 dBFS = leer, -10 dBFS = voll.
  function audioBarPct(dbfs) {
    if (dbfs == null) return 0;
    const min = -80, max = -10;
    return Math.max(0, Math.min(100, ((dbfs - min) / (max - min)) * 100));
  }
  function audioColor(dbfs) {
    // Schwellen kalibriert nach WSJT-X-Empfehlung "RX 30 dB" → optimal
    // ist Audio-Pegel um -30 bis -10 dBFS. 0 dBFS = Clipping, darunter
    // ist Headroom. Skala:
    //   < -60: grün  — ruhiges Rauschen ("Band leer")
    //   < -25: blau  — normaler RX-Signalpegel, ideal für Decoder
    //   < -10: gelb  — starkes Signal, viel zum Decodieren
    //   < -3:  orange — heiß, FT8-Bursts kurz vor Clipping
    //   ≥ -3:  rot   — Clipping-Gefahr, USB-AF-Output am Rig runter
    if (dbfs == null) return '#475569';
    if (dbfs < -60) return '#22c55e';
    if (dbfs < -25) return '#60a5fa';
    if (dbfs < -10) return '#fbbf24';
    if (dbfs <  -3) return '#f59e0b';
    return '#ef4444';
  }
</script>

<div class="rig" class:tx={isTx}>
  <div class="header">
    <span class="badge">🎙️ {modelLabel} {rig.vfo && rig.vfo !== '0' && !rig.vfo.startsWith('RPRT') ? rig.vfo : ''}</span>
    <div class="meta">
      <span class="tag mode-tag"
            class:mode-ft4={(statusStore.value.mode ?? 'FT8') === 'FT4'}
            title={(statusStore.value.mode ?? 'FT8') === 'FT4'
              ? 'FT4 active — 7.5s slots, schnellere QSOs, ~3 dB weniger Sensitivity als FT8'
              : 'FT8 active — 15s slots, Standard-Modus'}>
        📡 {statusStore.value.mode ?? 'FT8'}
      </span>
      {#if statusStore.value.cq_directed}
        <span class="tag directed-tag"
              title="Directed CQ: nur Stationen aus dieser Region/Award rufen den Pi an">
          🎯 CQ {statusStore.value.cq_directed}
        </span>
      {/if}
      {#if rig.split_on}<span class="tag">SPLIT</span>{/if}
      {#if rig.battery_v != null}
        <span class="tag" style="color: {batColor(rig.battery_v)}">
          🔋 {rig.battery_v.toFixed(1)} V
        </span>
      {/if}
      {#if rig.internal_temp_c != null}
        <span class="tag" style="color: {tempColor(rig.internal_temp_c)}">
          🌡 {rig.internal_temp_c.toFixed(0)} °C
        </span>
      {/if}
      {#if !connected}<span class="warn">offline</span>{/if}
    </div>
  </div>

  <div class="dial">
    <span class="freq">{freqDisplay(rig.freq_hz)}</span>
    <small class="unit">MHz · {rig.mode ?? '—'} · {bwDisplay(rig.bandwidth_hz)}</small>
  </div>

  <!-- Meter — S-Meter on RX, Power-Meter on TX (mutually exclusive) -->
  {#if isTx}
    <div class="meter tx-meter">
      <small class="lbl">PWR (TX)</small>
      <div class="bar"><div class="fill" style="width: {Math.min(100, (rig.rfpower_meter ?? 0) * 100)}%"></div></div>
      <strong>{Math.round((rig.rfpower_meter ?? 0) * rigMaxW)} W</strong>
    </div>
  {:else}
    {@const dbfs = statusStore.value.rx_audio_dbfs}
    <div class="meter s-meter" title="RX-Audio-Pegel direkt aus dem ALSA-Capture (RMS letzter 250 ms). Hamlib STRENGTH ist beim IC-7300 kaputt — dieser Wert zappelt mit echter Signal-Aktivität.">
      <small class="lbl">RX-PEGEL</small>
      <div class="bar"><div class="fill" style="width: {audioBarPct(dbfs)}%; background: {audioColor(dbfs)}"></div></div>
      <strong>{dbfs != null ? `${dbfs.toFixed(1)} dBFS` : '—'}</strong>
    </div>
  {/if}

  <div class="grid">
    <div class="cell" style="--c: {swrColor(rig.swr)}">
      <small>SWR</small>
      <strong>{rig.swr?.toFixed(2) ?? '—'}</strong>
    </div>
    <div class="cell" style="--c: {alcColor(rig.alc)}" title="Instantaneous ALC from rig">
      <small>ALC</small>
      <strong>{rig.alc != null ? Math.round(rig.alc * 100) : '—'}{rig.alc != null ? '%' : ''}</strong>
    </div>
    <div class="cell" style="--c: {gainColor(audioGain)}"
         title="Audio-gain factor maintained by the ALC closed-loop. Lower = quieter TX audio to keep rig ALC inside target window.">
      <small>AUDIO GAIN</small>
      <strong>{audioGain != null ? Math.round(audioGain * 100) + '%' : '—'}</strong>
    </div>
    <div class="cell"><small>PWR SET</small>
      <strong>{statusStore.value.tx_power_w ?? '—'} W</strong>
    </div>
    <div class="cell"><small>AF GAIN</small>
      <strong>{rig.af_gain != null ? Math.round(rig.af_gain * 100) + '%' : '—'}</strong>
    </div>
    <div class="cell"><small>RF GAIN</small>
      <strong>{rig.rf_gain != null ? Math.round(rig.rf_gain * 100) + '%' : '—'}</strong>
    </div>
    <div class="cell tx-cell" class:on={isTx}>
      <small>STATUS</small>
      <strong>{isTx ? '🔴 TX' : '⚫ RX'}</strong>
    </div>
  </div>

  <!-- Function indicator buttons (read-only) -->
  <div class="funcs">
    <span class="func" class:on={rig.preamp_on}>PRE</span>
    <span class="func" class:on={rig.att_on}>ATT</span>
    <span class="func" class:on={rig.nb_on}>NB</span>
    <span class="func" class:on={(rig.nr_level ?? 0) > 0}>NR</span>
    <span class="func agc" title="AGC mode">AGC {rig.agc_mode ?? '—'}</span>
  </div>
</div>

<style>
  .rig {
    background: #0b1220; border: 1px solid #1e293b; border-radius: 8px;
    padding: 0.7rem 0.9rem;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
  }
  .rig.tx {
    border-color: var(--danger);
    box-shadow: 0 0 12px rgba(239,68,68,0.4);
  }
  .header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 0.4rem; gap: 0.5rem; flex-wrap: wrap;
  }
  .badge {
    color: var(--accent); font-size: 0.85rem; font-weight: 700;
    letter-spacing: 0.05em;
  }
  .meta { display: flex; gap: 0.4rem; flex-wrap: wrap; align-items: center; }
  .tag {
    padding: 0.1rem 0.4rem; border-radius: 4px;
    background: rgba(15,23,42,0.6); border: 1px solid #1e293b;
    font-size: 0.75rem; font-family: ui-monospace, monospace; font-weight: 700;
  }
  .mode-tag {
    color: var(--accent); border-color: rgba(56,189,248,0.3);
    background: rgba(56,189,248,0.08);
  }
  /* FT4 markant orange — sichtbar dass der Pi im schnelleren Modus
     laeuft, damit der Operator das nicht aus Versehen uebersieht
     wenn er Standard-FT8 erwartet hat. Sebastian v0.4.1. */
  .mode-tag.mode-ft4 {
    color: #fb923c; border-color: rgba(251,146,60,0.4);
    background: rgba(251,146,60,0.12);
  }
  .directed-tag {
    color: #c084fc; border-color: rgba(192,132,252,0.3);
    background: rgba(192,132,252,0.08);
  }
  .warn {
    color: #f59e0b; font-size: 0.75rem;
    background: rgba(245,158,11,0.1); padding: 0.1rem 0.4rem; border-radius: 4px;
  }

  .dial { text-align: center; margin: 0.5rem 0; font-family: ui-monospace, monospace; }
  .freq {
    font-size: 1.8rem; font-weight: 700;
    color: #38bdf8; letter-spacing: 0.05em;
    text-shadow: 0 0 8px rgba(56,189,248,0.3);
  }
  .unit { display: block; color: #94a3b8; font-size: 0.75rem; margin-top: 0.1rem; }

  .meter {
    margin: 0.5rem 0; padding: 0.4rem 0.6rem;
    background: rgba(15,23,42,0.7); border-radius: 6px;
    display: grid; grid-template-columns: 4.5rem 1fr auto; gap: 0.5rem;
    align-items: center;
  }
  .meter .lbl { color: #94a3b8; font-size: 0.7rem; letter-spacing: 0.05em; }
  .meter .bar {
    height: 12px; background: #1e293b; border-radius: 3px; overflow: hidden;
  }
  .meter .fill {
    height: 100%; transition: width 0.25s ease;
  }
  .s-meter .fill {
    background: linear-gradient(90deg, #22c55e 0%, #22c55e 50%, #fbbf24 70%, #ef4444 90%);
  }
  .tx-meter .fill { background: #ef4444; }
  .meter strong {
    font-family: ui-monospace, monospace; font-weight: 700; font-size: 0.85rem;
    color: var(--fg);
  }

  .grid {
    display: grid; grid-template-columns: repeat(7, 1fr); gap: 0.35rem;
    margin-top: 0.4rem;
  }
  .cell {
    display: flex; flex-direction: column; align-items: center;
    background: rgba(15,23,42,0.6); border-radius: 4px; padding: 0.3rem 0.2rem;
    text-align: center;
  }
  .cell small {
    color: #94a3b8; font-size: 0.6rem; letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .cell strong {
    font-family: ui-monospace, monospace; font-weight: 700; font-size: 0.95rem;
    margin-top: 0.15rem; color: var(--c, var(--fg));
  }
  .tx-cell.on strong {
    color: var(--danger); animation: blink 1s steps(2) infinite;
  }
  @keyframes blink { 50% { opacity: 0.4; } }

  .funcs {
    display: flex; gap: 0.3rem; flex-wrap: wrap;
    margin-top: 0.45rem; padding-top: 0.4rem;
    border-top: 1px dotted #1e293b;
  }
  .func {
    padding: 0.15rem 0.5rem; border-radius: 4px;
    background: #1e293b; color: #64748b;
    font-size: 0.7rem; font-weight: 700; letter-spacing: 0.05em;
    font-family: ui-monospace, monospace;
  }
  .func.on { background: #38bdf8; color: #0f172a; }
  .func.agc { background: rgba(56,189,248,0.15); color: var(--accent); }

  @media (max-width: 600px) {
    .grid { grid-template-columns: repeat(3, 1fr); }
    .freq { font-size: 1.4rem; }
  }
</style>
