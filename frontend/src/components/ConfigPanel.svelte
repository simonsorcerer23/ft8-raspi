<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';

  // Stock max-power per rig. Mirrors backend RigConfig._RIG_TABLE — used
  // to bound the default-power slider when the operator switches rig.
  const RIG_DEFAULTS = {
    ic705:    { label: 'Icom IC-705 (QRP, 10 W)',         max_power_w: 10  },
    ic7300:   { label: 'Icom IC-7300 (Desktop, 100 W)',   max_power_w: 100 },
    ic9700:   { label: 'Icom IC-9700 (VHF/UHF, 100 W)',   max_power_w: 100 },
    ic7610:   { label: 'Icom IC-7610 (Top-Class, 100 W)', max_power_w: 100 },
    qmx_plus: { label: 'QRP Labs QMX/QMX+ (QRP, 5 W)',    max_power_w: 5   },
  };
  // Deutsche Lizenzklassen — Label fürs Dropdown.
  const LICENSE_LABELS = {
    A: 'Klasse A — Volllizenz (alle Bänder, 750W)',
    E: 'Klasse E — Einsteiger (80/15/10/2/70cm, 100W HF)',
    N: 'Klasse N — Newcomer (160/2/70cm, 10W)',
  };

  let cfg = $state(null);
  let error = $state(null);
  let saving = $state(false);
  let saved = $state(false);
  let yamlMode = $state(false);
  let yamlText = $state('');
  let detecting = $state(false);
  let detectMsg = $state(null);

  async function detectRig() {
    detecting = true; detectMsg = null;
    try {
      const r = await api.detectRig();
      const cands = r.candidates || [];
      if (cands.length === 0) {
        detectMsg = { kind: 'warn', text: 'Kein bekanntes Rig am USB gefunden.' };
      } else {
        const top = cands[0];
        cfg.rig.model = top.model;
        cfg.rig.serial_device = top.serial_device;
        cfg.rig.max_power_w = null;  // reset override → rig stock max
        const cap = RIG_DEFAULTS[top.model].max_power_w;
        if (cfg.operator.default_power_w > cap) cfg.operator.default_power_w = cap;
        detectMsg = { kind: 'ok', text: `Erkannt: ${top.description}` };
      }
    } catch (e) {
      detectMsg = { kind: 'warn', text: `Erkennung fehlgeschlagen: ${e.message}` };
    } finally { detecting = false; }
  }

  // Effective max-power for the slider: explicit override wins over the
  // rig's stock max.
  function powerCap(c) {
    return c?.rig?.max_power_w ?? RIG_DEFAULTS[c?.rig?.model ?? 'ic705'].max_power_w;
  }

  function configToYaml(c) {
    const ind = (n) => ' '.repeat(n);
    // Defensive: Copy-Paste aus Webseiten (z.B. QRZ-Logbook-API-Key)
    // schleppt oft Tabs + Whitespace mit, was den YAML-Parser zerlegt.
    // Plus Sonderzeichen wie : oder # müssen quotiert sein.
    const yq = (v) => {
      if (v == null) return '""';
      const t = String(v).replace(/[\t\r\n]/g, '').trim();
      // Quote wenn leer oder Sonderzeichen drin — sonst lassen wir
      // den Default-Stil unangetastet (Lesbarkeit).
      if (t === '' || /[:#&*!|>'"%@`,\[\]\{\}]/.test(t)) {
        return `"${t.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
      }
      return t;
    };
    let s = '';
    s += `operator:\n`;
    s += `${ind(2)}callsign: ${yq(c.operator.callsign)}\n`;
    if (c.operator.default_locator)
      s += `${ind(2)}default_locator: ${yq(c.operator.default_locator)}\n`;
    s += `${ind(2)}default_power_w: ${c.operator.default_power_w}\n`;
    if (c.operator.license_class)
      s += `${ind(2)}license_class: ${c.operator.license_class}\n`;
    if (c.rig) {
      s += `\nrig:\n`;
      s += `${ind(2)}model: ${c.rig.model}\n`;
      s += `${ind(2)}serial_device: "${c.rig.serial_device}"\n`;
      s += `${ind(2)}cat_baud: ${c.rig.cat_baud}\n`;
      if (c.rig.max_power_w != null)
        s += `${ind(2)}max_power_w: ${c.rig.max_power_w}\n`;
      if (c.rig.audio_card_hint)
        s += `${ind(2)}audio_card_hint: "${c.rig.audio_card_hint}"\n`;
    }
    if (c.bands?.length) {
      s += `\nbands:\n`;
      for (const b of c.bands) {
        s += `${ind(2)}- { name: "${b.name}", freq_khz: ${b.freq_khz} }\n`;
      }
    }
    if (c.antennas?.length) {
      s += `\nantennas:\n`;
      for (const a of c.antennas) {
        s += `${ind(2)}- { name: ${a.name}, bands: [${a.bands.map(x => `"${x}"`).join(', ')}] }\n`;
      }
    }
    s += `\noperating:\n`;
    // Emittiere ALLE Felder die das Backend kennt — sonst füllt
    // Pydantic die fehlenden mit Defaults und überschreibt die
    // Werte des Operators bei jedem Speichern.
    s += `${ind(2)}mode: ${c.operating.mode}\n`;
    s += `${ind(2)}auto_cq_interval_s: ${c.operating.auto_cq_interval_s}\n`;
    s += `${ind(2)}max_ptt_s: ${c.operating.max_ptt_s}\n`;
    s += `${ind(2)}cq_idle_timeout_min: ${c.operating.cq_idle_timeout_min}\n`;
    s += `${ind(2)}swr_max: ${c.operating.swr_max}\n`;
    s += `${ind(2)}alc_max: ${c.operating.alc_max}\n`;
    s += `${ind(2)}audio_gain: ${c.operating.audio_gain}\n`;
    s += `${ind(2)}alc_target_low: ${c.operating.alc_target_low}\n`;
    s += `${ind(2)}alc_target_high: ${c.operating.alc_target_high}\n`;
    s += `${ind(2)}qso_cooldown_min: ${c.operating.qso_cooldown_min}\n`;
    s += `${ind(2)}qso_max_stale_slots: ${c.operating.qso_max_stale_slots}\n`;
    s += `${ind(2)}hunt_skip_worked: ${c.operating.hunt_skip_worked}\n`;
    s += `${ind(2)}hunt_dxcc_only: ${c.operating.hunt_dxcc_only}\n`;
    s += `${ind(2)}boot_mode: ${c.operating.boot_mode || 'off'}\n`;
    s += `${ind(2)}mode_watchdog_min: ${c.operating.mode_watchdog_min}\n`;
    s += `${ind(2)}public_hostname: ${yq(c.operating.public_hostname || 'ft8')}\n`;
    s += `\nintegrations:\n`;
    s += `${ind(2)}qrz:\n`;
    s += `${ind(4)}enabled: ${c.integrations.qrz.enabled}\n`;
    if (c.integrations.qrz.user) s += `${ind(4)}user: ${yq(c.integrations.qrz.user)}\n`;
    if (c.integrations.qrz.password) s += `${ind(4)}password: ${yq(c.integrations.qrz.password)}\n`;
    if (c.integrations.qrz.logbook_api_key) s += `${ind(4)}logbook_api_key: ${yq(c.integrations.qrz.logbook_api_key)}\n`;
    s += `${ind(4)}logbook_auto_upload: ${c.integrations.qrz.logbook_auto_upload}\n`;
    s += `${ind(2)}hamqth:\n${ind(4)}enabled: ${c.integrations.hamqth.enabled}\n`;
    s += `${ind(2)}psk_reporter:\n`;
    s += `${ind(4)}enabled: ${c.integrations.psk_reporter.enabled}\n`;
    s += `${ind(4)}upload_decodes: ${c.integrations.psk_reporter.upload_decodes}\n`;
    s += `${ind(2)}hamqsl:\n${ind(4)}enabled: ${c.integrations.hamqsl.enabled}\n`;
    s += `${ind(2)}blitzortung:\n`;
    s += `${ind(4)}enabled: ${c.integrations.blitzortung.enabled}\n`;
    s += `${ind(4)}alarm_radius_km: ${c.integrations.blitzortung.alarm_radius_km}\n`;
    s += `${ind(2)}ntfy:\n${ind(4)}enabled: ${c.integrations.ntfy.enabled}\n`;
    if (c.integrations.ntfy.topic) s += `${ind(4)}topic: ${yq(c.integrations.ntfy.topic)}\n`;
    if (c.integrations.dx_cluster) {
      s += `${ind(2)}dx_cluster:\n`;
      s += `${ind(4)}enabled: ${c.integrations.dx_cluster.enabled}\n`;
      s += `${ind(4)}host: ${yq(c.integrations.dx_cluster.host)}\n`;
      s += `${ind(4)}port: ${c.integrations.dx_cluster.port}\n`;
    }
    if (c.network && c.network.ap_fallback) {
      s += `\nnetwork:\n`;
      s += `${ind(2)}ap_fallback:\n`;
      s += `${ind(4)}ssid: ${yq(c.network.ap_fallback.ssid)}\n`;
      s += `${ind(4)}psk: ${yq(c.network.ap_fallback.psk)}\n`;
    }
    // ui.language / ui.theme werden nicht mehr im Frontend exponiert,
    // aber der Backend-Schema-Default hält die Felder noch — wir lassen
    // sie hier weg, das Backend füllt mit Default auf.
    return s;
  }

  onMount(async () => {
    try { cfg = await api.config(); yamlText = configToYaml(cfg); }
    catch (e) { error = e.message; }
  });

  async function save() {
    saving = true; error = null; saved = false;
    try {
      const yaml = yamlMode ? yamlText : configToYaml(cfg);
      const r = await api.saveConfig(yaml);
      cfg = r;
      yamlText = configToYaml(cfg);
      saved = true;
      setTimeout(() => { saved = false; }, 3000);
    } catch (e) { error = e.message; }
    finally { saving = false; }
  }

  function addBand() {
    cfg.bands = [...(cfg.bands || []),
                 { name: '20m', freq_khz: 14074 }];
  }
  function removeBand(i) { cfg.bands = cfg.bands.filter((_, j) => j !== i); }

  function addAntenna() {
    cfg.antennas = [...(cfg.antennas || []), { name: 'new', bands: ['20m'] }];
  }
  function removeAntenna(i) { cfg.antennas = cfg.antennas.filter((_, j) => j !== i); }
</script>

<div class="wrap">
  <header>
    <h2>Konfiguration</h2>
    <label class="mode-toggle">
      <input type="checkbox" bind:checked={yamlMode}/>
      <span>YAML-Modus</span>
    </label>
  </header>

  {#if !cfg && !error}
    <p class="empty">Lade Konfig…</p>
  {:else if cfg}
    {#if yamlMode}
      <textarea bind:value={yamlText} spellcheck="false"></textarea>
    {:else}
      <!-- Operator -->
      <section>
        <h3>Operator</h3>
        <div class="grid">
          <label>
            <span>Rufzeichen</span>
            <input type="text" bind:value={cfg.operator.callsign}
                   style="text-transform: uppercase; font-family: ui-monospace, monospace"/>
          </label>
          <label>
            <span>Locator (leer = GPS)</span>
            <input type="text" bind:value={cfg.operator.default_locator}
                   placeholder="JN58td" maxlength="6"
                   style="font-family: ui-monospace, monospace"/>
          </label>
          <label>
            <span>TX-Power Default (W, max {powerCap(cfg)})</span>
            <input type="number" bind:value={cfg.operator.default_power_w}
                   min="1" max={powerCap(cfg)}/>
          </label>
          <label>
            <span>Lizenzklasse (BNetzA AFuV)</span>
            <select bind:value={cfg.operator.license_class}>
              {#each Object.entries(LICENSE_LABELS) as [id, label]}
                <option value={id}>{label}</option>
              {/each}
            </select>
          </label>
        </div>
        <p class="hint">
          ⚠️ Lizenz-Cap wird beim TX hart erzwungen. Klasse E darf z.B. nicht auf 40m/30m/20m/17m/12m,
          Klasse A auf 60m nur 15W EIRP.
        </p>
      </section>

      <!-- Rig -->
      {#if cfg.rig}
      <section>
        <h3>Funkgerät
          <button class="add" onclick={detectRig} disabled={detecting}>
            {detecting ? '🔍…' : '🔍 Auto-Detect'}
          </button>
          {#if detectMsg}
            <small style:color={detectMsg.kind === 'ok' ? 'var(--ok)' : 'var(--danger)'}
                   style="font-size: 0.75rem; margin-left: 0.5rem;">
              {detectMsg.text}
            </small>
          {/if}
        </h3>
        <div class="grid">
          <label>
            <span>Modell</span>
            <select bind:value={cfg.rig.model} onchange={() => {
              // Snap default_power_w into the new rig's max when switching.
              const cap = RIG_DEFAULTS[cfg.rig.model].max_power_w;
              if (cfg.operator.default_power_w > cap) cfg.operator.default_power_w = cap;
              // Clear an explicit override so the rig's stock max kicks in.
              cfg.rig.max_power_w = null;
              // Suggest a sensible default serial-by-id for known rigs.
              if (cfg.rig.model === 'ic705')
                cfg.rig.serial_device = '/dev/serial/by-id/usb-Icom_Inc._IC-705-if00';
              else if (cfg.rig.model === 'ic7300')
                cfg.rig.serial_device = '/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller-if00-port0';
            }}>
              {#each Object.entries(RIG_DEFAULTS) as [id, info]}
                <option value={id}>{info.label}</option>
              {/each}
            </select>
          </label>
          <label>
            <span>CAT-Serial-Device</span>
            <input type="text" bind:value={cfg.rig.serial_device}
                   style="font-family: ui-monospace, monospace; font-size: 0.78rem"/>
          </label>
          <label>
            <span>CAT-Baud</span>
            <select bind:value={cfg.rig.cat_baud}>
              <option value={4800}>4800</option>
              <option value={9600}>9600</option>
              <option value={19200}>19200</option>
              <option value={38400}>38400</option>
              <option value={57600}>57600</option>
              <option value={115200}>115200</option>
            </select>
          </label>
          <label>
            <span>Max-Power Override (leer = Rig-Default)</span>
            <input type="number" placeholder={String(RIG_DEFAULTS[cfg.rig.model].max_power_w)}
                   bind:value={cfg.rig.max_power_w} min="1" max="200"/>
          </label>
        </div>
      </section>
      {/if}

      <!-- Bänder (definieren erst die Frequenzen) -->
      <section>
        <h3>Bänder <button class="add" onclick={addBand}>+ Band</button></h3>
        <p class="hint">
          Bänder sind die Funkbereiche mit ihrer Standard-FT8-Dial-Frequenz.
          Welche Antenne ein Band abdeckt, wird darunter pro Antenne eingestellt.
        </p>
        {#each cfg.bands as b, i}
          <div class="row">
            <input type="text" bind:value={b.name} placeholder="20m" style="max-width: 5rem"/>
            <input type="number" bind:value={b.freq_khz} placeholder="14074" min="1800"/>
            <button class="rm" onclick={() => removeBand(i)}>×</button>
          </div>
        {/each}
      </section>

      <!-- Antennen (verweisen auf Bänder die sie abdecken) -->
      <section>
        <h3>Antennen <button class="add" onclick={addAntenna}>+ Antenne</button></h3>
        <p class="hint">
          Pro Antenne ankreuzen welche der oben definierten Bänder sie
          resonant abdeckt. Der TX-Lockout greift wenn das aktuelle Band
          von keiner Antenne abgedeckt ist.
        </p>
        {#each cfg.antennas as a, i}
          <div class="ant-row">
            <input type="text" bind:value={a.name} placeholder="endfed_2040"
                   class="ant-name"/>
            <div class="band-chips">
              {#each cfg.bands as b (b.name)}
                <label class="chip">
                  <input type="checkbox"
                         checked={a.bands.includes(b.name)}
                         onchange={(e) => {
                           if (e.target.checked) {
                             if (!a.bands.includes(b.name)) a.bands = [...a.bands, b.name];
                           } else {
                             a.bands = a.bands.filter(x => x !== b.name);
                           }
                         }}/>
                  <span>{b.name}</span>
                </label>
              {/each}
            </div>
            <button class="rm" onclick={() => removeAntenna(i)}>×</button>
          </div>
        {/each}
      </section>

      <!-- Operating -->
      <section>
        <h3>Operating</h3>
        <div class="grid">
          <label><span>Auto-CQ Intervall (s)</span>
            <input type="number" bind:value={cfg.operating.auto_cq_interval_s} min="15" max="300"/>
          </label>
          <label><span>Max PTT (s)</span>
            <input type="number" bind:value={cfg.operating.max_ptt_s} min="15" max="60"/>
          </label>
          <label><span>Max SWR</span>
            <input type="number" step="0.1" bind:value={cfg.operating.swr_max} min="1" max="5"/>
          </label>
          <label><span>QSO-Cooldown (min) — Station nicht erneut anrufen</span>
            <input type="number" min="0" max="1440"
                   bind:value={cfg.operating.qso_cooldown_min}/>
          </label>
          <label><span>QSO-Geduld (Slots) — wie oft bei Antwort-Ausbleiben wiederholen</span>
            <input type="number" min="2" max="20"
                   bind:value={cfg.operating.qso_max_stale_slots}/>
          </label>
          <p class="hint" style="grid-column: 1/-1">
            Die Hunting-Filter (nur ungerufene / nur neue DXCC) findest du
            jetzt direkt auf dem Funk-Dashboard unter dem Antworten-Button —
            so kannst du sie live umschalten ohne hier rein zu müssen.
          </p>
        </div>
        <h4>Auto-ALC (Audio-Gain-Regelung beim TX)</h4>
        <p class="hint">
          Der Controller misst während des Sendens die ALC-Anzeige des
          Rigs und passt die Audio-Lautstärke automatisch an. Über
          dem oberen Limit fährt er um 10 % runter, unter dem unteren
          um 5 % hoch. Werte in <strong>%</strong> der ALC-Anzeige
          (0–100). FT8 mag <strong>ALC = 0–5 %</strong> — alles darüber
          ist Verzerrung am Sender.
        </p>
        <div class="grid">
          <label><span>ALC-Ziel unten (% — Audio rauf wenn ALC darunter)</span>
            <input type="number" min="0" max="50"
                   bind:value={cfg.operating.alc_target_low}/>
          </label>
          <label><span>ALC-Ziel oben (% — Audio runter wenn ALC darüber)</span>
            <input type="number" min="0" max="80"
                   bind:value={cfg.operating.alc_target_high}/>
          </label>
          <label><span>Start-Audio-Gain (0.0–1.0)</span>
            <input type="number" step="0.05" min="0.1" max="1.0"
                   bind:value={cfg.operating.audio_gain}/>
          </label>
        </div>
      </section>

      <!-- Integrations -->
      <section>
        <h3>Online-Dienste</h3>
        <div class="grid">
          <label class="check">
            <input type="checkbox" bind:checked={cfg.integrations.qrz.enabled}/>
            <span>QRZ.com (Dads Abo)</span>
          </label>
          <label class="check">
            <input type="checkbox" bind:checked={cfg.integrations.hamqth.enabled}/>
            <span>HamQTH (kostenlos)</span>
          </label>
          <label class="check">
            <input type="checkbox" bind:checked={cfg.integrations.psk_reporter.enabled}/>
            <span>PSK Reporter</span>
          </label>
          <label class="check">
            <input type="checkbox" bind:checked={cfg.integrations.psk_reporter.upload_decodes}/>
            <span>Decodes hochladen</span>
          </label>
          <label class="check">
            <input type="checkbox" bind:checked={cfg.integrations.hamqsl.enabled}/>
            <span>hamqsl Solar</span>
          </label>
          <label class="check">
            <input type="checkbox" bind:checked={cfg.integrations.blitzortung.enabled}/>
            <span>Blitzortung-Warnung</span>
          </label>
          {#if cfg.integrations.dx_cluster}
            <label class="check">
              <input type="checkbox" bind:checked={cfg.integrations.dx_cluster.enabled}/>
              <span>DX-Cluster (Telnet)</span>
            </label>
          {/if}
        </div>
        {#if cfg.integrations.qrz.enabled}
          <div class="grid">
            <label><span>QRZ User</span>
              <input type="text" bind:value={cfg.integrations.qrz.user}/>
            </label>
            <label><span>QRZ Passwort</span>
              <input type="password" bind:value={cfg.integrations.qrz.password}/>
            </label>
            <label><span>QRZ Logbook API-Key</span>
              <input type="password"
                     bind:value={cfg.integrations.qrz.logbook_api_key}
                     placeholder="aus QRZ-Logbook-Settings"/>
            </label>
            <label class="check">
              <input type="checkbox"
                     bind:checked={cfg.integrations.qrz.logbook_auto_upload}/>
              <span>QSOs automatisch hochladen (auch nach Offline-Phase)</span>
            </label>
          </div>
        {/if}
        {#if cfg.integrations.ntfy.enabled}
          <div class="grid">
            <label><span>ntfy.sh Topic</span>
              <input type="text" bind:value={cfg.integrations.ntfy.topic}
                     placeholder="ft8-hochgericht-xyz"/>
            </label>
          </div>
        {/if}
        <label class="check">
          <input type="checkbox" bind:checked={cfg.integrations.ntfy.enabled}/>
          <span>Push-Notifications via ntfy.sh</span>
        </label>
      </section>

      <!-- Netzwerk-Fallback ist auf die WLAN-Seite umgezogen
           (sinnvoll gebündelt mit den anderen Netzwerk-Settings).
           Sprache + Theme entfernt: die Dropdowns hatten keine
           Implementierung dahinter, UI ist hartkodiert deutsch/dark.
           Wenn wir mal i18n und Light-Mode bauen, kommen sie zurück. -->
    {/if}

    <div class="actions">
      <button class="primary" onclick={save} disabled={saving}>
        {saving ? 'Speichere…' : 'Speichern'}
      </button>
      {#if saved}<span class="status ok">✅ gespeichert</span>{/if}
      {#if error}<span class="status err">⚠ {error}</span>{/if}
    </div>
  {/if}
</div>

<style>
  .wrap { background: var(--panel); border-radius: 8px; padding: 0.8rem; }
  header { display: flex; justify-content: space-between; align-items: center;
           margin-bottom: 0.7rem; }
  h2 { margin: 0; color: var(--accent); font-size: 1rem; }
  h3 { margin: 1rem 0 0.4rem; color: var(--accent); font-size: 0.9rem;
       display: flex; align-items: center; gap: 0.7rem; }
  section {
    margin-bottom: 0.4rem; padding: 0.6rem;
    background: rgba(15,23,42,0.4); border-radius: 6px;
  }
  .grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
    gap: 0.5rem;
  }
  label { display: flex; flex-direction: column; gap: 0.2rem;
          font-size: 0.85rem; color: #cbd5e1; }
  label.check { flex-direction: row; align-items: center; gap: 0.4rem; }
  label span { color: #94a3b8; font-size: 0.75rem; text-transform: uppercase;
               letter-spacing: 0.05em; }
  input[type=text], input[type=number], input[type=password], select {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 4px; padding: 0.35rem 0.5rem; font-size: 0.9rem;
  }
  .row { display: flex; gap: 0.4rem; margin-bottom: 0.3rem; align-items: center; }
  .row input { flex: 1; min-width: 4rem; }
  .add {
    background: transparent; color: var(--accent); border: 1px solid var(--accent);
    border-radius: 4px; padding: 0.15rem 0.5rem; font-size: 0.75rem; cursor: pointer;
  }
  .rm {
    background: transparent; color: var(--danger); border: 1px solid #334155;
    border-radius: 4px; width: 1.8rem; cursor: pointer; font-size: 1.1rem;
  }
  /* Antennen-Block: Name + Band-Chips + Remove-Button */
  .ant-row {
    display: flex; gap: 0.5rem; align-items: flex-start;
    padding: 0.5rem 0; flex-wrap: wrap;
  }
  .ant-name { min-width: 9rem; }
  .band-chips {
    display: flex; flex-wrap: wrap; gap: 0.3rem;
    flex: 1; min-width: 14rem;
  }
  .chip {
    display: inline-flex; align-items: center; gap: 0.25rem;
    background: rgba(15,23,42,0.6); border: 1px solid #334155;
    border-radius: 999px; padding: 0.15rem 0.55rem;
    font-size: 0.8rem; font-family: ui-monospace, monospace;
    cursor: pointer; user-select: none;
  }
  .chip input { margin: 0; cursor: pointer; }
  .chip:has(input:checked) {
    background: rgba(56,189,248,0.15); border-color: var(--accent);
    color: var(--accent);
  }
  .hint {
    color: #94a3b8; font-size: 0.85rem; margin: 0 0 0.5rem;
  }
  textarea {
    width: 100%; min-height: 22rem; resize: vertical;
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 6px; padding: 0.7rem;
    font-family: ui-monospace, monospace; font-size: 0.9rem;
  }
  .mode-toggle { display: flex; align-items: center; gap: 0.3rem;
                 font-size: 0.85rem; color: #94a3b8; cursor: pointer; }
  .actions { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap;
             margin-top: 1rem; }
  .primary {
    background: var(--accent); color: #0f172a; border: none;
    border-radius: 6px; padding: 0.5rem 1.2rem; font-weight: 600; cursor: pointer;
  }
  .primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .status.ok { color: var(--ok); }
  .status.err { color: var(--danger); }
  .empty { color: #94a3b8; font-style: italic; }
</style>
