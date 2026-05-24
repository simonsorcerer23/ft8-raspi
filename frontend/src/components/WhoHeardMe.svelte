<script>
  // Wer hat DK9XR via PSK-Reporter gehört — als gruppierte Tabelle
  // pro Reporter-Station, mit best-SNR und Anzahl Reports.
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';

  let reports = $state([]);
  let loading = $state(true);
  let hours = $state(24);
  let error = $state(null);

  async function refresh() {
    loading = true; error = null;
    try {
      const r = await api.whoHeardMe(hours);
      reports = r.reports ?? [];
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  // Aggregiere pro rx_call: count, best_snr, last_seen.
  const grouped = $derived.by(() => {
    const map = new Map();
    for (const r of reports) {
      const k = r.rx_call;
      const e = map.get(k);
      const ts = new Date(r.received_at);
      if (!e) {
        map.set(k, {
          rx_call: r.rx_call, rx_grid: r.rx_grid,
          best_snr: r.snr_db, count: 1, last_seen: ts,
          bands: new Set(r.band ? [r.band] : []),
        });
      } else {
        e.count += 1;
        if (r.snr_db != null && (e.best_snr == null || r.snr_db > e.best_snr)) {
          e.best_snr = r.snr_db;
        }
        if (ts > e.last_seen) e.last_seen = ts;
        if (r.band) e.bands.add(r.band);
      }
    }
    return [...map.values()].sort((a, b) => b.count - a.count);
  });

  function shortTs(d) {
    return d.toLocaleString([], { day: '2-digit', month: '2-digit',
                                   hour: '2-digit', minute: '2-digit' });
  }

  onMount(refresh);
</script>

<div class="wrap">
  <header>
    <h2>📡 Wer hat mich gehört? (PSK Reporter)</h2>
    <div class="ctrl">
      <select bind:value={hours} onchange={refresh}>
        <option value={1}>1 h</option>
        <option value={6}>6 h</option>
        <option value={24}>24 h</option>
        <option value={72}>3 Tage</option>
      </select>
      <button onclick={refresh} disabled={loading}>↻</button>
    </div>
  </header>

  {#if error}
    <div class="err">⚠ {error}</div>
  {:else if loading && reports.length === 0}
    <div class="muted">Lade…</div>
  {:else if grouped.length === 0}
    <div class="muted">Bisher kein Empfangsbericht. Sobald jemand DK9XR
      decodiert hat (typisch 5–10 min nach erstem TX), erscheinen die
      Reporter hier.</div>
  {:else}
    <table>
      <thead>
        <tr>
          <th>Reporter</th>
          <th>Grid</th>
          <th>Best SNR</th>
          <th>Reports</th>
          <th>Bänder</th>
          <th>Letzter</th>
        </tr>
      </thead>
      <tbody>
        {#each grouped as g (g.rx_call)}
          <tr>
            <td class="call">{g.rx_call}</td>
            <td class="grid">{g.rx_grid ?? '—'}</td>
            <td class="snr">{g.best_snr ?? '—'} dB</td>
            <td class="count">{g.count}</td>
            <td class="bands">{[...g.bands].join(', ')}</td>
            <td class="ts">{shortTs(g.last_seen)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
    <div class="muted">{grouped.length} einzigartige Reporter · {reports.length} Reports gesamt</div>
  {/if}
</div>

<style>
  .wrap { background: var(--panel); border-radius: 8px; padding: 0.6rem;
          display: flex; flex-direction: column; gap: 0.5rem; }
  header { display: flex; justify-content: space-between; align-items: center; }
  header h2 { font-size: 1rem; margin: 0; color: var(--accent); }
  .ctrl { display: flex; gap: 0.4rem; }
  .ctrl select, .ctrl button {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 4px; padding: 0.3rem 0.6rem; font-size: 0.85rem;
    cursor: pointer;
  }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th, td { text-align: left; padding: 0.3rem 0.4rem;
           border-bottom: 1px solid #1e293b; }
  th { color: #94a3b8; font-weight: 500; font-size: 0.75rem;
       text-transform: uppercase; letter-spacing: 0.05em; }
  .call { font-family: ui-monospace, monospace; font-weight: 600; color: var(--accent); }
  .grid, .ts { color: #94a3b8; font-size: 0.8rem; }
  .snr { font-family: ui-monospace, monospace; }
  .count { font-weight: 600; }
  .bands { font-size: 0.75rem; color: #cbd5e1; }
  .muted { color: #94a3b8; font-size: 0.85rem; padding: 0.5rem; }
  .err { color: var(--danger); padding: 0.5rem; }
</style>
