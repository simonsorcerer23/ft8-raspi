<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';

  let entries = $state([]);
  let threshold = $state(5);
  let minAttempts = $state(3);
  let showAll = $state(false);
  let error = $state(null);
  let loading = $state(false);

  async function refresh() {
    loading = true; error = null;
    try {
      const r = await api.reputation();
      entries = r.entries;
      threshold = r.soft_blacklist_threshold;
      minAttempts = r.min_attempts;
    }
    catch (e) { error = e.message; }
    finally { loading = false; }
  }

  async function reset(call) {
    try { await api.reputationReset(call); await refresh(); }
    catch (e) { error = e.message; }
  }

  onMount(refresh);

  function fmtTs(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString();
  }

  function reasonLabel(r) {
    return {
      success:                'erfolgreiches QSO',
      picked_another:         'Andere stärker',
      max_resends:            'Ignoriert uns wiederholt',
      went_silent:            'Plötzlich verstummt',
      report_never_closed:    'RR73 kam nie',
    }[r] || r || '—';
  }

  let visibleEntries = $derived(
    showAll ? entries : entries.filter(e => e.is_soft_blacklisted || e.score > 0)
  );

  let badCount = $derived(entries.filter(e => e.is_soft_blacklisted).length);
</script>

<div class="wrap">
  <header>
    <h2>✋ Soft-Blacklist (Call-Reputation)</h2>
  </header>

  <div class="stats">
    <span class="pill bad">{badCount} soft-blacklisted</span>
    <span class="pill">{entries.length} insgesamt getrackt</span>
    <label class="check">
      <input type="checkbox" bind:checked={showAll}/>
      <span>auch neutrale Calls zeigen</span>
    </label>
    <button class="refresh" onclick={refresh}>↻ Refresh</button>
  </div>

  {#if error}<div class="err">⚠ {error}</div>{/if}

  {#if loading && entries.length === 0}
    <p class="empty">Lade…</p>
  {:else if visibleEntries.length === 0}
    <p class="empty">
      {#if entries.length === 0}
        Noch keine Call-Reputation-Daten — die DB sammelt sich mit jedem
        QSO-Versuch.
      {:else}
        Keine schlechte Reputation. Alle gepickten Calls antworten.
      {/if}
    </p>
  {:else}
    <table>
      <thead>
        <tr>
          <th>Call</th>
          <th>Score</th>
          <th>Versuche</th>
          <th>Erfolge</th>
          <th>Letzter Grund</th>
          <th>Letzter Versuch</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {#each visibleEntries as e (e.call)}
          <tr class:bad={e.is_soft_blacklisted}>
            <td class="call">
              {#if e.is_soft_blacklisted}<span class="flag">⛔</span>{/if}
              {e.call}
            </td>
            <td class="num" class:score-bad={e.score >= threshold}>{e.score}</td>
            <td class="num">{e.attempts}</td>
            <td class="num">{e.successes}</td>
            <td>{reasonLabel(e.last_reason)}</td>
            <td class="ts">{fmtTs(e.last_attempt_at)}</td>
            <td><button class="rm" onclick={() => reset(e.call)}
                        title="Reputation zurücksetzen (Call wird wieder neutral)">
              Reset
            </button></td>
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
  .stats {
    display: flex; gap: 0.6rem; align-items: center;
    flex-wrap: wrap; margin: 0.6rem 0;
  }
  .pill {
    background: #1e293b; color: var(--fg);
    border-radius: 99px; padding: 0.15rem 0.65rem; font-size: 0.85rem;
  }
  .pill.bad { background: rgba(220,38,38,0.2); color: #fca5a5; }
  .check { display: flex; align-items: center; gap: 0.3rem; font-size: 0.85rem; color: #94a3b8; }
  .refresh {
    background: transparent; color: var(--accent); border: 1px solid #334155;
    border-radius: 4px; padding: 0.25rem 0.6rem; cursor: pointer; font-size: 0.85rem;
    margin-left: auto;
  }
  .empty { color: #94a3b8; font-style: italic; }
  .err { color: var(--danger); margin: 0.5rem 0; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.3rem 0.4rem; border-bottom: 1px solid #1e293b; }
  th { color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;
       font-size: 0.75rem; }
  tr.bad { background: rgba(220,38,38,0.08); }
  .call { font-family: ui-monospace, monospace; font-weight: 700; }
  .flag { color: var(--danger); margin-right: 0.25rem; }
  .num { font-family: ui-monospace, monospace; text-align: right; }
  .score-bad { color: var(--danger); font-weight: 700; }
  .ts { color: #94a3b8; font-family: ui-monospace, monospace; font-size: 0.85rem; }
  .rm {
    background: transparent; color: #94a3b8; border: 1px solid #334155;
    border-radius: 4px; padding: 0.2rem 0.5rem; cursor: pointer; font-size: 0.8rem;
  }
  .rm:hover { color: var(--fg); }
</style>
