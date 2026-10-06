<script>
  /**
   * Versorgungsspannung des Rigs — Empfang und Senden als zwei Linien.
   *
   * Daten aus /api/stats/versorgung. Der Abstand der beiden Linien ist der
   * Einbruch unter Last; waechst er ueber Wochen, altert das Netzteil oder
   * ein Stecker. Reines SVG wie SwrTrendChart.
   */
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { t } from '../lib/i18n.svelte.js';
  import { utcMillis, fmtUtcDateTime } from '../lib/time.js';

  let { hours = 24 } = $props();

  let data = $state(null);
  let loading = $state(true);
  let error = $state(null);
  let hoverPoint = $state(null);
  let hoverX = $state(0);
  let hoverY = $state(0);

  const W = 100, H = 40;
  const PAD_L = 8, PAD_R = 2, PAD_T = 3, PAD_B = 6;
  const PLOT_W = W - PAD_L - PAD_R;
  const PLOT_H = H - PAD_T - PAD_B;

  let vdMin = $state(12.0);
  let vdMax = $state(15.0);
  let yLo = $state(11.5);
  let yHi = $state(15.5);
  let rx = $state({ path: '', points: [] });
  let tx = $state({ path: '', points: [] });
  let xTicks = $state([]);
  let yTicks = $state([]);
  let einbruch = $state(null);

  const yFor = (v) => PAD_T + ((yHi - v) / (yHi - yLo)) * PLOT_H;

  async function load() {
    loading = true; error = null;
    try {
      const r = await api.versorgung(hours);
      data = r.points;
      vdMin = r.vd_min_v; vdMax = r.vd_max_v;
      rebuild();
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  function median(werte) {
    if (werte.length === 0) return null;
    const s = [...werte].sort((a, b) => a - b);
    return s[Math.floor(s.length / 2)];
  }

  function rebuild() {
    if (!data || data.length === 0) return;
    const vs = data.map(p => p.vd_v);
    yLo = Math.floor(Math.min(vdMin, ...vs) - 0.5);
    yHi = Math.ceil(Math.max(vdMax, ...vs) + 0.5);
    const tMin = utcMillis(data[0].ts);
    const tMax = utcMillis(data[data.length - 1].ts);
    const tRange = Math.max(1, tMax - tMin);
    const xFor = (ts) => PAD_L + ((utcMillis(ts) - tMin) / tRange) * PLOT_W;
    const reihe = (senden) => {
      const points = data.filter(p => p.senden === senden)
                         .map(p => ({ ...p, x: xFor(p.ts), y: yFor(p.vd_v) }));
      const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ');
      return { path, points };
    };
    rx = reihe(false);
    tx = reihe(true);
    const mRx = median(rx.points.map(p => p.vd_v));
    const mTx = median(tx.points.map(p => p.vd_v));
    einbruch = (mRx !== null && mTx !== null) ? (mRx - mTx) : null;

    xTicks = [];
    for (let i = 0; i <= 4; i++) {
      const ts = tMin + (tRange * i / 4);
      const ageH = (Date.now() - ts) / 3_600_000;
      const label = ageH < 1 ? '0h'
                  : ageH < 24 ? `-${ageH.toFixed(0)}h`
                  : `-${(ageH / 24).toFixed(1)}d`;
      xTicks.push({ x: PAD_L + (PLOT_W * i / 4), label });
    }
    yTicks = [];
    for (let v = yLo; v <= yHi + 0.01; v += 1) {
      yTicks.push({ y: yFor(v), label: v.toFixed(0) });
    }
  }

  function onPointHover(p, ev) {
    hoverPoint = p;
    const rect = ev.target.ownerSVGElement.getBoundingClientRect();
    hoverX = (p.x / W) * rect.width + rect.left;
    hoverY = (p.y / H) * rect.height + rect.top;
  }
  function onPointLeave() { hoverPoint = null; }

  onMount(load);
</script>

<div class="versorgung">
  <div class="head">
    <h3>{t('chart.versorgung')}</h3>
    <select bind:value={hours} onchange={load}>
      <option value={24}>{t('chart.last_24h')}</option>
      <option value={72}>{t('chart.last_3d')}</option>
      <option value={168}>{t('chart.last_week')}</option>
      <option value={720}>30 d</option>
    </select>
  </div>

  {#if loading}
    <p class="muted">{t('common.loading')}</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !data || data.length === 0}
    <p class="muted">{t('chart.no_versorgung')}</p>
  {:else}
    <svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" class="chart">
      {#each yTicks as tick}
        <line x1={PAD_L} x2={W - PAD_R} y1={tick.y} y2={tick.y}
              stroke="#1e293b" stroke-width="0.15" />
        <text x={PAD_L - 0.5} y={tick.y + 0.6} text-anchor="end"
              font-size="2" fill="#64748b">{tick.label}</text>
      {/each}
      {#each xTicks as tick}
        <text x={tick.x} y={H - 1} text-anchor="middle"
              font-size="2" fill="#64748b">{tick.label}</text>
      {/each}
      {#each [vdMin, vdMax] as grenze}
        <line x1={PAD_L} x2={W - PAD_R} y1={yFor(grenze)} y2={yFor(grenze)}
              stroke="#ef4444" stroke-width="0.2" stroke-dasharray="0.8,0.4" />
        <text x={W - PAD_R - 0.5} y={yFor(grenze) - 0.5} text-anchor="end"
              font-size="1.8" fill="#ef4444">{grenze} V</text>
      {/each}
      <path d={rx.path} stroke="#60a5fa" stroke-width="0.3" fill="none" />
      <path d={tx.path} stroke="#f59e0b" stroke-width="0.3" fill="none" />
      {#each [...rx.points, ...tx.points] as p}
        <circle cx={p.x} cy={p.y} r="0.5"
                fill={(p.vd_v > vdMax || p.vd_v < vdMin) ? '#ef4444' : (p.senden ? '#f59e0b' : '#60a5fa')}
                role="button" tabindex="0"
                onmouseenter={(e) => onPointHover(p, e)}
                onmouseleave={onPointLeave} />
      {/each}
    </svg>
    {#if hoverPoint}
      <div class="tip" style="left: {hoverX}px; top: {hoverY - 60}px;">
        <strong>{hoverPoint.vd_v} V</strong>
        {#if hoverPoint.id_a !== null && hoverPoint.senden} · {hoverPoint.id_a} A{/if}<br/>
        {hoverPoint.senden ? t('chart.vd_tx') : t('chart.vd_rx')}<br/>
        <span class="muted">{fmtUtcDateTime(hoverPoint.ts)}</span>
      </div>
    {/if}
    <div class="legend">
      <span class="dot blue"></span> {t('chart.vd_rx')}
      <span class="dot amber"></span> {t('chart.vd_tx')}
      <span class="dash"></span> {t('chart.vd_grenze')}
      {#if einbruch !== null}· {t('chart.vd_einbruch')} {einbruch.toFixed(1)} V{/if}
    </div>
  {/if}
</div>

<style>
  .versorgung {
    background: rgba(15,23,42,0.5); border: 1px solid #334155;
    border-radius: 8px; padding: 0.7rem;
    margin-top: 0.7rem;
    position: relative;
  }
  .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem; }
  .head h3 { margin: 0; font-size: 0.95rem; }
  .head select {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 4px; padding: 0.2rem 0.4rem; font-size: 0.85rem;
  }
  .chart { width: 100%; height: 12rem; display: block; }
  .legend { font-size: 0.75rem; color: #94a3b8; margin-top: 0.3rem;
            display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
  .dot { display: inline-block; width: 0.6rem; height: 0.6rem; border-radius: 50%; }
  .dot.blue { background: #60a5fa; }
  .dot.amber { background: #f59e0b; }
  .dash { display: inline-block; width: 1.2rem; height: 0; border-top: 1.5px dashed #ef4444; }
  .muted { color: #94a3b8; font-size: 0.85rem; margin: 0.3rem 0; }
  .err { color: var(--danger); font-size: 0.85rem; }
  .tip {
    position: fixed; pointer-events: none; z-index: 10;
    background: #0f172a; border: 1px solid #475569; color: var(--fg);
    padding: 0.4rem 0.6rem; border-radius: 6px; font-size: 0.8rem;
    white-space: nowrap; transform: translate(-50%, 0);
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  }
</style>
