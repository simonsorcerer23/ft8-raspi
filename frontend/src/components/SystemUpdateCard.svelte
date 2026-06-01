<script>
  /*
   * SystemUpdateCard — Card auf der Konfig-Seite die zeigt:
   *  - aktuell installierte Version
   *  - latest verfügbarer Tag (bekannt aus letzter git-fetch)
   *  - Button "Jetzt updaten" (nur wenn neuer Tag verfügbar)
   *  - Live-Indikator wenn Self-Update gerade läuft
   *  - Hinweis wenn Pi noch eine alte rsync-Installation ist (kein .git)
   *
   * Polling-Strategie:
   *  - alle 30s normal
   *  - alle 3s solange update_in_progress=true (für lebendiges Feedback)
   */
  import { onMount, onDestroy } from 'svelte';
  import { api } from '../lib/api.js';
  import { t } from '../lib/i18n.svelte.js';

  let info = $state(null);
  let error = $state(null);
  let triggering = $state(false);
  let triggerMsg = $state(null);
  let pollTimer = null;

  async function refresh() {
    try {
      info = await api.systemVersion();
      error = null;
    } catch (e) {
      error = e.message;
    }
  }

  function reschedule() {
    if (pollTimer) clearTimeout(pollTimer);
    const delay = (info?.update_in_progress) ? 3000 : 30000;
    pollTimer = setTimeout(async () => {
      await refresh();
      reschedule();
    }, delay);
  }

  async function triggerUpdate() {
    if (!confirm(t('sysupd.confirm'))) return;
    triggering = true;
    triggerMsg = null;
    try {
      const r = await api.triggerSelfUpdate();
      triggerMsg = { kind: 'ok', text: r.detail || t('sysupd.started') };
      // sofort refresh + auf fast-poll umschalten
      await refresh();
      reschedule();
    } catch (e) {
      triggerMsg = { kind: 'err', text: e.message };
    } finally { triggering = false; }
  }

  function fmtTs(unix) {
    if (!unix) return '—';
    const d = new Date(unix * 1000);
    const now = Date.now();
    const ageS = Math.round((now - d.getTime()) / 1000);
    if (ageS < 60) return t('sysupd.ago_s', { n: ageS });
    if (ageS < 3600) return t('sysupd.ago_m', { n: Math.round(ageS / 60) });
    if (ageS < 86400) return t('sysupd.ago_h', { n: Math.round(ageS / 3600) });
    return d.toLocaleString();
  }

  onMount(async () => {
    await refresh();
    reschedule();
  });

  onDestroy(() => { if (pollTimer) clearTimeout(pollTimer); });
</script>

<section class="card">
  <h3>{t('sysupd.title')}</h3>

  {#if error}
    <p class="err">⚠ {error}</p>
  {:else if !info}
    <p class="muted">{t('sysupd.loading')}</p>
  {:else if !info.repo_is_git}
    <p class="warn">
      {t('sysupd.rsync_warn')} <code>docs/self_update.md</code>.
    </p>
    <dl class="kv">
      <dt>{t('sysupd.installed')}</dt><dd>{info.current_version || '—'}</dd>
      <dt>{t('sysupd.git_state')}</dt><dd><code>{info.git_describe || '—'}</code></dd>
    </dl>
  {:else}
    <dl class="kv">
      <dt>{t('sysupd.installed')}</dt>
      <dd>
        <strong>{info.current_tag || t('sysupd.no_tag')}</strong>
        <span class="muted">({info.current_version})</span>
      </dd>

      <dt>{t('sysupd.latest')}</dt>
      <dd>
        {#if info.latest_version}
          <strong>{info.latest_version}</strong>
          {#if info.update_available}
            <span class="badge new">{t('sysupd.badge_new')}</span>
          {:else}
            <span class="badge ok">{t('sysupd.badge_current')}</span>
          {/if}
        {:else}
          <span class="muted">—</span>
        {/if}
      </dd>

      <dt>{t('sysupd.last_fetch')}</dt>
      <dd class="muted">{fmtTs(info.last_fetch_at)}</dd>

      <dt>{t('sysupd.git_describe')}</dt>
      <dd><code>{info.git_describe || '—'}</code></dd>
    </dl>

    <div class="actions">
      {#if info.update_in_progress}
        <span class="status running">{t('sysupd.running')}</span>
      {:else if info.update_available}
        <button class="primary" onclick={triggerUpdate} disabled={triggering}>
          {triggering ? t('sysupd.starting') : t('sysupd.update_to', { ver: info.latest_version })}
        </button>
      {:else}
        <button class="primary" onclick={triggerUpdate} disabled={triggering}>
          {triggering ? t('sysupd.starting') : t('sysupd.force')}
        </button>
      {/if}
      {#if triggerMsg}
        <span class="status {triggerMsg.kind}">{triggerMsg.text}</span>
      {/if}
    </div>
  {/if}
</section>

<style>
  .card {
    background: rgba(15,23,42,0.4); border-radius: 6px;
    padding: 0.7rem 0.8rem; margin-bottom: 0.5rem;
  }
  h3 { margin: 0 0 0.5rem; color: var(--accent); font-size: 0.9rem; }
  dl.kv {
    display: grid; grid-template-columns: max-content 1fr;
    gap: 0.25rem 0.8rem; margin: 0 0 0.7rem;
    font-size: 0.85rem;
  }
  dl.kv dt { color: #94a3b8; font-size: 0.75rem;
             text-transform: uppercase; letter-spacing: 0.05em;
             align-self: center; }
  dl.kv dd { margin: 0; color: var(--fg); }
  code { font-family: ui-monospace, monospace; font-size: 0.8rem;
         color: #cbd5e1; background: #0b1220; padding: 0.05rem 0.3rem;
         border-radius: 3px; }
  .muted { color: #94a3b8; }
  .small { font-size: 0.75rem; }
  .err { color: var(--danger); margin: 0; }
  .warn { color: #fbbf24; margin: 0 0 0.5rem; line-height: 1.4; }
  .badge {
    display: inline-block; padding: 0.05rem 0.45rem; border-radius: 999px;
    font-size: 0.7rem; margin-left: 0.4rem; text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .badge.new { background: rgba(56,189,248,0.15); color: var(--accent);
               border: 1px solid var(--accent); }
  .badge.ok  { background: rgba(34,197,94,0.10); color: var(--ok);
               border: 1px solid rgba(34,197,94,0.4); }
  .actions { display: flex; gap: 0.7rem; align-items: center; flex-wrap: wrap; }
  .primary {
    background: var(--accent); color: #0f172a; border: none;
    border-radius: 6px; padding: 0.4rem 0.9rem; font-weight: 600;
    cursor: pointer; font-size: 0.85rem;
  }
  .primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .status.ok      { color: var(--ok); }
  .status.err     { color: var(--danger); }
  .status.running { color: var(--accent);
                    font-family: ui-monospace, monospace; }
</style>
