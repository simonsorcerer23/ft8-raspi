<script>
  // v0.20.3 — Continent-Heatmap: welche UTC-Stunden sind laut eigener
  // QSO-DB typisch aktiv pro Kontinent (Top-50 % der Stunden). Aktuelle
  // Stunde hervorgehoben.
  import { onMount } from 'svelte';
  import { t } from '../lib/i18n.svelte.js';
  import { api } from '../lib/api.js';

  let byContinent = $state({});
  let currentHour = $state(0);
  let totalBuckets = $state(0);

  const CONTINENTS = ['EU', 'NA', 'SA', 'AS', 'AF', 'OC'];

  async function refresh() {
    try {
      const r = await api.activeHours();
      byContinent = r.by_continent ?? {};
      currentHour = r.current_utc_hour ?? 0;
      totalBuckets = r.total_active_buckets ?? 0;
    } catch { /* ignore */ }
  }

  onMount(() => {
    refresh();
    const t = setInterval(refresh, 60_000);
    return () => clearInterval(t);
  });

  function isActive(continent, hour) {
    const hours = byContinent[continent];
    return Array.isArray(hours) && hours.includes(hour);
  }
</script>

<div class="wrap">
  <h3>{t('chart.active_hours')}</h3>
  {#if totalBuckets === 0}
    <p class="empty">Noch keine ausreichende QSO-Historie für die Auswertung.</p>
  {:else}
    <div class="grid">
      <div class="hour-row header">
        <div class="cont-label"></div>
        {#each Array(24) as _, h}
          <div class="hour-label" class:now={h === currentHour}>{h}</div>
        {/each}
      </div>
      {#each CONTINENTS as cont}
        {#if byContinent[cont]?.length}
          <div class="hour-row">
            <div class="cont-label">{cont}</div>
            {#each Array(24) as _, h}
              <div class="cell"
                   class:active={isActive(cont, h)}
                   class:now={h === currentHour}
                   title="{cont} {h}:00 UTC{isActive(cont, h) ? ' — aktive Stunde' : ''}"></div>
            {/each}
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</div>

<style>
  .wrap { background: var(--panel); border-radius: 8px; padding: 0.7rem; }
  h3 { margin: 0 0 0.7rem; color: var(--accent); font-size: 0.95rem; }
  .empty { color: #94a3b8; font-style: italic; }
  .grid { display: flex; flex-direction: column; gap: 3px; }
  .hour-row { display: grid;
              grid-template-columns: 2.5rem repeat(24, 1fr);
              gap: 2px; align-items: center; }
  .cont-label { color: #cbd5e1; font-size: 0.8rem; font-weight: 600;
                font-family: ui-monospace, monospace; }
  .hour-label { color: #64748b; font-size: 0.6rem; text-align: center;
                font-family: ui-monospace, monospace; }
  .hour-label.now { color: #ec4899; font-weight: 700; }
  .cell { height: 1.1rem; background: rgba(148, 163, 184, 0.08);
          border-radius: 2px; }
  .cell.active { background: var(--accent); }
  .cell.now { outline: 2px solid #ec4899; outline-offset: -1px; }
</style>
