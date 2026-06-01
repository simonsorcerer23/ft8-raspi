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
        <button class="btn" onclick={() => check(op.callsign)}
                disabled={preflight[op.callsign]?.busy}>
          {preflight[op.callsign]?.busy ? '…' : t('opadmin.check')}
        </button>
      </div>
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
  .error { color: var(--danger); font-size: 0.8rem; margin-bottom: 0.5rem; }
</style>
