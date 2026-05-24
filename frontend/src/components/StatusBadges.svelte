<script>
  // Compact status row — GPS, Time, Rig, WLAN, SWR, ALC, Akku, Temp.
  // Each badge is a tiny pill with state colour. Tap a badge to see details.
  import { healthStore } from '../lib/stores.svelte.js';

  let { onDetail = () => {} } = $props();

  const sections = $derived(healthStore.value.sections || {});

  function statusClass(s) {
    if (!s) return 'unknown';
    if (s.status === 'ok')   return 'ok';
    if (s.status === 'warn') return 'warn';
    if (s.status === 'fail') return 'fail';
    return 'unknown';
  }
</script>

<div class="row">
  {#each ['time','gps','rig','audio','statemachine','system'] as key}
    {@const s = sections[key]}
    <button
      class="badge {statusClass(s)}"
      onclick={() => onDetail(key, s)}
      title={s?.details?.note ?? ''}
    >
      <span class="dot"></span>
      <span class="label">{key}</span>
    </button>
  {/each}
</div>

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
</style>
