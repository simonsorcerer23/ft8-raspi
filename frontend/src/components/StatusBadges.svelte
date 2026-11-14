<script>
  // Compact status row — GPS, Time, Rig, Audio, State-Machine, System.
  // Each badge is a tiny pill with state colour. Ein Klick klappt darunter
  // auf, WARUM das Badge diese Farbe hat, samt der Werte aus /api/healthcheck.
  // Bis zum 14.11.2026 schrieb der Klick nur in die Browser-Konsole (Audit F5).
  import { healthStore } from '../lib/stores.svelte.js';
  import { t } from '../lib/i18n.svelte.js';

  const KEYS = ['time', 'gps', 'rig', 'audio', 'statemachine', 'system'];

  const sections = $derived(healthStore.value.sections || {});
  let open = $state(null);
  const openSection = $derived(open ? sections[open] : null);
  const statusLabel = $derived({
    ok: t('health.status.ok'), warn: t('health.status.warn'),
    fail: t('health.status.fail'), unknown: t('health.status.unknown'),
  });

  function statusClass(s) {
    if (!s) return 'unknown';
    if (s.status === 'ok')   return 'ok';
    if (s.status === 'warn') return 'warn';
    if (s.status === 'fail') return 'fail';
    return 'unknown';
  }

  // Der Grund in Worten. Die Schwellen stehen in web/routes/healthcheck.py;
  // hier wird nur benannt, welche davon gerissen ist.
  function reason(key, s) {
    if (!s) return t('health.no_data');
    const d = s.details ?? {};
    if (s.status === 'ok') return '';
    if (key === 'time') {
      if (d.offset_s == null) return t('health.time_no_chrony');
      return t('health.time_offset', { offset: Number(d.offset_s).toFixed(3) });
    }
    if (key === 'gps') {
      return d.fix_mode === 2 ? t('health.gps_2d') : t('health.gps_no_fix');
    }
    if (key === 'rig') return t('health.rig_silent');
    if (key === 'audio') {
      if (d.late_slot_count > 0) return t('health.audio_late', { n: d.late_slot_count });
      if (d.drift_status && d.drift_status !== 'ok') {
        return t('health.audio_drift', { n: d.last_drift_samples ?? '?' });
      }
      return d.slots_decoded == null ? t('health.audio_no_capture') : '';
    }
    if (key === 'statemachine') {
      return d.lock_reason ? t('health.sm_locked', { reason: d.lock_reason }) : t('health.sm_locked_plain');
    }
    if (key === 'system') {
      if (d.cpu_temp_c != null && d.cpu_temp_c > 80) {
        return t('health.sys_hot', { temp: Number(d.cpu_temp_c).toFixed(0) });
      }
      if (d.throttled_any) return t('health.sys_throttled', { hex: d.throttled ?? '?' });
    }
    return '';
  }

  function fmt(v) {
    if (v == null) return '—';
    if (typeof v === 'number' && !Number.isInteger(v)) return v.toFixed(3);
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }
</script>

<div class="row">
  {#each KEYS as key}
    {@const s = sections[key]}
    <button
      class="badge {statusClass(s)}"
      class:open={open === key}
      aria-expanded={open === key}
      onclick={() => { open = open === key ? null : key; }}
    >
      <span class="dot"></span>
      <span class="label">{key}</span>
    </button>
  {/each}
  {#if healthStore.error}
    <span class="stale">{t('health.stale', { err: healthStore.error })}</span>
  {/if}
</div>

{#if open}
  {@const why = reason(open, openSection)}
  <div class="detail {statusClass(openSection)}">
    <div class="head">
      <strong>{open}</strong>
      <span class="status">{statusLabel[statusClass(openSection)]}</span>
      {#if why}<span class="why">{why}</span>{/if}
    </div>
    <dl>
      {#each Object.entries(openSection?.details ?? {}) as [k, v]}
        <div><dt>{k}</dt><dd>{fmt(v)}</dd></div>
      {/each}
    </dl>
  </div>
{/if}

<style>
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    padding: 0.5rem;
    background: var(--panel);
    border-radius: 8px;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.3rem 0.7rem;
    border-radius: 999px;
    border: 1px solid #334155;
    background: #0b1220;
    color: var(--fg);
    font-size: 0.85rem;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .badge .dot {
    width: 0.65rem;
    height: 0.65rem;
    border-radius: 50%;
    background: #6b7280;
  }
  .badge.ok    .dot { background: var(--ok); }
  .badge.warn  .dot { background: #f59e0b; }
  .badge.fail  .dot { background: var(--danger); }
  .badge.unknown .dot { background: #6b7280; }
  .badge.open { border-color: var(--accent, #38bdf8); }
  .stale {
    align-self: center;
    font-size: 0.75rem;
    color: #fca5a5;
    font-family: ui-monospace, monospace;
  }
  .detail {
    margin-top: 0.3rem;
    padding: 0.5rem 0.7rem;
    background: var(--panel);
    border-radius: 8px;
    border-left: 4px solid #6b7280;
    font-size: 0.82rem;
  }
  .detail.ok   { border-left-color: var(--ok); }
  .detail.warn { border-left-color: #f59e0b; }
  .detail.fail { border-left-color: var(--danger); }
  .detail .head {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem 0.7rem;
    align-items: baseline;
  }
  .detail .head strong { text-transform: uppercase; letter-spacing: 0.05em; }
  .detail .status { color: #94a3b8; }
  .detail.warn .why { color: #fbbf24; }
  .detail.fail .why { color: #fca5a5; }
  dl {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
    gap: 0.15rem 1rem;
    margin: 0.4rem 0 0;
    font-family: ui-monospace, monospace;
    font-size: 0.75rem;
  }
  dl div { display: flex; justify-content: space-between; gap: 0.5rem; min-width: 0; }
  dt { color: #94a3b8; }
  dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
</style>
