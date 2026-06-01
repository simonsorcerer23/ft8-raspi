<script>
  // v0.37.0 — Token-Login. Erscheint wenn kein Token gespeichert ist oder
  // ein API-Call 401 lieferte. Speichert den Token pro Origin in
  // localStorage; danach laeuft alles ueber den Authorization-Header.
  import { onMount } from 'svelte';
  import { api, getToken, setToken } from '../lib/api.js';
  import { t } from '../lib/i18n.svelte.js';

  let { onAuthed } = $props();
  let value = $state('');
  let busy = $state(false);
  let error = $state(null);

  async function submit(e) {
    e?.preventDefault();
    const t = value.trim();
    if (!t || busy) return;
    busy = true; error = null;
    setToken(t);
    try {
      // Validieren: ein geschuetzter Endpoint muss jetzt durchgehen.
      await api.status();
      onAuthed?.();
    } catch (err) {
      error = t('login.rejected');
      busy = false;
    }
  }
</script>

<div class="overlay">
  <form class="card" onsubmit={submit}>
    <h2>{t('login.title')}</h2>
    <p>{t('login.prompt')}</p>
    <input type="password" placeholder={t('login.placeholder')} bind:value autocomplete="current-password"
           autocapitalize="off" spellcheck="false" />
    {#if error}<div class="err">{error}</div>{/if}
    <button type="submit" disabled={busy}>{busy ? '…' : t('login.submit')}</button>
  </form>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 1000;
    background: #0b1220; display: flex; align-items: center; justify-content: center;
    padding: 1rem;
  }
  .card {
    background: var(--panel, #1e293b); border: 1px solid #334155; border-radius: 12px;
    padding: 1.5rem; width: 100%; max-width: 360px; display: flex; flex-direction: column;
    gap: 0.8rem;
  }
  h2 { margin: 0; color: var(--accent, #38bdf8); font-size: 1.1rem; }
  p { margin: 0; color: #94a3b8; font-size: 0.8rem; line-height: 1.5; }
  code { color: #cbd5e1; font-size: 0.72rem; word-break: break-all; }
  input {
    background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 6px;
    padding: 0.6rem 0.7rem; color: var(--text, #e2e8f0); font-size: 1rem;
    font-family: ui-monospace, monospace;
  }
  input:focus { outline: none; border-color: var(--accent, #38bdf8); }
  .err { color: #f87171; font-size: 0.8rem; }
  button {
    background: var(--accent, #38bdf8); color: #0f172a; border: none; border-radius: 6px;
    padding: 0.6rem; font-weight: 700; cursor: pointer; font-size: 0.95rem;
  }
  button:disabled { opacity: 0.6; cursor: progress; }
</style>
