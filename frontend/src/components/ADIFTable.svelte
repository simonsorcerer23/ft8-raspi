<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { logStore } from '../lib/stores.svelte.js';

  const COLOURS = { worked: '#22c55e', heard: '#f59e0b', both: '#38bdf8',
                    new_dxcc: '#a78bfa' };

  onMount(() => {
    logStore.refresh();
    const t = setInterval(() => logStore.refresh(), 30_000);
    return () => clearInterval(t);
  });

  function shortTs(iso) {
    if (!iso) return '';
    return new Date(iso).toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
  }
  function fmtFreq(hz) {
    return hz ? (hz / 1000).toFixed(2) + ' kHz' : '';
  }
  function fmtRst(r) {
    if (r === null || r === undefined) return '';
    return (r >= 0 ? '+' : '') + r;
  }
  function extractPrefix(call) {
    // Best-effort DXCC-prefix: portable-prefix style ("9A/DK9XR" → "9A")
    // wins over plain ("DK9XR" → "DK"). Matches the backend prefix filter.
    const m = call.match(/^([A-Z0-9]{1,3})\//);
    if (m) return m[1];
    const n = call.match(/^([A-Z]{1,2}\d|\d[A-Z])/);
    return n ? n[1] : call.slice(0, 2);
  }

  const totalPages = $derived(Math.max(1, Math.ceil(logStore.total / logStore.pageSize)));
  function sortArrow(col) {
    if (logStore.sortBy !== col) return '';
    return logStore.sortDir === 'desc' ? ' ▼' : ' ▲';
  }

  const headers = [
    { col: 'qso_start',  label: 'Zeit (UTC)' },
    { col: 'call',       label: 'Call' },
    { col: 'band',       label: 'Band' },
    { col: 'mode',       label: 'Mode' },
    { col: 'freq_hz',    label: 'Frequenz' },
    { col: 'rst_sent',   label: 'RST↑' },
    { col: 'rst_rcvd',   label: 'RST↓' },
    { col: 'grid_rcvd',  label: 'Grid' },
    { col: 'my_power_w', label: 'P' },
    { col: 'swr_avg',    label: 'SWR' },
  ];
</script>

<div class="wrap">
  <div class="header">
    <h2>QSO-Log</h2>
    <a class="export" href={api.adifUrl()} download="dk9xr_ft8.adif">⬇ ADIF Export</a>
  </div>

  <div class="filters">
    <input type="text" placeholder="Call (Substring)"
           value={logStore.filters.call}
           oninput={(e) => logStore.setFilter('call', e.target.value)}/>
    <input type="text" placeholder='Präfix (z.B. "9A")'
           value={logStore.filters.prefix}
           style="text-transform: uppercase; max-width: 8rem"
           oninput={(e) => logStore.setFilter('prefix', e.target.value)}/>
    <select value={logStore.filters.band}
            onchange={(e) => logStore.setFilter('band', e.target.value)}>
      <option value="">Alle Bänder</option>
      {#each ['160m','80m','60m','40m','30m','20m','17m','15m','12m','10m','6m','2m','70cm'] as b}
        <option value={b}>{b}</option>
      {/each}
    </select>
    <select value={logStore.filters.mode}
            onchange={(e) => logStore.setFilter('mode', e.target.value)}>
      <option value="">Alle Modi</option>
      <option value="FT8">FT8</option>
      <option value="FT4">FT4</option>
    </select>
    <input type="text" placeholder="Grid"
           value={logStore.filters.grid}
           style="text-transform: uppercase; max-width: 6rem"
           oninput={(e) => logStore.setFilter('grid', e.target.value)}/>
    <select value={logStore.filters.since_days}
            onchange={(e) => logStore.setFilter('since_days', parseInt(e.target.value) || null)}>
      <option value="">Beliebiger Zeitraum</option>
      <option value={1}>letzte 24 h</option>
      <option value={7}>letzte 7 Tage</option>
      <option value={30}>letzte 30 Tage</option>
      <option value={365}>letztes Jahr</option>
    </select>
    <input type="number" placeholder="min RST↓"
           value={logStore.filters.min_snr ?? ''}
           style="max-width: 6rem"
           oninput={(e) => logStore.setFilter('min_snr', e.target.value === '' ? null : parseInt(e.target.value))}/>
    <button class="clear" onclick={() => logStore.clearFilters()}>↺ Reset</button>
    <span class="count">{logStore.total} Treffer</span>
  </div>

  {#if logStore.loading && logStore.qsos.length === 0}
    <p class="empty">Lade…</p>
  {:else if logStore.qsos.length === 0}
    <p class="empty">Keine QSOs mit diesen Filtern.</p>
  {:else}
    <table>
      <thead>
        <tr>
          <th class="pfx-col">Präfix</th>
          {#each headers as h}
            <th class="sortable {h.col === 'call' ? 'call-col' : ''}"
                onclick={() => logStore.setSort(h.col)}>
              {h.label}{sortArrow(h.col)}
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each logStore.qsos as q (q.id)}
          <tr>
            <td><span class="pfx-tag"
                       onclick={() => logStore.setFilter('prefix', extractPrefix(q.call))}
                       title="Filter auf diesen Präfix">{extractPrefix(q.call)}</span></td>
            <td class="ts">{shortTs(q.qso_start)}</td>
            <td class="call-col">
              {#if q.flag}<span class="flag" title={q.call}>{q.flag}</span>{/if}
              {#if q.mf_mfnr}<span class="mf-badge" title="Marinefunker MF #{q.mf_mfnr}">⚓</span>{/if}
              <span class="call" style="color: {COLOURS.worked}">{q.call}</span>
            </td>
            <td>{q.band}</td>
            <td><span class="mode-tag mode-{(q.mode ?? 'FT8').toLowerCase()}">{q.mode ?? 'FT8'}</span></td>
            <td class="freq">{fmtFreq(q.freq_hz)}</td>
            <td class="rst">{fmtRst(q.rst_sent)}</td>
            <td class="rst">{fmtRst(q.rst_rcvd)}</td>
            <td>{q.grid_rcvd ?? ''}</td>
            <td>{q.my_power_w ? q.my_power_w + 'W' : ''}</td>
            <td>{q.swr_avg ? q.swr_avg.toFixed(2) : ''}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    <div class="pager">
      <button onclick={() => logStore.setPage(1)}                disabled={logStore.page === 1}>«</button>
      <button onclick={() => logStore.setPage(logStore.page - 1)} disabled={logStore.page === 1}>‹</button>
      <span>Seite {logStore.page} / {totalPages}</span>
      <button onclick={() => logStore.setPage(logStore.page + 1)} disabled={logStore.page >= totalPages}>›</button>
      <button onclick={() => logStore.setPage(totalPages)}       disabled={logStore.page >= totalPages}>»</button>
    </div>
  {/if}
</div>

<style>
  .wrap { background: var(--panel); border-radius: 8px; padding: 0.6rem; }
  .header { display: flex; align-items: center; gap: 0.7rem;
            flex-wrap: wrap; justify-content: space-between; }
  h2 { margin: 0; font-size: 1rem; color: var(--accent); }
  .export {
    color: var(--accent); text-decoration: none; font-size: 0.85rem;
    border: 1px solid #334155; border-radius: 4px; padding: 0.2rem 0.5rem;
  }
  .export:hover { background: #1e293b; }
  .filters {
    display: flex; gap: 0.3rem; align-items: center; flex-wrap: wrap;
    margin: 0.5rem 0;
  }
  .filters input, .filters select {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 4px; padding: 0.3rem 0.5rem; font-size: 0.85rem;
    font-family: ui-monospace, monospace;
  }
  .filters input[type="text"] { max-width: 10rem; }
  .clear {
    background: transparent; color: #94a3b8; border: 1px solid #334155;
    border-radius: 4px; padding: 0.3rem 0.6rem; cursor: pointer; font-size: 0.8rem;
  }
  .clear:hover { color: var(--fg); }
  .count { color: #94a3b8; font-size: 0.8rem; margin-left: auto; }
  .empty { color: #94a3b8; font-style: italic; padding: 1rem 0; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem;
          font-family: ui-monospace, monospace; }
  th, td { text-align: left; padding: 0.3rem 0.4rem; border-bottom: 1px solid #1e293b; }
  th {
    color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;
    font-size: 0.7rem; font-family: system-ui, sans-serif;
    background: rgba(15,23,42,0.5); user-select: none;
  }
  th.sortable { cursor: pointer; }
  th.sortable:hover { color: var(--accent); background: rgba(56,189,248,0.05); }
  .pfx-col { width: 4rem; }
  .pfx-tag {
    display: inline-block; padding: 0.05em 0.4em; border-radius: 3px;
    background: rgba(167,139,250,0.15); color: #a78bfa; font-size: 0.7rem;
    font-weight: 700; letter-spacing: 0.03em; cursor: pointer;
  }
  .pfx-tag:hover { background: rgba(167,139,250,0.3); }
  /* Call-Spalte breit genug fuer Flag + 7-Zeichen-Call wie "DK9XR/P"
     ohne Umbruch. Sebastian-Bug 2026-05-24 nach Flag-Einbau (v0.3.0).
     min-width damit auch lange Special-Calls (3-letter-prefix + 3-digit
     + 3-letter-suffix) keine umbrechen. */
  .call-col { min-width: 8.5rem; white-space: nowrap; }
  .call { font-weight: 700; }
  .flag { display: inline-block; margin-right: 0.3em; vertical-align: middle; }
  .mf-badge { display: inline-block; margin-right: 0.3em; color: #5eead4; cursor: help; }
  .mode-tag {
    display: inline-block; padding: 0.05em 0.4em; border-radius: 3px;
    font-size: 0.7rem; font-weight: 700; font-family: ui-monospace, monospace;
    border: 1px solid;
  }
  .mode-tag.mode-ft8 {
    color: var(--accent); border-color: rgba(56,189,248,0.4);
    background: rgba(56,189,248,0.08);
  }
  .mode-tag.mode-ft4 {
    color: #fb923c; border-color: rgba(251,146,60,0.4);
    background: rgba(251,146,60,0.10);
  }
  .ts { color: #94a3b8; }
  .freq, .rst { text-align: right; color: #cbd5e1; }
  .pager {
    display: flex; gap: 0.4rem; align-items: center; justify-content: center;
    padding: 0.7rem 0 0.2rem; color: #94a3b8;
  }
  .pager button {
    background: #1e293b; color: var(--fg); border: 1px solid #334155;
    border-radius: 4px; padding: 0.3rem 0.6rem; cursor: pointer;
  }
  .pager button:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
