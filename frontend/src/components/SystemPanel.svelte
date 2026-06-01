<script>
  // Pi-Status-Panel: CPU-Temp, Load, RAM, Disk, Uptime, Throttling.
  // Auto-refresh alle 30s — Werte ändern sich nur langsam, kein Bedarf
  // für SSE oder höhere Frequenz.
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { t } from '../lib/i18n.svelte.js';

  let info = $state(null);
  let lastError = $state(null);

  async function refresh() {
    try {
      info = await api.systemInfo();
      lastError = null;
    } catch (e) {
      lastError = e?.message ?? String(e);
    }
  }

  onMount(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  });

  function formatUptime(s) {
    if (!s) return '–';
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  function tempColor(t) {
    if (t == null) return 'var(--muted)';
    if (t < 60) return '#22c55e';   // grün — entspannt
    if (t < 75) return '#fbbf24';   // gelb — warm
    return '#ef4444';               // rot — heiß, throttle-Gefahr
  }

  function loadColor(l, cores = 4) {
    // Pi5 hat 4 Cores. Load 4.0 = 100% Auslastung. Wir warnen ab 2.0 (=50%).
    if (l == null) return 'var(--muted)';
    const r = l / cores;
    if (r < 0.5) return '#22c55e';
    if (r < 0.8) return '#fbbf24';
    return '#ef4444';
  }

  function memColor(used, total) {
    if (!total) return 'var(--muted)';
    const r = used / total;
    if (r < 0.5) return '#22c55e';
    if (r < 0.8) return '#fbbf24';
    return '#ef4444';
  }

  function diskColor(used, total) {
    if (!total) return 'var(--muted)';
    const r = used / total;
    if (r < 0.7) return '#22c55e';
    if (r < 0.9) return '#fbbf24';
    return '#ef4444';
  }
</script>

<div class="wrap">
  <div class="head">
    <h2>{t('system.title')}</h2>
    {#if info?.pi_model}
      <span class="model">{info.pi_model}</span>
    {/if}
  </div>

  {#if lastError}
    <div class="err">{t('common.error')}: {lastError}</div>
  {/if}

  {#if info}
    <div class="grid">
      <div class="cell">
        <div class="lbl">CPU-Temp</div>
        <div class="val" style="color: {tempColor(info.cpu_temp_c)}">
          {info.cpu_temp_c != null ? `${info.cpu_temp_c.toFixed(1)}°C` : '–'}
        </div>
      </div>
      <div class="cell">
        <div class="lbl">Load (1m)</div>
        <div class="val" style="color: {loadColor(info.cpu_load_1m)}">
          {info.cpu_load_1m.toFixed(2)}
        </div>
        <div class="sub">5m: {info.cpu_load_5m.toFixed(2)} · 15m: {info.cpu_load_15m.toFixed(2)}</div>
      </div>
      <div class="cell">
        <div class="lbl">RAM</div>
        <div class="val" style="color: {memColor(info.mem_used_mb, info.mem_total_mb)}">
          {info.mem_used_mb}<small>/{info.mem_total_mb} MB</small>
        </div>
        <div class="sub">{Math.round(100 * info.mem_used_mb / Math.max(1, info.mem_total_mb))}% belegt</div>
      </div>
      <div class="cell">
        <div class="lbl">Disk</div>
        <div class="val" style="color: {diskColor(info.disk_used_gb, info.disk_total_gb)}">
          {info.disk_used_gb}<small>/{info.disk_total_gb} GB</small>
        </div>
        <div class="sub">{Math.round(100 * info.disk_used_gb / Math.max(1, info.disk_total_gb))}% belegt</div>
      </div>
      <div class="cell">
        <div class="lbl">Uptime</div>
        <div class="val">{formatUptime(info.uptime_s)}</div>
      </div>
      <div class="cell">
        <div class="lbl">Throttle</div>
        {#if info.throttled_hex == null}
          <div class="val muted">n/a</div>
        {:else if info.throttled_healthy}
          <div class="val ok">✓ OK</div>
          <div class="sub mono">{info.throttled_hex}</div>
        {:else}
          <div class="val bad">⚠ ACTIVE</div>
          <div class="sub mono">{info.throttled_hex}</div>
        {/if}
      </div>
    </div>
  {:else}
    <div class="loading">Lade …</div>
  {/if}
</div>

<style>
  .wrap {
    background: var(--bg2, #0f172a);
    border: 1px solid var(--border, #1e293b);
    border-radius: 6px;
    padding: 0.75rem;
    margin-top: 0.75rem;
  }
  .head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 0.5rem; }
  .head h2 { margin: 0; font-size: 0.95rem; color: var(--accent, #38bdf8); }
  .model { font-size: 0.75rem; color: var(--muted, #64748b); }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 0.5rem;
  }
  .cell {
    background: #0b1220;
    border-radius: 4px;
    padding: 0.4rem 0.5rem;
  }
  .lbl { font-size: 0.65rem; color: var(--muted, #64748b); text-transform: uppercase; letter-spacing: 0.5px; }
  .val { font-size: 1.05rem; font-weight: bold; margin-top: 0.15rem; }
  .val small { font-size: 0.7rem; font-weight: normal; color: var(--muted, #64748b); }
  .sub { font-size: 0.7rem; color: var(--muted, #64748b); margin-top: 0.1rem; }
  .mono { font-family: monospace; }
  .ok { color: #22c55e; }
  .bad { color: #ef4444; }
  .muted { color: var(--muted, #64748b); }
  .err { color: #ef4444; padding: 0.5rem; font-size: 0.85rem; }
  .loading { color: var(--muted, #64748b); padding: 0.5rem; }
</style>
