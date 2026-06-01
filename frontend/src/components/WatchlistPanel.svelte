<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { fmtUtcDateTime } from '../lib/time.js';
  import { t } from '../lib/i18n.svelte.js';

  let entries = $state([]);
  let newCall = $state('');
  let newNote = $state('');
  let error = $state(null);
  let loading = $state(false);

  async function refresh() {
    loading = true; error = null;
    try { const r = await api.watchlist(); entries = r.entries; }
    catch (e) { error = e.message; }
    finally { loading = false; }
  }

  async function add() {
    if (!newCall.trim()) return;
    try {
      await api.watchlistAdd(newCall.trim().toUpperCase(), newNote.trim() || null);
      newCall = ''; newNote = '';
      await refresh();
    } catch (e) { error = e.message; }
  }

  async function remove(call) {
    try { await api.watchlistRemove(call); await refresh(); }
    catch (e) { error = e.message; }
  }

  onMount(refresh);

  function fmtTs(iso) {
    if (!iso) return '—';
    return fmtUtcDateTime(iso);
  }
</script>

<div class="wrap">
  <header>
    <h2>{t('wl.title')}</h2>
  </header>

  <form class="add" onsubmit={(e) => { e.preventDefault(); add(); }}>
    <input type="text" placeholder={t('wl.call_ph')}
           bind:value={newCall} required style="text-transform: uppercase"/>
    <input type="text" placeholder={t('wl.note_ph')} bind:value={newNote}/>
    <button class="add-btn" type="submit">{t('wl.watch')}</button>
  </form>

  {#if error}<div class="err">⚠ {error}</div>{/if}

  {#if loading && entries.length === 0}
    <p class="empty">{t('common.loading')}</p>
  {:else if entries.length === 0}
    <p class="empty">{t('wl.empty')}</p>
  {:else}
    <table>
      <thead>
        <tr><th>{t('common.call')}</th><th>{t('common.note')}</th><th>{t('common.added')}</th><th>{t('wl.last_alert')}</th><th></th></tr>
      </thead>
      <tbody>
        {#each entries as e (e.call)}
          <tr>
            <td class="call">{e.call}</td>
            <td>{e.note ?? '—'}</td>
            <td class="ts">{fmtTs(e.added)}</td>
            <td class="ts">{fmtTs(e.last_alert_at)}</td>
            <td><button class="rm" onclick={() => remove(e.call)}>{t('common.remove')}</button></td>
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
  .add input:first-child { width: 8rem; font-family: ui-monospace, monospace; }
  .add input:nth-child(2) { flex: 1; min-width: 12rem; }
  .add-btn {
    background: var(--accent); color: white; border: none;
    border-radius: 4px; padding: 0.4rem 1rem; cursor: pointer; font-weight: 600;
  }
  .empty { color: #94a3b8; font-style: italic; }
  .err { color: var(--danger); margin: 0.5rem 0; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.3rem 0.4rem; border-bottom: 1px solid #1e293b; }
  th { color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;
       font-size: 0.75rem; }
  .call { font-family: ui-monospace, monospace; font-weight: 700; color: var(--accent); }
  .ts { color: #94a3b8; font-family: ui-monospace, monospace; font-size: 0.85rem; }
  .rm {
    background: transparent; color: #94a3b8; border: 1px solid #334155;
    border-radius: 4px; padding: 0.2rem 0.5rem; cursor: pointer; font-size: 0.8rem;
  }
  .rm:hover { color: var(--fg); }
</style>
