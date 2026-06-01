<script>
  // Space-Weather-Header: aktuelle Solar-/Geomagnetik-Indizes mit
  // erklärenden Tooltips. Werte kommen von hamqsl.com, alle 30 min
  // refreshed (Quelle updated ~3h cycle).
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { t } from '../lib/i18n.svelte.js';

  let data = $state({ available: false });

  async function refresh() {
    try {
      data = await api.get('/solar');
    } catch { data = { available: false }; }
  }

  onMount(() => {
    refresh();
    const t = setInterval(refresh, 30 * 60_000);
    return () => clearInterval(t);
  });

  function sfiQuality(s) {
    if (s == null) return { color: '#94a3b8', label: '—' };
    if (s < 80)  return { color: '#ef4444', label: t('solar.q_poor') };
    if (s < 120) return { color: '#fbbf24', label: t('solar.q_moderate') };
    if (s < 180) return { color: '#22c55e', label: t('solar.q_good') };
    return { color: '#38bdf8', label: t('solar.q_top') };
  }
  function kQuality(k) {
    if (k == null) return { color: '#94a3b8', label: '—' };
    if (k <= 2) return { color: '#22c55e', label: t('solar.q_calm') };
    if (k <= 3) return { color: '#fbbf24', label: t('solar.q_unsettled') };
    if (k <= 4) return { color: '#f59e0b', label: t('solar.q_active') };
    return { color: '#ef4444', label: t('solar.storm') };
  }
  function aQuality(a) {
    if (a == null) return { color: '#94a3b8', label: '—' };
    if (a <= 15)  return { color: '#22c55e', label: t('solar.q_calm') };
    if (a <= 30)  return { color: '#fbbf24', label: t('solar.q_elevated') };
    return { color: '#ef4444', label: t('solar.q_disturbed') };
  }
  function snQuality(n) {
    if (n == null) return { color: '#94a3b8', label: '—' };
    if (n < 30)   return { color: '#ef4444', label: t('solar.q_few') };
    if (n < 80)   return { color: '#fbbf24', label: t('solar.q_moderate2') };
    if (n < 150)  return { color: '#22c55e', label: t('solar.q_many') };
    return { color: '#38bdf8', label: t('solar.q_maximum') };
  }
  function xQuality(x) {
    if (!x || x === 'A0.0') return { color: '#22c55e', label: t('solar.q_calm') };
    const cls = x[0];
    if (cls === 'A' || cls === 'B') return { color: '#22c55e', label: t('solar.q_calm') };
    if (cls === 'C') return { color: '#fbbf24', label: t('solar.q_mild') };
    if (cls === 'M') return { color: '#f59e0b', label: t('solar.q_flare') };
    if (cls === 'X') return { color: '#ef4444', label: t('solar.q_strong_flare') };
    return { color: '#94a3b8', label: t('solar.q_unknown') };
  }

  const sfiTip = (s) =>
    t('solar.tip_sfi', { val: s ?? '?', label: sfiQuality(s).label });
  const kTip = (k) =>
    t('solar.tip_k', { val: k ?? '?', label: kQuality(k).label });
  const aTip = (a) =>
    t('solar.tip_a', { val: a ?? '?', label: aQuality(a).label });
  const snTip = (n) =>
    t('solar.tip_sn', { val: n ?? '?', label: snQuality(n).label });
  const xTip = (x) =>
    t('solar.tip_x', { val: x ?? '?', label: xQuality(x).label });
</script>

{#if data.available}
  <div class="solar" title={t('solar.tooltip')}>
    <div class="cell" style="color: {sfiQuality(data.sfi).color}" title={sfiTip(data.sfi)}>
      <span class="icon">☀️</span>
      <span class="lbl">SFI</span>
      <strong>{data.sfi ?? '?'}</strong>
    </div>
    <div class="cell" style="color: {kQuality(data.k_index).color}" title={kTip(data.k_index)}>
      <span class="icon">🧲</span>
      <span class="lbl">K</span>
      <strong>{data.k_index ?? '?'}</strong>
    </div>
    <div class="cell" style="color: {aQuality(data.a_index).color}" title={aTip(data.a_index)}>
      <span class="icon">📅</span>
      <span class="lbl">A</span>
      <strong>{data.a_index ?? '?'}</strong>
    </div>
    <div class="cell" style="color: {snQuality(data.sunspots).color}" title={snTip(data.sunspots)}>
      <span class="icon">⚫</span>
      <span class="lbl">SN</span>
      <strong>{data.sunspots ?? '?'}</strong>
    </div>
    {#if data.x_ray}
      <div class="cell" style="color: {xQuality(data.x_ray).color}" title={xTip(data.x_ray)}>
        <span class="icon">⚡</span>
        <span class="lbl">X</span>
        <strong>{data.x_ray}</strong>
      </div>
    {/if}
  </div>
{/if}

<style>
  .solar {
    display: flex; gap: 0.6rem;
    background: rgba(15,23,42,0.7); border: 1px solid #334155;
    border-radius: 8px; padding: 0.4rem 0.7rem;
    font-size: 0.9rem;
    cursor: help;
  }
  .cell {
    display: flex; align-items: center; gap: 0.3rem;
    line-height: 1;
    cursor: help;
  }
  .icon { font-size: 1.05rem; }
  .lbl {
    color: #94a3b8;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
  }
  .cell strong {
    font-family: ui-monospace, monospace;
    font-weight: 700;
    font-size: 1.05rem;
  }
  @media (max-width: 640px) {
    .solar { gap: 0.4rem; padding: 0.3rem 0.5rem; font-size: 0.8rem; }
    .lbl { display: none; }    /* nur Icon + Wert auf Mobile */
    .icon { font-size: 0.95rem; }
    .cell strong { font-size: 0.95rem; }
  }
</style>
