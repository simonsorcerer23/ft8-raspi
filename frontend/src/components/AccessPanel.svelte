<script>
  // v0.39.0 — Login-Passwort. Setzt das Master-Passwort auf etwas
  // Merkbares, das Dad eingeben kann (statt des Zufalls-Tokens).
  import { api, setToken } from '../lib/api.js';

  let pw = $state('');
  let busy = $state(false);
  let msg = $state(null);

  async function setPassword(e) {
    e?.preventDefault();
    if (pw.trim().length < 8 || busy) { msg = 'Mindestens 8 Zeichen.'; return; }
    busy = true; msg = null;
    try {
      await api.setAuthPassword(pw.trim());
      // neues Passwort sofort lokal übernehmen, sonst sperrt sich dieser
      // Browser beim nächsten Call selbst aus.
      setToken(pw.trim());
      pw = '';
      msg = 'Passwort gesetzt ✓';
    } catch (err) {
      msg = `Fehlgeschlagen: ${err.message}`;
    } finally { busy = false; }
  }
</script>

<div class="panel">
  <h3>Login-Passwort</h3>
  <form class="pw-box" onsubmit={setPassword}>
    <input type="text" placeholder="z.B. hochgericht-73" bind:value={pw}
           autocapitalize="off" spellcheck="false" />
    <button class="btn primary" type="submit" disabled={busy}>{busy ? '…' : 'setzen'}</button>
  </form>
  {#if msg}<div class="msg">{msg}</div>{/if}
</div>

<style>
  .panel { background: var(--panel); border-radius: 8px; padding: 0.8rem; margin-bottom: 0.8rem; }
  h3 { margin: 0 0 0.5rem; color: var(--accent); font-size: 0.95rem; }
  .pw-box { display: flex; gap: 0.4rem; }
  input {
    flex: 1; min-width: 0; background: rgba(15,23,42,0.6); border: 1px solid #334155;
    border-radius: 5px; padding: 0.5rem 0.6rem; color: var(--text);
    font-family: ui-monospace, monospace; font-size: 0.9rem;
  }
  input:focus { outline: none; border-color: var(--accent); }
  .btn {
    background: rgba(56,189,248,0.12); border: 1px solid #334155; color: var(--accent);
    border-radius: 5px; padding: 0.5rem 0.8rem; cursor: pointer; font-size: 0.82rem; white-space: nowrap;
  }
  .btn.primary { background: var(--accent); color: #0f172a; font-weight: 700; }
  .btn:disabled { opacity: 0.6; cursor: progress; }
  .msg { font-size: 0.78rem; color: #cbd5e1; margin-top: 0.4rem; }
</style>
