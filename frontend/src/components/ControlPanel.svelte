<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { statusStore } from '../lib/stores.svelte.js';

  let busy = $state(false);
  let lastError = $state(null);
  let antennas = $state([]);
  // Slider-Obergrenze leitet sich vom Rig ab — IC-705 = 10 W, IC-7300/9700
  // = 100 W, IC-7610 = 100 W. Default 10 W hält die Anzeige bei einem
  // unkonfigurierten Pi/dev-Workstation sinnvoll.
  let rigMaxW = $state(10);
  const RIG_MAX_W = { ic705: 10, ic7300: 100, ic9700: 100, ic7610: 100, qmx_plus: 5 };
  // Hunting-Filter — live aus dem Funk-Dashboard schaltbar damit Dad
  // nicht ins Config-Menü muss um "nur DXCC-Relevante" zu togglen.
  let huntSkipWorked = $state(false);
  let huntDxccOnly   = $state(false);

  async function toggleHuntFilter(field, value) {
    await call(() => api.setHuntFilter({ [field]: value }));
    if (field === 'skip_worked') huntSkipWorked = value;
    if (field === 'dxcc_only')   huntDxccOnly   = value;
  }

  const state = $derived(statusStore.value.state ?? 'UNKNOWN');
  const isInQso  = $derived(state === 'QSO_RESPOND' || state === 'QSO_REPORT');
  const isLocked = $derived(state === 'TX_LOCKED');
  // Op-Mode-Klammern bilden den USER-INTENT ab. auto_cq und auto_answer
  // sind die Flags die das State-Machine-CTX trägt — exklusiv vom User
  // gesetzt durch die beiden Mode-Buttons. Damit zeigt der CQ-Button
  // auch dann "STOP CQ" wenn die Maschine gerade in QSO_RESPOND ist —
  // weil's eben der CQ-Run war der zum QSO führte; und der Antworten-
  // Button zeigt "STOP Antworten" weiter wenn ein Hunting-QSO gerade
  // im Austausch ist.
  const cqActive   = $derived(statusStore.value.auto_cq === true);
  const huntActive = $derived(statusStore.value.auto_answer === true && !cqActive);
  const isHunting  = $derived(huntActive);
  const isCqMode   = $derived(cqActive);
  const txPower = $derived(statusStore.value.tx_power_w ?? 10);
  const activeAntenna = $derived(statusStore.value.active_antenna ?? '');
  // Lizenz-Cap aus Status (Backend-berechnet aus aktivem Band +
  // operator.license_class). Wenn Backend einen Wert liefert,
  // gewinnt der — sonst Fallback auf Rig-Hardware-Cap.
  const sliderMax = $derived(
    statusStore.value.effective_max_power_w ?? rigMaxW
  );
  const activeBand   = $derived(statusStore.value.active_band ?? null);
  const licenseClass = $derived(statusStore.value.license_class ?? 'A');

  async function call(fn) {
    busy = true; lastError = null;
    try { await fn(); await statusStore.refresh(); }
    catch (e) { lastError = e.message; }
    finally { busy = false; }
  }

  // CQ mode: I call CQ until someone answers
  async function toggleCq() {
    if (isCqMode || isInQso) await call(api.stop);
    else { if (isHunting) await call(() => api.setAutoAnswer(false)); await call(api.startCq); }
  }
  // Hunting / Antworten mode: I auto-call CQers I hear
  async function toggleHunting() {
    if (isHunting) await call(() => api.setAutoAnswer(false));
    else {
      if (isCqMode || isInQso) await call(api.stop);
      await call(() => api.setAutoAnswer(true));
    }
  }

  // TX-Power-Slider mit Live-Display und numerischer Eingabe
  // (Sebastian 2026-05-24: konkrete Wattzahl per Slider zu treffen
  // war frustig, jetzt sieht man den Wert beim Ziehen + kann ihn
  // direkt eintippen).
  let pendingPower = $state(10);
  let pwrDragging  = $state(false);
  // Sync vom Server in den lokalen State — nur wenn der User gerade
  // NICHT selber dran zieht, sonst wuerde unsere Drag-Position vom
  // naechsten Status-Tick ueberschrieben.
  $effect(() => {
    if (!pwrDragging) pendingPower = Math.min(txPower, sliderMax);
  });
  function clampPwr(v) {
    const n = parseInt(v);
    if (!Number.isFinite(n)) return pendingPower;
    return Math.max(1, Math.min(sliderMax, n));
  }
  async function commitPower(v) {
    const w = clampPwr(v);
    pendingPower = w;
    pwrDragging = false;
    await call(() => api.setTxPower(w));
  }
  async function onAntenna(e) { await call(() => api.setAntenna(e.target.value)); }

  onMount(async () => {
    try {
      const c = await api.config();
      antennas = c.antennas ?? [];
      // Wenn der Operator explizit ein max_power_w gesetzt hat
      // (Pflicht-Begrenzung unter dem Rig-Stock), gewinnt das. Sonst
      // die stock-Leistung des erkannten Modells.
      rigMaxW = c.rig?.max_power_w ?? RIG_MAX_W[c.rig?.model ?? 'ic705'];
      huntSkipWorked = c.operating?.hunt_skip_worked ?? false;
      huntDxccOnly   = c.operating?.hunt_dxcc_only ?? false;
    } catch {}
  });
</script>

<div class="panel">
  {#if isLocked}
    <div class="lock-banner">
      <strong>TX gesperrt:</strong> {statusStore.value.last_lock_reason ?? 'unbekannt'}
      <button onclick={() => call(api.resetLock)} disabled={busy}>Sperre lösen</button>
    </div>
  {:else}
    <div class="main-buttons">
      <button class="big cq"
              class:active={cqActive}
              class:dim={!cqActive}
              disabled={busy || huntActive}
              onclick={toggleCq}>
        <span class="label">{cqActive ? 'STOP CQ' : 'CQ'}</span>
        <small>{cqActive
                  ? (isInQso ? 'QSO läuft' : 'CQ läuft — wartet auf Anruf')
                  : huntActive
                    ? 'inaktiv während Antworten-Mode'
                    : 'CQ rufen bis jemand antwortet'}</small>
      </button>
      <button class="big hunt"
              class:active={huntActive}
              class:dim={!huntActive}
              disabled={busy || cqActive}
              onclick={toggleHunting}>
        <span class="label">{huntActive ? 'STOP Antworten' : 'Antworten'}</span>
        <small>{huntActive
                  ? (isInQso ? 'QSO läuft' : 'Hunting läuft — rufe gehörte CQs der Reihe nach')
                  : cqActive
                    ? 'inaktiv während CQ-Mode'
                    : 'Stationen rufen die selbst CQ rufen'}</small>
      </button>
    </div>
  {/if}

  {#if isInQso}
    <button class="skip" onclick={() => call(api.skipQso)} disabled={busy}>
      QSO abbrechen (nicht loggen)
    </button>
  {/if}

  <!-- Hunting-Filter live umschaltbar — keine Config-Reise nötig -->
  <div class="hunt-filters" class:dim={!huntActive}>
    <label class="check">
      <input type="checkbox" checked={huntSkipWorked}
             disabled={busy}
             onchange={(e) => toggleHuntFilter('skip_worked', e.target.checked)}/>
      <span>nur noch nie gearbeitete</span>
    </label>
    <label class="check">
      <input type="checkbox" checked={huntDxccOnly}
             disabled={busy}
             onchange={(e) => toggleHuntFilter('dxcc_only', e.target.checked)}/>
      <span>nur neue DXCC (Award-Modus)</span>
    </label>
  </div>

  <div class="row">
    <label class="settings">
      <div class="hdr">
        TX-Leistung
        {#if activeBand && sliderMax < rigMaxW}
          <span class="cap-hint" title="Lizenzbedingtes Cap auf {activeBand}">
            🇩🇪 {licenseClass} · max {sliderMax}W
          </span>
        {/if}
      </div>
      {#key sliderMax}
        <div class="pwr-controls">
          <input class="pwr-slider" type="range" min="1" max={sliderMax} step="1"
                 bind:value={pendingPower}
                 oninput={() => pwrDragging = true}
                 onchange={(e) => commitPower(e.target.value)}
                 disabled={busy}/>
          <input class="pwr-number" type="number" min="1" max={sliderMax} step="1"
                 bind:value={pendingPower}
                 onfocus={() => pwrDragging = true}
                 onblur={(e) => commitPower(e.target.value)}
                 onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); } }}
                 disabled={busy}/>
          <span class="pwr-unit">W</span>
        </div>
      {/key}
      <div class="value">
        {pendingPower} W
        {#if pwrDragging && pendingPower !== Math.min(txPower, sliderMax)}
          <span class="pending">(nicht gespeichert)</span>
        {/if}
      </div>
    </label>
    <label class="settings">
      <div class="hdr">Antenne</div>
      <select value={activeAntenna} onchange={onAntenna} disabled={busy || antennas.length === 0}>
        {#each antennas as a}
          <option value={a.name}>{a.name} ({a.bands.join(', ')})</option>
        {/each}
      </select>
    </label>
  </div>

  <button class="panic" onclick={() => call(api.panic)} disabled={busy}>PANIC</button>
  <button class="shutdown"
          onclick={() => { if (confirm('Pi wirklich herunterfahren?')) call(api.shutdown); }}
          disabled={busy}>🌙 Pi herunterfahren</button>
  {#if lastError}<div class="err">⚠ {lastError}</div>{/if}
</div>

<style>
  .panel { display: flex; flex-direction: column; gap: 0.7rem; padding: 0.5rem; }
  .main-buttons {
    display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem;
  }
  .big {
    display: flex; flex-direction: column; align-items: stretch; justify-content: center;
    font-weight: 700; border: none; border-radius: 12px;
    padding: 1.2rem 1rem; cursor: pointer; min-height: 5rem;
    text-align: center; transition: filter 0.15s ease;
  }
  .big .label { font-size: 1.7rem; letter-spacing: 0.05em; }
  .big small  { font-size: 0.7rem; opacity: 0.85; margin-top: 0.3rem; font-weight: 500; }
  .big.cq   { background: var(--accent); color: #0f172a; }
  .big.hunt { background: #a78bfa;       color: #0f172a; }
  .big.cq.active   { background: #f59e0b; }
  .big.hunt.active { background: #f59e0b; }
  /* Dim-State: Button ist klickbar oder gerade inaktiv, soll aber
   * nicht denselben visuellen Stellenwert wie der aktive Modus haben.
   * Sebastians Beschwerde: beide farbig nebeneinander suggeriert
   * "beides läuft". Mit dim wird der gerade NICHT-Mode deutlich
   * zurückgenommen, der aktive bleibt voll farbig. */
  .big.dim { opacity: 0.45; filter: grayscale(40%); }
  .big.dim.active { opacity: 1; filter: none; }
  .big:hover:not(:disabled) { filter: brightness(1.08); }
  @media (max-width: 540px) { .main-buttons { grid-template-columns: 1fr; } }

  .skip {
    background: transparent; color: #f59e0b; border: 1px solid #f59e0b;
    border-radius: 6px; padding: 0.5rem; cursor: pointer; font-size: 0.9rem;
    font-weight: 600;
  }
  .panic {
    background: var(--danger); color: white; border: none; border-radius: 8px;
    padding: 1rem; font-size: 1.3rem; font-weight: 700; cursor: pointer;
    letter-spacing: 0.1em;
  }
  .shutdown {
    background: transparent; color: #94a3b8; border: 1px solid #334155;
    border-radius: 8px; padding: 0.6rem; font-size: 0.95rem;
    cursor: pointer; margin-top: 0.5rem;
  }
  .hunt-filters {
    display: flex; gap: 1rem; flex-wrap: wrap;
    padding: 0.4rem 0.7rem;
    background: rgba(167,139,250,0.08);
    border: 1px solid rgba(167,139,250,0.25);
    border-radius: 8px;
    font-size: 0.85rem;
  }
  .hunt-filters.dim { opacity: 0.5; }
  .hunt-filters .check {
    display: inline-flex; align-items: center; gap: 0.35rem;
    cursor: pointer; user-select: none;
  }
  .hunt-filters input { margin: 0; cursor: pointer; }
  .shutdown:hover:not(:disabled) { background: rgba(148,163,184,0.1); }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .lock-banner {
    background: rgba(239, 68, 68, 0.15); border: 1px solid var(--danger);
    border-radius: 8px; padding: 0.7rem;
    display: flex; flex-direction: column; gap: 0.5rem;
  }
  .lock-banner button {
    background: var(--danger); color: white; border: none; border-radius: 4px;
    padding: 0.4rem 0.8rem; cursor: pointer; align-self: flex-start;
  }
  .err { color: var(--danger); font-size: 0.9rem; }
  .row { display: flex; gap: 0.7rem; flex-wrap: wrap; }
  .settings {
    flex: 1; min-width: 12rem;
    background: rgba(15,23,42,0.5); border: 1px solid #334155;
    border-radius: 8px; padding: 0.6rem;
    display: flex; flex-direction: column; gap: 0.3rem;
  }
  .settings .hdr { font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;
                   display: flex; justify-content: space-between; align-items: baseline; gap: 0.4rem; }
  .cap-hint { font-size: 0.7rem; color: #fbbf24; text-transform: none; font-weight: 600;
              background: rgba(251,191,36,0.12); padding: 0.1rem 0.35rem; border-radius: 4px; }
  .settings .value { font-family: ui-monospace, monospace; color: var(--accent); font-weight: 700; }
  .pending {
    font-family: inherit; font-weight: 500; color: #fbbf24;
    font-size: 0.7rem; margin-left: 0.5rem;
  }
  .pwr-controls {
    display: flex; align-items: center; gap: 0.5rem;
  }
  .pwr-controls .pwr-slider { flex: 1; }
  .pwr-controls .pwr-number {
    width: 4.5rem; background: #0b1220; color: var(--fg);
    border: 1px solid #334155; border-radius: 4px;
    padding: 0.3rem 0.4rem; font-family: ui-monospace, monospace;
    font-size: 0.9rem; text-align: right;
    -moz-appearance: textfield;
  }
  .pwr-controls .pwr-number::-webkit-inner-spin-button,
  .pwr-controls .pwr-number::-webkit-outer-spin-button {
    -webkit-appearance: none; margin: 0;
  }
  .pwr-controls .pwr-unit {
    color: #94a3b8; font-size: 0.8rem;
  }
  /* Select bekommt das normale Form-Element-Styling. */
  .settings select {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 4px; padding: 0.3rem; width: 100%;
    box-sizing: border-box;
  }
  /* Range-Slider voll aus eigener Hand. Kein border, kein padding —
   * die Browser legen die Thumb-Position auf das input.width an, jede
   * border/padding-Falte verschiebt den sichtbaren Thumb relativ
   * zum logischen Wert. */
  .settings input[type=range] {
    -webkit-appearance: none; appearance: none;
    width: 100%; height: 2rem;
    padding: 0; margin: 0; border: 0;
    background: transparent; box-sizing: border-box;
    cursor: pointer;
  }
  .settings input[type=range]:disabled { opacity: 0.5; cursor: not-allowed; }
  .settings input[type=range]::-webkit-slider-runnable-track {
    height: 6px; background: #334155; border-radius: 3px; border: 0;
  }
  .settings input[type=range]::-moz-range-track {
    height: 6px; background: #334155; border-radius: 3px; border: 0;
  }
  .settings input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 18px; height: 18px; border-radius: 50%;
    background: var(--accent); border: 2px solid #0b1220;
    margin-top: -6px; /* (thumb 18 - track 6)/2 = 6, dann Border 2 */
    cursor: pointer;
  }
  .settings input[type=range]::-moz-range-thumb {
    width: 18px; height: 18px; border-radius: 50%;
    background: var(--accent); border: 2px solid #0b1220;
    cursor: pointer;
  }
</style>
