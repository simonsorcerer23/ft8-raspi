<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { decodeStore, statusStore } from '../lib/stores.svelte.js';
  import { t } from '../lib/i18n.svelte.js';
  import { fmtUtcTime } from '../lib/time.js';

  let { onReply = () => {}, onTailEnd = () => {} } = $props();

  // v0.12.0 — RR73/RRR/73 am Ende der Message = Closing.
  // Wenn der Sender (call_from) jemand anderes ist als wir, koennen wir
  // ihn direkt nach seinem Closing als Tail-End anrufen.
  function isClosing(msg) {
    if (!msg) return false;
    const parts = msg.trim().split(/\s+/);
    if (parts.length === 0) return false;
    const tail = parts[parts.length - 1].toUpperCase();
    return tail === 'RR73' || tail === 'RRR' || tail === '73';
  }

  const items = $derived(decodeStore.items);
  let busy = $state(false);
  let onlyToMe = $state(false);
  // v0.20.3 Pile-Up-Set, alle 30 s gepollt. Decodes deren call_from drin
  // ist bekommen ein 🌪️-Badge — Picker laesst sie eh aus.
  let pileUpSet = $state(new Set());

  const myCall = $derived(statusStore.value.callsign ?? null);
  const visible = $derived(
    onlyToMe && myCall
      ? items.filter(d => d.call_to === myCall)
      : items
  );

  // Initial fetch of recent decodes from DB so the list isn't empty on tab
  // switch. Live updates land via SSE later (already wired in stores).
  onMount(async () => {
    try {
      const r = await api.decodes({ limit: 100 });
      decodeStore.setAll(r.decodes);
    } catch { /* ignore */ }
    async function refreshPileUp() {
      try {
        const r = await api.pileUp();
        pileUpSet = new Set((r.calls ?? []).map(c => c.toUpperCase()));
      } catch {}
    }
    refreshPileUp();
    const t = setInterval(async () => {
      try {
        const r = await api.decodes({ limit: 100 });
        decodeStore.setAll(r.decodes);
      } catch {}
    }, 15_000);
    const tp = setInterval(refreshPileUp, 30_000);
    return () => { clearInterval(t); clearInterval(tp); };
  });

  const shortTs = fmtUtcTime;
  function fmtSnr(s) {
    if (s === null || s === undefined) return '   ';
    return (s >= 0 ? '+' : '') + String(s).padStart(3, ' ');
  }

  async function blacklist(call) {
    busy = true;
    try { await api.blacklistAdd(call); }
    finally { busy = false; }
  }

  function doReply(d) {
    if (d.worked_before) {
      const ok = confirm(
        `${d.call_from} wurde schon gearbeitet (B4).\n` +
        `Trotzdem antworten?`
      );
      if (!ok) return;
    }
    onReply(d);
  }

  function doTailEnd(d) {
    if (d.worked_before) {
      const ok = confirm(
        `${d.call_from} wurde schon gearbeitet (B4).\n` +
        `Trotzdem Tail-End anrufen?`
      );
      if (!ok) return;
    }
    onTailEnd(d);
  }
</script>

<div class="wrap">
  <header>
    <h2>{t('dl.title')}</h2>
    <label class="filter">
      <input type="checkbox" bind:checked={onlyToMe}/>
      <span>{t('dl.only_me')}</span>
    </label>
  </header>
  {#if visible.length === 0}
    <p class="empty">
      {onlyToMe
        ? 'Keine Decodes an dich in dieser Liste.'
        : t('dl.empty')}
    </p>
  {:else}
    <ul>
      {#each visible as d (d.id ?? `${d.ts}-${d.message}`)}
        <li class:to-me={d.call_to && statusFromMe(d.call_to)}
            class:worked-b4={d.worked_before}
            class:blacklisted={d.blacklisted}
            class:new-dxcc={d.is_new_dxcc}
            class:new-grid={d.is_new_grid && !d.is_new_dxcc}
            class:new-grid-band={d.is_new_grid_on_band && !d.is_new_grid && !d.is_new_dxcc}>
          <span class="ts">{shortTs(d.ts)}</span>
          <span class="snr">{fmtSnr(d.snr_db)}dB</span>
          <span class="dt">{d.dt_s?.toFixed(1) ?? ' '}</span>
          <span class="freq">{d.freq_offset_hz ?? ''}</span>
          <span class="msg">
            {#if d.flag}<span class="flag" title={d.call_from ?? ''}>{d.flag}</span>{/if}
            {#if d.mf_mfnr}<span class="badge mf" title="Marinefunker MF #{d.mf_mfnr}">⚓MF</span>{/if}
            {#if d.is_new_dxcc}<span class="badge ndxcc" title="Neue DXCC-Entity">🏆DXCC</span>{/if}
            {#if d.is_new_grid && !d.is_new_dxcc}<span class="badge ngrid" title={t('dl.tip_newgrid')}>🆕Grid</span>{/if}
            {#if d.is_new_grid_on_band && !d.is_new_grid && !d.is_new_dxcc}<span class="badge ngridb" title={t('dl.tip_newgrid_band')}>🎯Band</span>{/if}
            {#if d.psk_heard_us}<span class="badge psk" title="laut PSK Reporter hat diese Station uns gehört → Asymmetrie-Pfad lohnt">📡PSK</span>{/if}
            {#if d.call_from && pileUpSet.has(d.call_from.toUpperCase())}<span class="badge pileup" title={t('dl.tip_pileup')}>🌪️Pile-Up</span>{/if}
            {#if d.worked_before}<span class="badge worked" title="schon gearbeitet">B4</span>{/if}
            {#if d.blacklisted}<span class="badge bl" title="auf Blacklist">⛔</span>{/if}
            {d.message}
          </span>
          <span class="actions">
            {#if d.call_from && d.message?.startsWith('CQ')}
              <button class="reply" onclick={() => doReply(d)}>Reply</button>
            {:else if d.call_from && d.call_from !== myCall && d.call_to !== myCall && isClosing(d.message)}
              <button class="tail-end" onclick={() => doTailEnd(d)}
                      title={t('dl.tip_tailend')}>🎯 Tail-End</button>
            {/if}
            {#if d.call_from && !d.blacklisted}
              <button class="bl-btn" title={t('dl.tip_blacklist')}
                      onclick={() => blacklist(d.call_from)} disabled={busy}>⛔</button>
            {/if}
          </span>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<script context="module">
  function statusFromMe() { return false; }  // future: compare against status.callsign
</script>

<style>
  .wrap { background: var(--panel); border-radius: 8px; padding: 0.6rem; }
  header { display: flex; align-items: center; justify-content: space-between;
           margin-bottom: 0.4rem; }
  h2 { margin: 0; font-size: 1rem; color: var(--accent); }
  .filter { display: flex; align-items: center; gap: 0.3rem; color: #94a3b8;
            font-size: 0.85rem; cursor: pointer; user-select: none; }
  .empty { color: #94a3b8; font-style: italic; margin: 0.5rem 0; }
  ul { list-style: none; padding: 0; margin: 0;
       font-family: ui-monospace, monospace; font-size: 0.85rem;
       max-height: 40vh; overflow-y: auto; }
  li {
    display: grid;
    grid-template-columns: 4.5rem 3.5rem 2rem 3rem 1fr auto;
    gap: 0.4rem; padding: 0.2rem 0.3rem;
    border-bottom: 1px solid #1e293b; align-items: center;
  }
  li.to-me        { background: rgba(56, 189, 248, 0.08); }
  li.worked-b4    { background: rgba(34, 197, 94, 0.05); }
  /* WSJT-Z-style highlights, ranked by rarity (DXCC > Grid > Grid-Band > B4).
   * Earlier classes win because we already gate is_new_grid on
   * !is_new_dxcc in the template, so only the highest applicable class
   * is on the li at a time. */
  li.new-dxcc      { background: rgba(217, 70, 239, 0.18); /* magenta */ }
  li.new-grid      { background: rgba(251, 191,  36, 0.16); /* amber  */ }
  li.new-grid-band { background: rgba(56, 189, 248, 0.14); /* cyan   */ }
  li.blacklisted  { opacity: 0.4; text-decoration: line-through; }
  .ts   { color: #94a3b8; }
  .snr  { color: #fbbf24; }
  .dt   { color: #64748b; }
  .freq { color: #64748b; }
  .msg  { color: var(--fg); }
  .flag { display: inline-block; margin-right: 0.35em; font-size: 0.95em;
          vertical-align: middle; }
  .badge { display: inline-block; padding: 0.05em 0.4em; border-radius: 4px;
           font-size: 0.7rem; font-weight: 700; margin-right: 0.3em;
           vertical-align: middle; }
  .badge.worked { background: rgba(34, 197, 94, 0.25); color: #22c55e; }
  .badge.bl     { background: rgba(239, 68, 68, 0.25); color: #ef4444; }
  .badge.ndxcc  { background: rgba(217, 70, 239, 0.30); color: #f0abfc; }
  .badge.ngrid  { background: rgba(251, 191, 36, 0.30); color: #fbbf24; }
  .badge.ngridb { background: rgba(56, 189, 248, 0.30); color: #38bdf8; }
  .badge.mf     { background: rgba(20, 184, 166, 0.30); color: #5eead4; }
  .badge.psk    { background: rgba(16, 185, 129, 0.30); color: #6ee7b7; }
  .badge.pileup { background: rgba(148, 163, 184, 0.30); color: #cbd5e1; }
  .actions { display: flex; gap: 0.25rem; }
  .reply {
    background: var(--accent); color: #0f172a; border: none;
    border-radius: 4px; padding: 0.15rem 0.5rem; font-size: 0.75rem;
    cursor: pointer; font-weight: 600;
  }
  /* v0.12.0 — Tail-End-Button erscheint statt Reply bei RR73/RRR/73. */
  .tail-end {
    background: #a855f7; color: #0f172a; border: none;
    border-radius: 4px; padding: 0.15rem 0.5rem; font-size: 0.75rem;
    cursor: pointer; font-weight: 600;
  }
  .tail-end:hover { background: #c084fc; }
  .bl-btn {
    background: transparent; color: var(--danger); border: 1px solid var(--danger);
    border-radius: 4px; padding: 0.1rem 0.3rem; font-size: 0.7rem; cursor: pointer;
  }
  .bl-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
