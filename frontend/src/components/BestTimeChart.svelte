<script>
  // 24-hour histogram showing when QSOs typically happen per band.
  // No charting library — plain CSS bars. Saves ~50KB bundle.
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { t } from '../lib/i18n.svelte.js';

  let { band = '20m' } = $props();

  let buckets = $state([]);
  let maxCount = $derived(Math.max(1, ...buckets.map(b => b.count)));

  async function refresh() {
    try {
      const r = await api.bestTime(band);
      buckets = r.buckets;
    } catch { /* ignore */ }
  }

  onMount(refresh);
  $effect(() => { band; refresh(); });

  function currentHour() {
    return new Date().getUTCHours();
  }
</script>

<div class="wrap">
  <h3>{t('chart.best_time', { band })}</h3>
  {#if buckets.every(b => b.count === 0)}
    <p class="empty">{t('chart.no_qsos_band', { band })}</p>
  {:else}
    <div class="bars">
      {#each buckets as b}
        <div class="bar-wrap" class:now={b.utc_hour === currentHour()}
             title="{b.utc_hour}:00 UTC — {b.count} QSOs">
          <div class="bar" style="height: {Math.max(2, (b.count / maxCount) * 100)}%"></div>
          <div class="label">{b.utc_hour}</div>
        </div>
      {/each}
    </div>
    <div class="legend">{t('chart.legend_besttime')}</div>
  {/if}
</div>

<style>
  .wrap { background: var(--panel); border-radius: 8px; padding: 0.7rem; }
  h3 { margin: 0 0 0.7rem; color: var(--accent); font-size: 0.95rem; }
  h3 small { color: #94a3b8; font-weight: normal; font-size: 0.8rem; }
  .empty { color: #94a3b8; font-style: italic; }
  .bars {
    display: grid; grid-template-columns: repeat(24, 1fr);
    height: 8rem; gap: 2px; align-items: end;
  }
  .bar-wrap { display: flex; flex-direction: column; align-items: center;
              justify-content: flex-end; height: 100%; }
  .bar {
    width: 100%; background: var(--accent); border-radius: 2px 2px 0 0;
    transition: height 0.3s ease;
  }
  .bar-wrap.now .bar { background: #ec4899; }
  .label { color: #94a3b8; font-size: 0.6rem; margin-top: 0.2rem;
           font-family: ui-monospace, monospace; }
  .legend { color: #94a3b8; font-size: 0.75rem; margin-top: 0.3rem; }
</style>
