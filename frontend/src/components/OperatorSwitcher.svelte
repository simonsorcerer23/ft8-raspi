<script>
  // Operator-Profile-Switcher (Sebastian 2026-05-23).
  //
  // Zeigt das aktuelle Callsign mit Click-Toggle zu einem Dropdown
  // aller verfuegbaren Profile. Wechsel ruft POST /api/operators/select
  // — das Backend macht Hot-Swap: state-machine reset, worked-Set
  // neu laden, QRZ-Client mit neuen Credentials neu init.
  //
  // Erweitert um Create + Delete (Sebastian 2026-05-23 spaeter):
  // "+ Neuer Operator" oeffnet ein Inline-Formular, "✕" neben einem
  // nicht-aktiven Profil loescht es (mit Confirm-Dialog wenn QSOs
  // existieren).
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';

  let operators = $state([]);
  let active = $state('');
  let open = $state(false);
  let busy = $state(false);
  let error = $state(null);

  // Create-Form-State. Wird via "+ Neuer Operator" sichtbar gemacht.
  let showCreate = $state(false);
  let newOp = $state({
    callsign: '',
    default_locator: '',
    default_power_w: 50,
    license_class: 'A',
    qrz_user: '',
    qrz_password: '',
    qrz_logbook_api_key: '',
    clublog_email: '',
    clublog_app_password: '',
  });
  let createError = $state(null);

  async function refresh() {
    try {
      const data = await api.operatorsList();
      operators = data.operators;
      active = data.active_callsign;
      error = null;
    } catch (e) {
      error = e.message;
    }
  }

  onMount(refresh);

  async function selectOperator(callsign) {
    if (callsign === active || busy) return;
    busy = true;
    error = null;
    try {
      await api.operatorSelect(callsign);
      await refresh();
      open = false;
      // Nach Switch: voller Page-Reload damit alle Components ihre
      // gefilterten Daten frisch laden (worked, log, blacklist, etc.).
      window.location.reload();
    } catch (e) {
      error = e.message;
    } finally {
      busy = false;
    }
  }

  function resetCreateForm() {
    newOp = {
      callsign: '', default_locator: '', default_power_w: 50,
      license_class: 'A', qrz_user: '', qrz_password: '', qrz_logbook_api_key: '',
      clublog_email: '', clublog_app_password: '',
    };
    createError = null;
  }

  async function submitCreate(e) {
    e.preventDefault();
    if (busy) return;
    busy = true;
    createError = null;
    try {
      // Leere Strings als null schicken — sonst schluckt die
      // Pydantic-Validierung sie als Wert (auch wenn Optional).
      const body = {
        callsign: newOp.callsign.trim().toUpperCase(),
        default_locator: newOp.default_locator.trim() || null,
        default_power_w: Number(newOp.default_power_w) || 10,
        license_class: newOp.license_class,
        qrz_user: newOp.qrz_user.trim() || null,
        qrz_password: newOp.qrz_password || null,
        qrz_logbook_api_key: newOp.qrz_logbook_api_key.trim() || null,
        clublog_email: newOp.clublog_email.trim() || null,
        clublog_app_password: newOp.clublog_app_password.trim() || null,
      };
      await api.operatorCreate(body);
      await refresh();
      showCreate = false;
      resetCreateForm();
    } catch (err) {
      createError = err.message;
    } finally {
      busy = false;
    }
  }

  async function deleteOperator(callsign) {
    if (busy) return;
    if (!confirm(`${callsign} wirklich loeschen?`)) return;
    busy = true;
    error = null;
    try {
      await api.operatorDelete(callsign, false);
      await refresh();
    } catch (err) {
      // Backend wirft 409 wenn QSOs in der DB sind — dann nochmal
      // mit force=true nachfragen.
      if (/QSOs in der DB/.test(err.message)) {
        if (confirm(`${callsign} hat QSO-Historie in der DB. Profil trotzdem loeschen? Die QSO-Rows bleiben (kannst den Operator mit gleichem Callsign neu anlegen und siehst die alten QSOs wieder).`)) {
          try {
            await api.operatorDelete(callsign, true);
            await refresh();
          } catch (err2) {
            error = err2.message;
          }
        }
      } else {
        error = err.message;
      }
    } finally {
      busy = false;
    }
  }
</script>

<div class="switcher" class:open>
  <button class="current" onclick={() => open = !open} disabled={busy}>
    <span class="cs">{active || '—'}</span>
    {#if operators.length > 1}
      <span class="caret">{open ? '▴' : '▾'}</span>
    {/if}
  </button>

  {#if open}
    <div class="dropdown">
      {#each operators as op, i (op.callsign)}
        <div class="item-row" class:active={op.is_active}>
          <button
            class="item"
            onclick={() => selectOperator(op.callsign)}
            disabled={busy}
          >
            <span class="cs">{op.callsign}</span>
            <span class="meta">
              {op.license_class}
              {#if op.default_locator}· {op.default_locator}{/if}
              · {op.default_power_w}W
              {#if op.has_qrz_credentials}· QRZ{/if}
            </span>
          </button>
          {#if !op.is_active}
            <button
              class="del"
              title="Operator loeschen"
              onclick={(e) => { e.stopPropagation(); deleteOperator(op.callsign); }}
              disabled={busy}
            >✕</button>
          {/if}
        </div>
      {/each}

      {#if !showCreate}
        <button class="add-btn" onclick={() => { showCreate = true; resetCreateForm(); }} disabled={busy}>
          + Neuer Operator
        </button>
      {:else}
        <form class="create-form" onsubmit={submitCreate}>
          <div class="form-title">Neuen Operator anlegen</div>
          <label>
            <span>Rufzeichen *</span>
            <input type="text" required bind:value={newOp.callsign}
                   placeholder="DK1ABC" autocapitalize="characters" />
          </label>
          <label>
            <span>Locator</span>
            <input type="text" bind:value={newOp.default_locator}
                   placeholder="JN58" />
          </label>
          <div class="row-2">
            <label>
              <span>Klasse</span>
              <select bind:value={newOp.license_class}>
                <option value="A">A (Klasse A — 750W HF)</option>
                <option value="E">E (Klasse E — 100W, eingeschr. Bänder)</option>
                <option value="N">N (Klasse N — 10W)</option>
              </select>
            </label>
            <label>
              <span>Standard-Power (W)</span>
              <input type="number" min="1" max="750" bind:value={newOp.default_power_w} />
            </label>
          </div>
          <div class="form-section">QRZ-Logbook (optional)</div>
          <label>
            <span>QRZ User</span>
            <input type="text" bind:value={newOp.qrz_user} placeholder="DK1ABC" />
          </label>
          <label>
            <span>QRZ Passwort</span>
            <input type="password" bind:value={newOp.qrz_password} />
          </label>
          <label>
            <span>QRZ Logbook API-Key</span>
            <input type="text" bind:value={newOp.qrz_logbook_api_key}
                   placeholder="XXXX-XXXX-XXXX-XXXX" />
          </label>
          <div class="form-section">ClubLog (optional)</div>
          <label>
            <span>ClubLog Email</span>
            <input type="email" bind:value={newOp.clublog_email}
                   placeholder="me@example.com" />
          </label>
          <label>
            <span>ClubLog Application Password</span>
            <input type="password" bind:value={newOp.clublog_app_password}
                   placeholder="xxx-xxxx-xxxx-..." />
          </label>
          {#if createError}
            <div class="error">⚠ {createError}</div>
          {/if}
          <div class="form-actions">
            <button type="button" onclick={() => { showCreate = false; resetCreateForm(); }}
                    disabled={busy}>Abbrechen</button>
            <button type="submit" class="primary" disabled={busy || !newOp.callsign}>
              Anlegen
            </button>
          </div>
        </form>
      {/if}

      {#if error}
        <div class="error">⚠ {error}</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .switcher { position: relative; display: inline-block; }
  .current {
    background: transparent; border: none; cursor: pointer;
    font-family: ui-monospace, monospace; font-size: 1rem;
    font-weight: 700; color: var(--accent);
    padding: 0.1rem 0.3rem; display: inline-flex; gap: 0.3rem;
    align-items: baseline;
  }
  .current:hover { opacity: 0.8; }
  .caret { font-size: 0.7rem; color: #94a3b8; }
  .dropdown {
    position: absolute; top: 100%; left: 0; z-index: 100;
    background: var(--panel); border: 1px solid #1e293b;
    border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    min-width: 280px; padding: 0.3rem 0; margin-top: 0.2rem;
  }
  .item-row {
    display: flex; align-items: stretch;
  }
  .item-row.active { background: rgba(56,189,248,0.2); border-left: 3px solid var(--accent); }
  .item-row:hover:not(.active) { background: rgba(56,189,248,0.1); }
  .item {
    flex: 1; background: transparent; border: none;
    text-align: left; padding: 0.5rem 0.8rem; cursor: pointer;
    color: var(--text); display: flex; flex-direction: column; gap: 0.15rem;
  }
  .item .cs {
    font-family: ui-monospace, monospace; font-weight: 700;
    color: var(--accent); font-size: 0.95rem;
  }
  .item .meta { font-size: 0.7rem; color: #94a3b8; }
  .del {
    background: transparent; border: none; color: #64748b; cursor: pointer;
    padding: 0 0.6rem; font-size: 0.9rem;
  }
  .del:hover { color: var(--danger); }
  .del:disabled { opacity: 0.3; cursor: not-allowed; }
  .add-btn {
    width: 100%; background: transparent; border: none;
    border-top: 1px solid #1e293b; padding: 0.6rem 0.8rem;
    color: var(--accent); cursor: pointer; text-align: left;
    font-size: 0.85rem;
  }
  .add-btn:hover { background: rgba(56,189,248,0.1); }
  .add-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .create-form {
    display: flex; flex-direction: column; gap: 0.4rem;
    padding: 0.6rem 0.8rem; border-top: 1px solid #1e293b;
  }
  .form-title { font-weight: 700; color: var(--accent); font-size: 0.85rem; }
  .form-section {
    font-size: 0.7rem; color: #94a3b8; text-transform: uppercase;
    letter-spacing: 0.05em; margin-top: 0.3rem;
  }
  .create-form label {
    display: flex; flex-direction: column; gap: 0.15rem; font-size: 0.7rem;
  }
  .create-form label span { color: #94a3b8; }
  .create-form input, .create-form select {
    background: rgba(15,23,42,0.6); border: 1px solid #334155;
    border-radius: 4px; padding: 0.3rem 0.45rem; color: var(--text);
    font-size: 0.8rem; font-family: inherit;
  }
  .create-form input:focus, .create-form select:focus {
    outline: none; border-color: var(--accent);
  }
  .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.4rem; }
  .form-actions {
    display: flex; justify-content: flex-end; gap: 0.4rem; margin-top: 0.3rem;
  }
  .form-actions button {
    background: rgba(15,23,42,0.6); border: 1px solid #334155;
    border-radius: 4px; padding: 0.35rem 0.7rem; color: var(--text);
    cursor: pointer; font-size: 0.75rem;
  }
  .form-actions button:hover { border-color: var(--accent); }
  .form-actions button.primary {
    background: var(--accent); color: #0f172a; border-color: var(--accent);
    font-weight: 700;
  }
  .form-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
  .error {
    padding: 0.3rem 0.8rem; color: var(--danger); font-size: 0.75rem;
    border-top: 1px solid #1e293b;
  }
</style>
