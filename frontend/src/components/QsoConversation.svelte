<script>
  // Live transcript of what we're sending and receiving in the current QSO,
  // plus a "next action" hint so you always know what comes next.
  import { onMount } from 'svelte';
  import { fmtUtcTime } from '../lib/time.js';

  let conv = $state({ op_mode: 'off', state: 'IDLE', entries: [],
                      next_action_hint: null });

  async function refresh() {
    try {
      const r = await fetch('/api/qso/conversation');
      conv = await r.json();
    } catch { /* ignore */ }
  }

  onMount(() => {
    refresh();
    const t = setInterval(refresh, 3_000);  // poll every 3s
    return () => clearInterval(t);
  });

  const shortTime = fmtUtcTime;

  function kindIcon(k) {
    return ({
      cq: '📢', respond_grid: '📍', respond_report: '📊',
      r_report: '✓', rr73: '🤝',
    }[k] ?? '→');
  }
</script>

<div class="wrap">
  <header>
    <h2>Live-Konversation</h2>
    {#if conv.partner_call}
      <div class="partner">
        <strong>📡 {#if conv.partner_flag}<span class="flag" title={conv.partner_call}>{conv.partner_flag}</span> {/if}{conv.partner_call}</strong>
        {#if conv.partner_grid}<span class="grid">{conv.partner_grid}</span>{/if}
        {#if conv.our_snr_sent != null}<span class="snr">SNR↑ {conv.our_snr_sent > 0 ? '+' : ''}{conv.our_snr_sent}</span>{/if}
        {#if conv.partner_snr_received != null}<span class="snr">SNR↓ {conv.partner_snr_received > 0 ? '+' : ''}{conv.partner_snr_received}</span>{/if}
        {#if conv.started_at}<span class="duration">seit {shortTime(conv.started_at)}</span>{/if}
      </div>
    {/if}
  </header>

  {#if conv.entries.length === 0}
    <p class="empty">Noch keine Aktivität — wenn du CQ oder Antworten startest, taucht hier alles auf.</p>
  {:else}
    <div class="transcript">
      {#each conv.entries as e, i (i + '-' + e.message)}
        <div class="entry {e.direction}">
          <span class="time">{shortTime(e.ts)}</span>
          <span class="arrow">{e.direction === 'tx' ? '↑' : '↓'}</span>
          <span class="icon">{kindIcon(e.kind)}</span>
          {#if e.mf_mfnr}<span class="mf-badge" title="Marinefunker MF #{e.mf_mfnr}">⚓</span>{/if}
          <span class="msg">{e.message}</span>
        </div>
      {/each}
    </div>
  {/if}

  {#if conv.next_action_hint}
    <div class="next">
      <strong>Nächste Aktion:</strong> {conv.next_action_hint}
    </div>
  {/if}
</div>

<style>
  .wrap {
    background: var(--panel); border-radius: 8px; padding: 0.6rem;
  }
  header { margin-bottom: 0.5rem; }
  h2 { margin: 0; color: var(--accent); font-size: 1rem; }
  .partner {
    display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;
    margin-top: 0.3rem; font-family: ui-monospace, monospace;
    font-size: 0.85rem;
  }
  .partner strong { color: var(--accent); font-size: 1rem; }
  .grid { background: rgba(34,197,94,0.2); color: #22c55e;
          padding: 0.05em 0.4em; border-radius: 3px; font-size: 0.75rem; }
  .snr  { background: rgba(251,191,36,0.15); color: #fbbf24;
          padding: 0.05em 0.4em; border-radius: 3px; font-size: 0.75rem; }
  .duration { color: #94a3b8; font-size: 0.75rem; }
  .empty { color: #94a3b8; font-style: italic; padding: 0.5rem 0; }

  .transcript {
    max-height: 22vh; overflow-y: auto; padding: 0.3rem;
    background: #0b1220; border: 1px solid #1e293b; border-radius: 6px;
    font-family: ui-monospace, monospace; font-size: 0.85rem;
  }
  .entry {
    display: grid; grid-template-columns: 5rem 1rem 1.3rem 1fr;
    gap: 0.3rem; padding: 0.15rem 0;
    border-bottom: 1px dotted #1e293b;
  }
  .entry:last-child { border-bottom: none; }
  .entry.tx .arrow { color: #38bdf8; font-weight: 700; }
  .entry.rx .arrow { color: #fbbf24; font-weight: 700; }
  .entry.tx .msg { color: #cbd5e1; }
  .entry.rx .msg { color: var(--fg); font-weight: 600; }
  .mf-badge { color: #5eead4; margin-right: 0.25em; cursor: help; }
  .time { color: #64748b; font-size: 0.75rem; }
  .icon { text-align: center; }

  .next {
    margin-top: 0.5rem; padding: 0.5rem;
    background: rgba(56,189,248,0.08);
    border-left: 3px solid var(--accent);
    border-radius: 4px; font-size: 0.85rem; color: #cbd5e1;
  }
  .next strong { color: var(--accent); }
</style>
