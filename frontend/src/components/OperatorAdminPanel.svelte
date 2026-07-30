<script>
  // v0.29.2 — Operator- & Logbuch-Verwaltung auf der Konfig-Seite.
  // Zeigt pro Person den QRZ-/ClubLog-Status + alle hinterlegten
  // Sende-Call-Logbuecher (Prefix/Suffix → eigener QRZ-Key) mit
  // Pre-Flight-Check, Hinzufuegen + Entfernen. Loest das fummelige
  // Mini-Fenster im Header ab.
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { t } from '../lib/i18n.svelte.js';

  let operators = $state([]);
  let active = $state('');
  let busy = $state(false);
  let error = $state(null);
  let preflight = $state({});   // call → { busy, qrz, clublog, error }
  let lbForm = $state({});      // person → { call, key }
  // person → Zugangsdaten-Formular. Passwoerter/Keys werden von der API
  // nie ausgeliefert, also starten die Felder leer: leer = "nicht
  // aendern". Geloescht wird ueber die expliziten Entfernen-Buttons.
  let credOpen = $state({});
  let credForm = $state({});

  function pfClass(status) {
    if (status === 'ok') return 'ok';
    if (status === 'error') return 'err';
    if (status === 'info') return 'info';
    return 'warn';
  }

  async function refresh() {
    try {
      const data = await api.operatorsList();
      operators = data.operators;
      active = data.active_callsign;
      for (const op of operators) {
        if (!lbForm[op.callsign]) lbForm[op.callsign] = { call: '', key: '' };
      }
      error = null;
    } catch (e) { error = e.message; }
  }

  onMount(refresh);

  async function check(call) {
    preflight[call] = { busy: true };
    try {
      const res = await api.operatorPreflight(call);
      preflight[call] = { busy: false, qrz: res.qrz, clublog: res.clublog };
    } catch (e) {
      preflight[call] = { busy: false, error: e.message };
    }
  }

  async function addLogbook(cs) {
    const d = lbForm[cs];
    if (!d || !d.call.trim() || !d.key.trim() || busy) return;
    busy = true; error = null;
    try {
      await api.operatorAddLogbook(cs, d.call.trim().toUpperCase(), d.key.trim());
      lbForm[cs] = { call: '', key: '' };
      await refresh();
    } catch (e) { error = e.message; } finally { busy = false; }
  }

  function toggleCreds(op) {
    if (credOpen[op.callsign]) { credOpen[op.callsign] = false; return; }
    credForm[op.callsign] = {
      qrz_user: op.qrz_user ?? '',
      qrz_password: '',
      qrz_logbook_api_key: '',
      clublog_email: op.clublog_email ?? '',
      clublog_app_password: '',
      clublog_api_key: '',
    };
    credOpen[op.callsign] = true;
  }

  async function saveCreds(op) {
    const f = credForm[op.callsign];
    if (!f || busy) return;
    // Nur geaenderte/gefuellte Felder senden. Ein leeres Passwortfeld
    // heisst "unveraendert lassen" — sonst wuerde jedes Speichern die
    // nicht angezeigten Secrets loeschen.
    const body = {};
    if (f.qrz_user.trim() !== (op.qrz_user ?? '')) body.qrz_user = f.qrz_user.trim();
    if (f.clublog_email.trim() !== (op.clublog_email ?? '')) {
      body.clublog_email = f.clublog_email.trim();
    }
    for (const k of ['qrz_password', 'qrz_logbook_api_key',
                     'clublog_app_password', 'clublog_api_key']) {
      if (f[k].trim()) body[k] = f[k].trim();
    }
    if (Object.keys(body).length === 0) { credOpen[op.callsign] = false; return; }
    busy = true; error = null;
    try {
      await api.operatorUpdate(op.callsign, body);
      credOpen[op.callsign] = false;
      delete preflight[op.callsign];
      await refresh();
    } catch (e) { error = e.message; } finally { busy = false; }
  }

  async function clearCreds(op, service) {
    if (busy || !confirm(t('opadmin.confirm_clear_creds',
                           { service, call: op.callsign }))) return;
    // Leerstring = Feld loeschen (PATCH-Semantik im Backend).
    const body = service === 'QRZ'
      ? { qrz_user: '', qrz_password: '', qrz_logbook_api_key: '' }
      : { clublog_email: '', clublog_app_password: '', clublog_api_key: '' };
    busy = true; error = null;
    try {
      await api.operatorUpdate(op.callsign, body);
      credOpen[op.callsign] = false;
      delete preflight[op.callsign];
      await refresh();
    } catch (e) { error = e.message; } finally { busy = false; }
  }

  async function removeLogbook(cs, call) {
    if (busy || !confirm(t('opadmin.confirm_remove', { call }))) return;
    busy = true; error = null;
    try {
      await api.operatorDeleteLogbook(cs, call);
      delete preflight[call];
      await refresh();
    } catch (e) { error = e.message; } finally { busy = false; }
  }
</script>

<div class="panel">
  <h3>{t('opadmin.title')}</h3>
  {#if error}<div class="error">⚠ {error}</div>{/if}

  {#each operators as op (op.callsign)}
    <div class="op" class:active={op.callsign === active}>
      <div class="op-head">
        <span class="cs">{op.callsign}</span>
        <span class="meta">{op.license_class}{#if op.default_locator} · {op.default_locator}{/if}</span>
        <span class="creds">
          <span class="chip {op.has_qrz_credentials ? 'on' : 'off'}">QRZ</span>
          <span class="chip {op.has_clublog_credentials ? 'on' : 'off'}">ClubLog</span>
        </span>
        <button class="btn" onclick={() => toggleCreds(op)} disabled={busy}>
          {t('opadmin.credentials')}
        </button>
        <button class="btn" onclick={() => check(op.callsign)}
                disabled={preflight[op.callsign]?.busy}>
          {preflight[op.callsign]?.busy ? '…' : t('opadmin.check')}
        </button>
      </div>

      {#if credOpen[op.callsign]}
        <div class="creds-form">
          <div class="cf-title">
            <span>QRZ.com</span>
            {#if op.has_qrz_credentials}
              <button class="btn sm del" onclick={() => clearCreds(op, 'QRZ')}
                      disabled={busy}>{t('opadmin.clear_creds')}</button>
            {/if}
          </div>
          <label><span>{t('opsw.qrz_user')}</span>
            <input type="text" autocapitalize="characters"
                   bind:value={credForm[op.callsign].qrz_user} /></label>
          <label><span>{t('opsw.qrz_password')}</span>
            <input type="password" autocomplete="new-password"
                   placeholder={t('opadmin.unchanged')}
                   bind:value={credForm[op.callsign].qrz_password} /></label>
          <label><span>{t('opsw.qrz_api_key')}</span>
            <input type="text" placeholder={op.has_qrz_credentials
                     ? t('opadmin.unchanged') : t('opadmin.api_key_ph')}
                   bind:value={credForm[op.callsign].qrz_logbook_api_key} /></label>

          <div class="cf-title">
            <span>ClubLog</span>
            {#if op.has_clublog_credentials}
              <button class="btn sm del" onclick={() => clearCreds(op, 'ClubLog')}
                      disabled={busy}>{t('opadmin.clear_creds')}</button>
            {/if}
          </div>
          <label><span>{t('opsw.clublog_email')}</span>
            <input type="email" bind:value={credForm[op.callsign].clublog_email} /></label>
          <label><span>{t('opsw.clublog_app_pw')}</span>
            <input type="password" autocomplete="new-password"
                   placeholder={t('opadmin.unchanged')}
                   bind:value={credForm[op.callsign].clublog_app_password} /></label>
          <label><span>{t('opsw.clublog_api_key')}</span>
            <input type="text" placeholder={op.has_clublog_credentials
                     ? t('opadmin.unchanged') : ''}
                   bind:value={credForm[op.callsign].clublog_api_key} /></label>

          <div class="cf-actions">
            <button class="btn" onclick={() => credOpen[op.callsign] = false}
                    disabled={busy}>{t('opadmin.cancel')}</button>
            <button class="btn primary" onclick={() => saveCreds(op)}
                    disabled={busy}>{t('opadmin.save')}</button>
          </div>
        </div>
      {/if}
      {#if preflight[op.callsign] && !preflight[op.callsign].busy}
        {@const pf = preflight[op.callsign]}
        <div class="pf">
          {#if pf.error}
            <div class="pf-line err">⚠ {pf.error}</div>
          {:else}
            <div class="pf-line {pfClass(pf.qrz.status)}">QRZ: {pf.qrz.detail}</div>
            <div class="pf-line {pfClass(pf.clublog.status)}">ClubLog: {pf.clublog.detail}</div>
          {/if}
        </div>
      {/if}

      <div class="lb">
        <div class="lb-title">{t('opadmin.send_calls')}</div>
        {#if op.station_logbooks.length === 0}
          <div class="lb-empty">{t('opadmin.no_logbooks')}</div>
        {/if}
        {#each op.station_logbooks as call (call)}
          <div class="lb-row">
            <span class="lb-call">{call}</span>
            <span class="chip on">Key ✓</span>
            <button class="btn sm" onclick={() => check(call)}
                    disabled={preflight[call]?.busy}>
              {preflight[call]?.busy ? '…' : t('opadmin.check')}
            </button>
            <button class="btn sm del" onclick={() => removeLogbook(op.callsign, call)}
                    disabled={busy}>{t('opadmin.remove')}</button>
          </div>
          {#if preflight[call] && !preflight[call].busy && !preflight[call].error}
            <div class="pf-line {pfClass(preflight[call].qrz.status)}">QRZ: {preflight[call].qrz.detail}</div>
            <div class="pf-line {pfClass(preflight[call].clublog.status)}">ClubLog: {preflight[call].clublog.detail}</div>
          {/if}
        {/each}
        {#if lbForm[op.callsign]}
          <div class="lb-add">
            <input type="text" placeholder="{op.callsign}/AM" autocapitalize="characters"
                   bind:value={lbForm[op.callsign].call} />
            <input type="text" placeholder={t('opadmin.api_key_ph')}
                   bind:value={lbForm[op.callsign].key} />
            <button class="btn" onclick={() => addLogbook(op.callsign)} disabled={busy}>{t('opadmin.add')}</button>
          </div>
        {/if}
      </div>
    </div>
  {/each}
</div>

<style>
  .panel { background: var(--panel); border-radius: 8px; padding: 0.8rem; }
  h3 { margin: 0 0 0.6rem; color: var(--accent); font-size: 0.95rem; }
  .op {
    border: 1px solid #1e293b; border-radius: 6px; padding: 0.6rem;
    margin-bottom: 0.6rem;
  }
  .op.active { border-left: 3px solid var(--accent); }
  .op-head { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
  .cs { font-family: ui-monospace, monospace; font-weight: 700; font-size: 1.05rem; color: var(--accent); }
  .meta { color: #94a3b8; font-size: 0.78rem; }
  .creds { display: flex; gap: 0.3rem; margin-left: auto; }
  .chip {
    font-size: 0.65rem; padding: 0.1rem 0.45rem; border-radius: 999px;
    text-transform: uppercase; letter-spacing: 0.04em; font-weight: 700;
  }
  .chip.on { background: rgba(34,197,94,0.18); color: #4ade80; }
  .chip.off { background: rgba(100,116,139,0.18); color: #64748b; }
  .btn {
    background: rgba(56,189,248,0.12); border: 1px solid #334155; color: var(--accent);
    border-radius: 5px; padding: 0.25rem 0.6rem; cursor: pointer; font-size: 0.78rem;
  }
  .btn:hover { border-color: var(--accent); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn.sm { padding: 0.15rem 0.45rem; font-size: 0.7rem; }
  .btn.del { color: #94a3b8; background: transparent; }
  .btn.del:hover { color: var(--danger); border-color: var(--danger); }
  .pf { margin: 0.4rem 0; display: flex; flex-direction: column; gap: 0.2rem; }
  .pf-line { font-size: 0.72rem; color: #94a3b8; }
  .pf-line.ok { color: #4ade80; }
  .pf-line.warn { color: #fbbf24; }
  .pf-line.err { color: var(--danger); }
  .pf-line.info { color: #94a3b8; }
  .lb { margin-top: 0.5rem; border-top: 1px solid #1e293b; padding-top: 0.4rem; }
  .lb-title { font-size: 0.66rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.3rem; }
  .lb-empty { font-size: 0.75rem; color: #64748b; font-style: italic; }
  .lb-row { display: flex; align-items: center; gap: 0.45rem; font-size: 0.8rem; margin: 0.15rem 0; }
  .lb-call { font-family: ui-monospace, monospace; color: var(--text); }
  .lb-add { display: flex; gap: 0.4rem; margin-top: 0.4rem; flex-wrap: wrap; }
  .lb-add input {
    flex: 1; min-width: 8rem; background: rgba(15,23,42,0.6); border: 1px solid #334155;
    border-radius: 4px; padding: 0.3rem 0.45rem; color: var(--text); font-size: 0.78rem;
  }
  .lb-add input:focus { outline: none; border-color: var(--accent); }
  .creds-form {
    margin: 0.5rem 0; padding: 0.5rem; border: 1px solid #1e293b;
    border-radius: 6px; display: flex; flex-direction: column; gap: 0.35rem;
  }
  .cf-title {
    display: flex; align-items: center; justify-content: space-between;
    font-size: 0.66rem; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.05em; margin-top: 0.2rem;
  }
  .creds-form label { display: flex; align-items: center; gap: 0.5rem; font-size: 0.78rem; }
  .creds-form label span { flex: 0 0 9rem; color: #94a3b8; }
  .creds-form input {
    flex: 1; min-width: 6rem; background: rgba(15,23,42,0.6); border: 1px solid #334155;
    border-radius: 4px; padding: 0.3rem 0.45rem; color: var(--text); font-size: 0.78rem;
  }
  .creds-form input:focus { outline: none; border-color: var(--accent); }
  .cf-actions { display: flex; gap: 0.4rem; justify-content: flex-end; margin-top: 0.3rem; }
  .btn.primary { background: rgba(56,189,248,0.25); border-color: var(--accent); }
  .error { color: var(--danger); font-size: 0.8rem; margin-bottom: 0.5rem; }
</style>
