<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { t } from '../lib/i18n.svelte.js';

  let stats = $state({ qso_today: 0, dxccs_today: 0, qso_7d: 0, qso_total: 0,
                       decodes_last_hour: 0, best_dx_today: null });
  let suggestions = $state([]);
  let currentBand = $state(null);

  async function refresh() {
    try { stats = await api.stats(); } catch { /* ignore */ }
    try {
      const r = await api.bandSuggestions();
      suggestions = r.suggestions;
      currentBand = r.current_band;
    } catch { /* ignore */ }
  }

  async function switchBand(band) {
    try { await api.switchBand(band); }
    catch (e) { console.warn(e); }
    refresh();
  }

  onMount(() => {
    refresh();
    const t = setInterval(refresh, 60_000);
    return () => clearInterval(t);
  });
</script>

<div class="wrap">
  <div class="head">
    <h2>{t('stats.today')}</h2>
    <a class="export" href={api.adifUrl()} download="dk9xr_ft8.adif">⬇ ADIF Export</a>
  </div>

  <div class="grid">
    <div class="card">
      <div class="big">{stats.qso_today}</div>
      <div class="lbl">{t('stats.qsos_today')}</div>
    </div>
    <div class="card">
      <div class="big">{stats.dxccs_today}</div>
      <div class="lbl">{t('stats.dxcc_today')}</div>
    </div>
    <div class="card">
      <div class="big">{stats.qso_7d}</div>
      <div class="lbl">{t('stats.qsos_7d')}</div>
    </div>
    <div class="card">
      <div class="big">{stats.decodes_last_hour}</div>
      <div class="lbl">{t('stats.decodes_h')}</div>
    </div>
  </div>

  {#if stats.best_dx_today}
    <div class="best-dx">
      <strong>{t('stats.best_dx_today')}:</strong>
      {stats.best_dx_today.call} ({stats.best_dx_today.grid}, {stats.best_dx_today.band})
      {#if stats.best_dx_today.distance_km_estimate}
        — ~{stats.best_dx_today.distance_km_estimate} km
      {/if}
    </div>
  {/if}

  {#if suggestions.length > 0}
    <h3>{t('stats.band_suggest')}</h3>
    <div class="bands">
      {#each suggestions.slice(0, 5) as s}
        <button
          class:current={s.current}
          class="band-btn"
          onclick={() => switchBand(s.band)}
          disabled={s.current}
          title={s.reason}
        >
          <span class="band-name">{s.band}</span>
          <span class="score" style="background: hsl({s.score * 1.2}, 70%, 45%)">{Math.round(s.score)}</span>
        </button>
      {/each}
    </div>
  {/if}
</div>

<style>
  .wrap { background: var(--panel); border-radius: 8px; padding: 0.7rem; }
  .head { display: flex; align-items: center; justify-content: space-between; }
  h2 { margin: 0; color: var(--accent); font-size: 1rem; }
  h3 { margin: 0.8rem 0 0.2rem; color: var(--accent); font-size: 0.85rem; }
  .export {
    color: var(--accent); text-decoration: none; font-size: 0.85rem;
    border: 1px solid #334155; border-radius: 4px; padding: 0.2rem 0.5rem;
  }
  .export:hover { background: #1e293b; }
  .grid {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.4rem;
    margin: 0.7rem 0;
  }
  .card {
    background: #0b1220; border: 1px solid #1e293b; border-radius: 6px;
    padding: 0.5rem; text-align: center;
  }
  .big { font-size: 1.4rem; font-weight: 700; color: var(--accent);
         font-family: ui-monospace, monospace; }
  .lbl { font-size: 0.75rem; color: #94a3b8; letter-spacing: 0.05em; }
  .best-dx { font-size: 0.85rem; color: #cbd5e1; margin: 0.3rem 0 0.7rem; }
  .hint { font-size: 0.75rem; color: #94a3b8; }
  .bands { display: flex; gap: 0.3rem; flex-wrap: wrap; margin-top: 0.4rem; }
  .band-btn {
    display: flex; align-items: center; gap: 0.4rem;
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 6px; padding: 0.3rem 0.6rem; cursor: pointer;
    font-family: ui-monospace, monospace; font-size: 0.85rem;
  }
  .band-btn:hover { border-color: var(--accent); }
  .band-btn.current { border-color: var(--accent); background: rgba(56,189,248,0.1); }
  .band-btn:disabled { opacity: 0.5; cursor: default; }
  .score {
    font-size: 0.7rem; color: white; padding: 0.1rem 0.4rem;
    border-radius: 999px; font-weight: 700;
  }
</style>
