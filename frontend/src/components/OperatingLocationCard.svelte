<script>
  // v0.22.0 — DX-Operating-Location.
  // Zeigt aktuellen TX-Call (mit Prefix wenn Auslandsbetrieb), GPS-Detection,
  // Mismatch-Warnung und Country-Selector. Bei Mismatch (GPS != current
  // Setting) wird ein oranges Banner mit Tap-to-fix-Action gezeigt.
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';

  let loc = $state(null);
  let countries = $state([]);
  let busy = $state(false);
  let err = $state(null);

  async function refresh() {
    try {
      loc = await api.operatingLocation();
      err = null;
    } catch (e) {
      err = e.message;
    }
  }

  async function loadCountries() {
    try {
      const r = await api.countryList();
      countries = r.countries;
    } catch { /* ignore */ }
  }

  async function setCountry(code) {
    if (busy) return;
    busy = true;
    err = null;
    try {
      loc = await api.setOperatingLocation(code || null);
    } catch (e) {
      err = e.message;
    } finally {
      busy = false;
    }
  }

  // v0.29.0 — Modifier-Suffix (Aeronautical/Maritime Mobile, Portable …)
  const SUFFIXES = [
    { v: '',    label: '🏠 keiner (Heimat)' },
    { v: 'AM',  label: '/AM · Aeronautical Mobile' },
    { v: 'MM',  label: '/MM · Maritime Mobile' },
    { v: 'P',   label: '/P · Portable' },
    { v: 'M',   label: '/M · Mobile' },
    { v: 'QRP', label: '/QRP · QRP' },
  ];

  async function setSuffix(s) {
    if (busy) return;
    busy = true;
    err = null;
    try {
      loc = await api.setOperatingSuffix(s || null);
    } catch (e) {
      err = e.message;
    } finally {
      busy = false;
    }
  }

  onMount(() => {
    refresh();
    loadCountries();
    // GPS-Detection ändert sich selten, alle 30 s reicht
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  });

  function gpsStatusText(mode) {
    if (mode === 3) return 'Fix (3D)';
    if (mode === 2) return 'Fix (2D)';
    return 'kein Fix';
  }
</script>

<div class="card" class:dx={loc?.current_country} class:mismatch={loc?.mismatch}>
  <header>
    <h3>📍 Operating-Location</h3>
    {#if loc?.current_country}
      <span class="badge dx-badge">{loc.tx_callsign}</span>
    {:else}
      <span class="badge home-badge">🏠 {loc?.tx_callsign ?? '—'}</span>
    {/if}
  </header>

  {#if err}
    <div class="err">⚠ {err}</div>
  {/if}

  {#if loc}
    <div class="grid">
      <div class="row">
        <span class="lbl">Aktuell</span>
        <span class="val">
          {#if loc.current_country}
            🌐 {loc.current_country_name} ({loc.current_country})
          {:else}
            🏠 {loc.home_country_name} (Heimat)
          {/if}
        </span>
      </div>
      <div class="row">
        <span class="lbl">GPS</span>
        <span class="val">
          {gpsStatusText(loc.gps_fix_mode)}
          {#if loc.gps_detected_country}
            · in {loc.gps_detected_name} ({loc.gps_detected_country})
          {/if}
        </span>
      </div>
      {#if loc.effective_max_power_w != null}
        <div class="row">
          <span class="lbl">CEPT-Power-Cap</span>
          <span class="val">{loc.effective_max_power_w} W</span>
        </div>
      {/if}
    </div>

    {#if loc.cept_lock_reason}
      <div class="banner red">🚫 {loc.cept_lock_reason}</div>
    {/if}

    {#if loc.mismatch}
      <div class="banner orange">
        ⚠️ {loc.mismatch_reason}
        <div class="banner-actions">
          {#if loc.gps_detected_country && loc.gps_detected_country !== loc.home_country}
            <button onclick={() => setCountry(loc.gps_detected_country)} disabled={busy}>
              Auf {loc.gps_detected_country} umstellen
            </button>
          {/if}
          {#if loc.current_country}
            <button onclick={() => setCountry(null)} disabled={busy}>
              Zurück auf Heimat
            </button>
          {/if}
        </div>
      </div>
    {/if}

    <div class="picker">
      <label>
        <span>Land manuell wählen</span>
        <select bind:value={loc.current_country} onchange={(e) => setCountry(e.target.value)}
                disabled={busy}>
          <option value="">🏠 Heimat ({loc.home_country})</option>
          {#each countries as c}
            {#if c.code !== loc.home_country}
              <option value={c.code}>{c.name} ({c.code}){c.cept_class_e_allowed ? '' : ' · ⚠️ keine Klasse-E'}</option>
            {/if}
          {/each}
        </select>
      </label>
      <label>
        <span>Funke als</span>
        <select value={loc.current_suffix ?? ''} onchange={(e) => setSuffix(e.target.value)}
                disabled={busy}>
          {#each SUFFIXES as s}
            <option value={s.v}>{s.label}</option>
          {/each}
        </select>
      </label>
    </div>
  {/if}
</div>

<style>
  .card {
    background: var(--panel); border-radius: 8px; padding: 0.7rem;
    border-left: 3px solid #475569;
  }
  .card.dx { border-left-color: #38bdf8; }
  .card.mismatch { border-left-color: #f59e0b; }
  header { display: flex; justify-content: space-between; align-items: center;
           margin-bottom: 0.5rem; }
  h3 { margin: 0; color: var(--accent); font-size: 0.95rem; }
  .badge { padding: 0.15em 0.55em; border-radius: 4px;
           font-family: ui-monospace, monospace; font-size: 0.9rem; font-weight: 700; }
  .badge.dx-badge { background: rgba(56, 189, 248, 0.25); color: #38bdf8; }
  .badge.home-badge { background: rgba(34, 197, 94, 0.18); color: #22c55e; }
  .grid { display: flex; flex-direction: column; gap: 0.25rem; margin-bottom: 0.4rem; }
  .row { display: grid; grid-template-columns: 8rem 1fr; gap: 0.5rem;
         font-size: 0.85rem; }
  .lbl { color: #94a3b8; }
  .val { color: var(--fg); font-family: ui-monospace, monospace; }
  .banner { padding: 0.5rem 0.7rem; border-radius: 5px; margin: 0.4rem 0;
            font-size: 0.85rem; }
  .banner.orange { background: rgba(245, 158, 11, 0.15); color: #fbbf24;
                   border-left: 3px solid #f59e0b; }
  .banner.red    { background: rgba(239, 68, 68, 0.18); color: #fca5a5;
                   border-left: 3px solid #ef4444; font-weight: 600; }
  .banner-actions { margin-top: 0.4rem; display: flex; gap: 0.4rem; flex-wrap: wrap; }
  .banner-actions button {
    background: #1e293b; color: var(--fg); border: 1px solid #334155;
    padding: 0.3rem 0.7rem; border-radius: 4px; font-size: 0.8rem;
    cursor: pointer;
  }
  .banner-actions button:hover { background: #334155; }
  .banner-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
  .picker { margin-top: 0.4rem; }
  .picker label { display: flex; flex-direction: column; gap: 0.25rem; }
  .picker span { color: #94a3b8; font-size: 0.8rem; }
  .picker select {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    padding: 0.35rem; border-radius: 4px; font-size: 0.85rem;
    font-family: ui-monospace, monospace;
  }
  .err { color: #ef4444; font-size: 0.85rem; padding: 0.3rem 0; }
</style>
