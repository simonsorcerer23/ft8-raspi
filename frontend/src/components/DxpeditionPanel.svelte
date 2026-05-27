<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';

  let entries = $state([]);
  let newCall = $state('');
  let newStart = $state('');
  let newEnd = $state('');
  let newNote = $state('');
  let error = $state(null);
  let loading = $state(false);

  async function refresh() {
    loading = true; error = null;
    try { const r = await api.dxpedition(); entries = r.entries; }
    catch (e) { error = e.message; }
    finally { loading = false; }
  }

  async function add() {
    if (!newCall.trim() || !newStart || !newEnd) return;
    try {
      const start = new Date(newStart).toISOString();
      const end = new Date(newEnd).toISOString();
      await api.dxpeditionAdd(
        newCall.trim().toUpperCase(),
        start, end, newNote.trim() || null,
      );
      newCall = ''; newStart = ''; newEnd = ''; newNote = '';
      await refresh();
    } catch (e) { error = e.message; }
  }

  async function remove(call) {
    try { await api.dxpeditionRemove(call); await refresh(); }
    catch (e) { error = e.message; }
  }

  onMount(refresh);

  function fmtDate(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString();
  }

  function statusOf(e) {
    const now = new Date();
    const start = new Date(e.start_date);
    const end = new Date(e.end_date);
    if (now > end) return { label: 'vorbei', cls: 'past' };
    if (now < start) {
      const dh = (start - now) / 3600000;
      if (dh < 24) return { label: `in ${Math.round(dh)} h`, cls: 'soon' };
      const dd = dh / 24;
      return { label: `in ${Math.round(dd)} d`, cls: 'future' };
    }
    return { label: 'QRV jetzt', cls: 'active' };
  }
</script>

<div class="wrap">
  <header>
    <h2>📡 DXpedition-Schedule</h2>
    <small>
      Trage geplante DXpeditions ein damit du sie nicht verpasst. 24 h
      vor Start kommt eine ntfy-Push, beim QRV-Start wird der Call
      automatisch in die Watchlist gesetzt (und nach Ende wieder raus).
    </small>
  </header>

  <form class="add" onsubmit={(e) => { e.preventDefault(); add(); }}>
    <input type="text" placeholder="Call (z.B. ZL9HR)"
           bind:value={newCall} required style="text-transform: uppercase"/>
    <input type="datetime-local" placeholder="Start" bind:value={newStart} required/>
    <input type="datetime-local" placeholder="Ende" bind:value={newEnd} required/>
    <input type="text" placeholder="Notiz (z.B. Bouvet)" bind:value={newNote}/>
    <button class="add-btn" type="submit">Hinzufügen</button>
  </form>

  {#if error}<div class="err">⚠ {error}</div>{/if}

  {#if loading && entries.length === 0}
    <p class="empty">Lade…</p>
  {:else if entries.length === 0}
    <p class="empty">
      Kein DXpedition-Schedule. Trag deine geplanten DXpeditions ein —
      24 h vor QRV-Start kommt eine ntfy-Push.
    </p>
  {:else}
    <table>
      <thead>
        <tr>
          <th>Call</th><th>Notiz</th><th>Start</th><th>Ende</th>
          <th>Status</th><th>Watchlist</th><th></th>
        </tr>
      </thead>
      <tbody>
        {#each entries as e (e.call)}
          {@const st = statusOf(e)}
          <tr class={st.cls}>
            <td class="call">{e.call}</td>
            <td>{e.note ?? '—'}</td>
            <td class="ts">{fmtDate(e.start_date)}</td>
            <td class="ts">{fmtDate(e.end_date)}</td>
            <td><span class="pill {st.cls}">{st.label}</span></td>
            <td>{e.auto_added_to_watchlist ? '👀 ja' : '—'}</td>
            <td><button class="rm" onclick={() => remove(e.call)}>Entfernen</button></td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .wrap { background: var(--panel); border-radius: 8px; padding: 0.8rem; }
  header { margin-bottom: 0.7rem; }
  h2 { margin: 0; color: var(--accent); font-size: 1rem; }
  header small { color: #94a3b8; font-size: 0.8rem; }
  .add { display: flex; gap: 0.4rem; margin-bottom: 0.7rem; flex-wrap: wrap; }
  .add input {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 4px; padding: 0.4rem 0.6rem; font-size: 0.9rem;
  }
  .add input[type="text"]:first-child { width: 7rem; font-family: ui-monospace, monospace; }
  .add input[type="text"]:last-of-type { flex: 1; min-width: 10rem; }
  .add-btn {
    background: var(--accent); color: white; border: none;
    border-radius: 4px; padding: 0.4rem 1rem; cursor: pointer; font-weight: 600;
  }
  .empty { color: #94a3b8; font-style: italic; }
  .err { color: var(--danger); margin: 0.5rem 0; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.3rem 0.4rem; border-bottom: 1px solid #1e293b; }
  th { color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; }
  tr.active { background: rgba(34,197,94,0.08); }
  tr.past { color: #64748b; }
  .call { font-family: ui-monospace, monospace; font-weight: 700; color: var(--accent); }
  .ts { color: #94a3b8; font-family: ui-monospace, monospace; font-size: 0.85rem; }
  .pill { background: #1e293b; padding: 0.1rem 0.5rem; border-radius: 99px; font-size: 0.8rem; }
  .pill.active { background: rgba(34,197,94,0.25); color: #86efac; }
  .pill.soon { background: rgba(234,179,8,0.25); color: #fde047; }
  .pill.future { background: #1e293b; color: #94a3b8; }
  .pill.past { background: #0f172a; color: #475569; }
  .rm {
    background: transparent; color: #94a3b8; border: 1px solid #334155;
    border-radius: 4px; padding: 0.2rem 0.5rem; cursor: pointer; font-size: 0.8rem;
  }
  .rm:hover { color: var(--fg); }
</style>
