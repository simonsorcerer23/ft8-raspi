<script>
  /**
   * SWR-Trend — Liniendiagramm mit Punkten pro QSO.
   *
   * Liefert aus /api/stats/swr-trend die per-QSO-SWR-Werte und zeichnet
   * sie als SVG-Path mit Datenpunkten. Hartcodierte Threshold-Linie bei
   * 2.0 (= OperatingConfig.swr_max-Default) als rote Strichlinie.
   *
   * Achsen: X = Zeit (relative Tick-Labels, "vor X h"), Y = SWR.
   * Kein Chart-Lib — reines SVG, ~150 Zeilen, fügt 0 KB Dependencies.
   */
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';

  import { utcMillis, fmtUtcDateTime } from '../lib/time.js';

  let { hours = 24 } = $props();

  let data = $state(null);
  let loading = $state(true);
  let error = $state(null);
  // Hover-Tooltip-State.
  let hoverPoint = $state(null);
  let hoverX = $state(0);
  let hoverY = $state(0);

  // Layout-Konstanten — SVG-Box ist 100×40 viewBox-units, CSS skaliert.
  const W = 100, H = 40;
  const PAD_L = 8, PAD_R = 2, PAD_T = 3, PAD_B = 6;
  const PLOT_W = W - PAD_L - PAD_R;
  const PLOT_H = H - PAD_T - PAD_B;

  // SWR-Y-Range: 1.0 (perfekt) bis max(2.5, höchster Wert + 0.2)
  const Y_MIN = 1.0;
  let yMax = $state(2.5);
  let threshold = $state(2.0);
  let path = $state('');
  let points = $state([]);
  let xTicks = $state([]);
  let yTicks = $state([]);

  async function load() {
    loading = true; error = null;
    try {
      const r = await api.swrTrend(hours);
      data = r.points;
      threshold = r.threshold ?? 2.0;
      rebuild();
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  function rebuild() {
    if (!data || data.length === 0) { path = ''; points = []; return; }
    const swrs = data.map(p => p.swr);
    yMax = Math.max(threshold + 0.3, Math.max(...swrs) + 0.2, 2.0);
    const tMin = utcMillis(data[0].ts);
    const tMax = utcMillis(data[data.length - 1].ts);
    const tRange = Math.max(1, tMax - tMin);
    const xFor = (ts) => PAD_L + ((utcMillis(ts) - tMin) / tRange) * PLOT_W;
    const yFor = (swr) => PAD_T + ((yMax - swr) / (yMax - Y_MIN)) * PLOT_H;
    points = data.map(p => ({ ...p, x: xFor(p.ts), y: yFor(p.swr) }));
    path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ');

    // X-Achse: 4 evenly-spaced Ticks
    xTicks = [];
    for (let i = 0; i <= 4; i++) {
      const ts = tMin + (tRange * i / 4);
      const ageH = (Date.now() - ts) / 3_600_000;
      const label = ageH < 1 ? 'jetzt'
                  : ageH < 24 ? `-${ageH.toFixed(0)}h`
                  : `-${(ageH / 24).toFixed(1)}d`;
      xTicks.push({ x: PAD_L + (PLOT_W * i / 4), label });
    }
    // Y-Achse: SWR-Werte 1.0, 1.5, 2.0, ...
    yTicks = [];
    for (let v = 1.0; v <= yMax + 0.01; v += 0.5) {
      yTicks.push({ y: PAD_T + ((yMax - v) / (yMax - Y_MIN)) * PLOT_H, label: v.toFixed(1) });
    }
  }

  function onPointHover(p, ev) {
    hoverPoint = p;
    const rect = ev.target.ownerSVGElement.getBoundingClientRect();
    const svgX = (p.x / W) * rect.width + rect.left;
    const svgY = (p.y / H) * rect.height + rect.top;
    hoverX = svgX;
    hoverY = svgY;
  }
  function onPointLeave() { hoverPoint = null; }

  onMount(load);
</script>

<div class="swr-trend">
  <div class="head">
    <h3>SWR-Trend</h3>
    <select bind:value={hours} onchange={load}>
      <option value={3}>letzte 3h</option>
      <option value={12}>letzte 12h</option>
      <option value={24}>letzte 24h</option>
      <option value={72}>letzte 3 Tage</option>
      <option value={168}>letzte Woche</option>
    </select>
  </div>

  {#if loading}
    <p class="muted">lädt…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !data || data.length === 0}
    <p class="muted">Noch keine QSOs mit SWR-Daten in diesem Zeitraum.</p>
  {:else}
    <svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" class="chart">
      <!-- Grid + Y-Achse -->
      {#each yTicks as t}
        <line x1={PAD_L} x2={W - PAD_R} y1={t.y} y2={t.y}
              stroke="#1e293b" stroke-width="0.15" />
        <text x={PAD_L - 0.5} y={t.y + 0.6} text-anchor="end"
              font-size="2" fill="#64748b">{t.label}</text>
      {/each}
      <!-- X-Achse -->
      {#each xTicks as t}
        <text x={t.x} y={H - 1} text-anchor="middle"
              font-size="2" fill="#64748b">{t.label}</text>
      {/each}
      <!-- Threshold-Linie (SWR-Max) -->
      {#if threshold <= yMax}
        {@const ty = PAD_T + ((yMax - threshold) / (yMax - Y_MIN)) * PLOT_H}
        <line x1={PAD_L} x2={W - PAD_R} y1={ty} y2={ty}
              stroke="#ef4444" stroke-width="0.2" stroke-dasharray="0.8,0.4" />
        <text x={W - PAD_R - 0.5} y={ty - 0.5} text-anchor="end"
              font-size="1.8" fill="#ef4444">Limit {threshold}</text>
      {/if}
      <!-- Linie -->
      <path d={path} stroke="#60a5fa" stroke-width="0.3" fill="none" />
      <!-- Datenpunkte (klickbare Hover-Targets) -->
      {#each points as p}
        <circle cx={p.x} cy={p.y} r="0.6"
                fill={p.swr >= threshold ? '#ef4444' : '#60a5fa'}
                role="button" tabindex="0"
                onmouseenter={(e) => onPointHover(p, e)}
                onmouseleave={onPointLeave} />
      {/each}
    </svg>
    {#if hoverPoint}
      <div class="tip" style="left: {hoverX}px; top: {hoverY - 60}px;">
        <strong>{hoverPoint.call}</strong> · {hoverPoint.band}<br/>
        SWR <strong>{hoverPoint.swr}</strong> · {hoverPoint.power_w ?? '?'}W<br/>
        <span class="muted">{fmtUtcDateTime(hoverPoint.ts)}</span>
      </div>
    {/if}
    <div class="legend">
      <span class="dot blue"></span> SWR-avg pro QSO
      <span class="dash"></span> Grenzwert {threshold}
      · {data.length} QSO{data.length === 1 ? '' : 's'}
    </div>
  {/if}
</div>

<style>
  .swr-trend {
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
