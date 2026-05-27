<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';

  let connections = $state([]);
  let scan = $state([]);
  let loading = $state(false);
  let scanning = $state(false);
  let error = $state(null);

  // "Add new network" form state
  let addOpen = $state(false);
  let newSsid = $state('');
  let newPsk = $state('');
  let newPriority = $state(50);
  let saving = $state(false);

  // AP-Fallback (Notfall-Hotspot): wenn alle gespeicherten WLANs
  // unerreichbar sind, öffnet der Pi sein eigenes WLAN unter diesem
  // Namen. User verbindet sich per Handy, kommt aufs Captive-Portal,
  // konfiguriert dort neu. Lebt hier statt im Config-Panel weil's
  // semantisch zum Netzwerk-Setup gehört.
  let apFb = $state({ ssid: '', psk: '' });
  let apSaving = $state(false);
  let apSavedFlash = $state(false);

  async function refresh() {
    loading = true; error = null;
    try {
      connections = await api.wifiConnections();
    } catch (e) { error = e.message; }
    finally { loading = false; }
  }

  async function refreshScan() {
    scanning = true;
    try {
      scan = await api.wifiScan();
    } catch (e) { error = e.message; }
    finally { scanning = false; }
  }

  async function loadApFallback() {
    try { apFb = await api.apFallbackGet(); }
    catch (e) { error = e.message; }
  }

  async function saveApFallback() {
    if (!apFb.ssid || apFb.psk.length < 8) {
      error = 'AP-Fallback: SSID + Passwort (mind. 8 Zeichen) nötig';
      return;
    }
    apSaving = true; error = null;
    try {
      await api.apFallbackSet({ ssid: apFb.ssid, psk: apFb.psk });
      apSavedFlash = true;
      setTimeout(() => apSavedFlash = false, 2500);
    } catch (e) { error = e.message; }
    finally { apSaving = false; }
  }

  onMount(async () => {
    await refresh();
    refreshScan();             // fire & forget, takes ~2s
    loadApFallback();
  });

  function pickFromScan(ap) {
    newSsid = ap.ssid;
    newPsk = '';
    addOpen = true;
    // Focus PSK field after Svelte updates DOM
    setTimeout(() => document.getElementById('wifi-new-psk')?.focus(), 50);
  }

  async function addConnection() {
    if (!newSsid) return;
    saving = true; error = null;
    try {
      await api.wifiAdd({ ssid: newSsid, psk: newPsk || null, priority: newPriority });
      newSsid = ''; newPsk = ''; newPriority = 50;
      addOpen = false;
      await refresh();
    } catch (e) { error = e.message; }
    finally { saving = false; }
  }

  async function deleteConnection(name) {
    if (!confirm(`WLAN-Profil "${name}" wirklich löschen?`)) return;
    error = null;
    try {
      await api.wifiDelete(name);
      await refresh();
    } catch (e) { error = e.message; }
  }

  async function activateConnection(name) {
    error = null;
    try {
      await api.wifiActivate(name);
      // Brief pause then refresh so the "active" flag updates.
      setTimeout(refresh, 1500);
    } catch (e) { error = e.message; }
  }

  async function changePriority(name, newPrio) {
    try {
      await api.wifiSetPriority(name, parseInt(newPrio, 10));
      await refresh();
    } catch (e) { error = e.message; }
  }

  function sigBars(sig) {
    if (sig >= 75) return '▂▄▆█';
    if (sig >= 50) return '▂▄▆_';
    if (sig >= 25) return '▂▄__';
    return '▂___';
  }
</script>

<div class="wrap">
  <header>
    <h2>WLAN</h2>
    <div class="actions-h">
      <button class="ghost" onclick={refresh} disabled={loading}>
        {loading ? '…' : '↻ Profile'}
      </button>
      <button class="ghost" onclick={refreshScan} disabled={scanning}>
        {scanning ? 'Scanne…' : '🔍 Scan'}
      </button>
      <button class="primary" onclick={() => addOpen = !addOpen}>
        {addOpen ? '× Abbrechen' : '＋ Neues WLAN'}
      </button>
    </div>
  </header>

  {#if error}<div class="err">⚠ {error}</div>{/if}

  <!-- Add-form -->
  {#if addOpen}
    <section class="add-form">
      <div class="grid">
        <label><span>SSID</span>
          <input type="text" bind:value={newSsid} placeholder="Heim-WLAN"/>
        </label>
        <label><span>Passwort (leer = offenes WLAN)</span>
          <input id="wifi-new-psk" type="password" bind:value={newPsk}/>
        </label>
        <label><span>Priorität (höher = bevorzugt)</span>
          <input type="number" bind:value={newPriority} min="-999" max="999"/>
        </label>
      </div>
      <button class="primary" onclick={addConnection} disabled={!newSsid || saving}>
        {saving ? 'Speichere…' : 'Hinzufügen'}
      </button>
    </section>
  {/if}

  <!-- Saved profiles -->
  <section>
    <h3>Gespeicherte Profile</h3>
    {#if connections.length === 0 && !loading}
      <p class="empty">Noch keine WLAN-Profile gespeichert.</p>
    {:else}
      <table>
        <thead>
          <tr>
            <th>SSID</th>
            <th>Prio</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each connections as c}
            <tr class:active={c.active}>
              <td class="ssid">
                {#if c.active}<span class="dot ok" title="verbunden"></span>{/if}
                {c.ssid}
              </td>
              <td>
                <input type="number" value={c.priority}
                       onchange={(e) => changePriority(c.name, e.target.value)}
                       min="-999" max="999" style="width: 4.5rem"/>
              </td>
              <td>
                {#if c.active}
                  <span class="badge ok">aktiv</span>
                {:else if c.autoconnect}
                  <span class="badge">auto</span>
                {:else}
                  <span class="badge muted">manuell</span>
                {/if}
              </td>
              <td class="row-actions">
                {#if !c.active}
                  <button class="ghost-sm" onclick={() => activateConnection(c.name)}>
                    verbinden
                  </button>
                {/if}
                <button class="rm" onclick={() => deleteConnection(c.name)} title="Löschen">×</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>

  <!-- AP-Fallback (Notfall-Hotspot) -->
  <section class="ap-fallback">
    <h3>📡 Notfall-Hotspot</h3>
    <div class="grid">
      <label><span>SSID</span>
        <input type="text" bind:value={apFb.ssid} placeholder="ft8-hochgericht"
               maxlength="32"/>
      </label>
      <label><span>Passwort (8–63 Zeichen)</span>
        <input type="text" bind:value={apFb.psk} placeholder="mind. 8 Zeichen"
               maxlength="63"/>
      </label>
    </div>
    <button class="primary" onclick={saveApFallback} disabled={apSaving}>
      {apSaving ? 'Speichere…' : (apSavedFlash ? '✓ Gespeichert' : 'AP-Fallback speichern')}
    </button>
  </section>

  <!-- Scan results -->
  <section>
    <h3>In Reichweite {#if scanning}<small style="font-weight:400">(scanne…)</small>{/if}</h3>
    {#if scan.length === 0 && !scanning}
      <p class="empty">Keine WLANs in Reichweite. Sind WiFi-Radio + Antenne an?</p>
    {:else}
      <table>
        <thead>
          <tr><th>SSID</th><th>Signal</th><th>Sicherheit</th><th></th></tr>
        </thead>
        <tbody>
          {#each scan as ap}
            <tr>
              <td class="ssid">{ap.ssid}</td>
              <td><span class="bars">{sigBars(ap.signal)}</span> {ap.signal}</td>
              <td>{ap.security === '--' ? 'offen' : ap.security}</td>
              <td class="row-actions">
                {#if !connections.some(c => c.ssid === ap.ssid)}
                  <button class="ghost-sm" onclick={() => pickFromScan(ap)}>＋ hinzufügen</button>
                {:else}
                  <small class="muted">bereits gespeichert</small>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
</div>

<style>
  .wrap { background: var(--panel); border-radius: 8px; padding: 0.8rem; }
  header { display: flex; justify-content: space-between; align-items: center;
           margin-bottom: 0.8rem; }
  .actions-h { display: flex; gap: 0.4rem; }
  h2 { margin: 0; color: var(--accent); font-size: 1rem; }
  h3 { margin: 1rem 0 0.4rem; color: var(--accent); font-size: 0.9rem; }
  section { margin-bottom: 0.6rem; padding: 0.6rem;
            background: rgba(15,23,42,0.4); border-radius: 6px; }
  section.ap-fallback .grid { display: grid;
                              grid-template-columns: 1fr 1fr;
                              gap: 0.5rem; margin: 0.4rem 0 0.6rem; }
  section.ap-fallback label { display: flex; flex-direction: column; gap: 0.2rem; }
  section.ap-fallback label span { color: #94a3b8; font-size: 0.75rem; }
  section.ap-fallback input { background: #0b1220; color: var(--fg);
                              border: 1px solid #334155; border-radius: 4px;
                              padding: 0.35rem; }
  section.ap-fallback .hint { color: #94a3b8; font-size: 0.8rem; margin: 0.2rem 0; }
  section.ap-fallback .primary { padding: 0.4rem 0.8rem; }
  @media (max-width: 640px) {
    section.ap-fallback .grid { grid-template-columns: 1fr; }
  }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.35rem 0.5rem; font-size: 0.85rem; }
  th { color: #94a3b8; font-weight: 500; text-transform: uppercase;
       letter-spacing: 0.05em; font-size: 0.7rem; }
  td.ssid { font-family: ui-monospace, monospace; }
  tr.active td { background: rgba(34,197,94,0.05); }
  .dot { display: inline-block; width: 0.55rem; height: 0.55rem;
         border-radius: 50%; margin-right: 0.3rem; vertical-align: middle; }
  .dot.ok { background: var(--ok); }
  .badge { padding: 0.1rem 0.45rem; border-radius: 999px; font-size: 0.7rem;
           background: #1e293b; color: #94a3b8; }
  .badge.ok { background: rgba(34,197,94,0.2); color: var(--ok); }
  .badge.muted { background: transparent; border: 1px solid #334155; }
  .row-actions { text-align: right; white-space: nowrap; }
  .ghost {
    background: transparent; color: var(--fg); border: 1px solid #334155;
    border-radius: 5px; padding: 0.3rem 0.7rem; font-size: 0.8rem; cursor: pointer;
  }
  .ghost-sm {
    background: transparent; color: var(--accent); border: 1px solid #334155;
    border-radius: 4px; padding: 0.15rem 0.5rem; font-size: 0.75rem; cursor: pointer;
    margin-right: 0.3rem;
  }
  .primary {
    background: var(--accent); color: #0f172a; border: none;
    border-radius: 5px; padding: 0.3rem 0.8rem; font-weight: 600; font-size: 0.85rem;
    cursor: pointer;
  }
  .rm {
    background: transparent; color: var(--danger); border: 1px solid #334155;
    border-radius: 4px; width: 1.6rem; height: 1.6rem; cursor: pointer;
  }
  .bars { font-family: ui-monospace, monospace; color: var(--accent); }
  .empty { color: #94a3b8; font-style: italic; font-size: 0.85rem; }
  .err { color: var(--danger); margin-bottom: 0.6rem; font-size: 0.85rem; }
  .muted { color: #94a3b8; }
  .add-form .grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
    gap: 0.5rem; margin-bottom: 0.6rem;
  }
  .add-form label { display: flex; flex-direction: column; gap: 0.2rem; }
  .add-form label span { color: #94a3b8; font-size: 0.75rem; text-transform: uppercase;
                          letter-spacing: 0.05em; }
  .add-form input {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 4px; padding: 0.35rem 0.5rem; font-size: 0.9rem;
  }
  .hint { display: block; color: #94a3b8; font-size: 0.75rem; margin-top: 0.4rem; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  input[type=number] {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 4px; padding: 0.2rem 0.4rem; font-size: 0.85rem;
  }
</style>
