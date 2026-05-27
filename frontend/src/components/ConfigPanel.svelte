<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import SystemUpdateCard from './SystemUpdateCard.svelte';

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
  // v0.10.0 Hunt-Priority-Tier-Labels (deutsch, kompakt) + Icons.
  // Muss synchron sein mit HUNT_TIERS in backend/statemachine/machine.py.
  const TIER_ICONS = {
    not_bad_reputation: '✋',
    not_his_tx_slot:    '⏱️',
    not_in_pileup:      '🌪️',
    marine_psk:      '⚓📡',
    marine:          '⚓',
    tail_end_target: '🎯',
    grayline:        '🌅',
    band_open:       '☀️',
    active_hour:     '⏰',
    buddy_seen:      '🤝',
    new_dxcc_psk:    '🆕📡',
    new_dxcc:        '🆕',
    psk_heard_us:    '📡',
    new_dxcc_band:   '🌐',
    new_grid:        '📍',
    new_grid_band:   '📍🌐',
    not_worked:      '❄️',
    dxcc_rarity:     '⭐',
    snr:             '📶',
  };
  const TIER_LABELS = {
    not_bad_reputation: 'Soft-Blacklist meiden (Bail-Reason-aware)',
    not_his_tx_slot:    'Nicht in SEINEM TX-Slot anrufen',
    not_in_pileup:      'Pile-Up meiden (rare DX mit vielen Callern)',
    marine_psk:      'Marinefunker + PSK sagt "hört uns"',
    marine:          'Marinefunker (auch ohne PSK)',
    tail_end_target: 'Tail-End: Station hat gerade QSO beendet',
    grayline:        'Grayline: Station in eigener Dämmerung',
    band_open:       'Band laut hamqsl gerade "Good"',
    active_hour:     'Aktuelle Stunde laut DB-History für Kontinent aktiv',
    buddy_seen:      'Schon gearbeitet (anderes Band) — RX-Pfad bekannt',
    new_dxcc_psk:    'Neues DXCC + PSK sagt "hört uns"',
    new_dxcc:        'Neues DXCC (auch ohne PSK)',
    psk_heard_us:    'PSK sagt "hört uns" (Asymmetrie ausnutzen)',
    new_dxcc_band:   'Neues Band für DXCC (5BWAS)',
    new_grid:        'Neues Maidenhead-Grid (VUCC)',
    new_grid_band:   'Neues Grid auf diesem Band',
    not_worked:      'Noch nie gearbeitet',
    dxcc_rarity:     'DXCC-Rarity-Bonus',
    snr:             'SNR (bestes Signal als Tie-Breaker)',
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
      // YAML-1.1-Magic-Booleans MUESSEN quoted werden sonst werden
      // sie als bool false/true interpretiert (Sebastian-Bug v0.4.4
      // wiedergekehrt v0.6.1: boot_mode: off → bool False → Pydantic-
      // Literal-Error). Liste aus YAML 1.1 spec — alle case-insensitive.
      const yamlMagicBool = /^(y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$/;
      // Quote wenn leer oder Sonderzeichen drin — sonst lassen wir
      // den Default-Stil unangetastet (Lesbarkeit).
      if (t === '' || /[:#&*!|>'"%@`,\[\]\{\}]/.test(t) || yamlMagicBool.test(t)) {
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
        // freq_khz_ft4 nur emittieren wenn gesetzt — sonst greift im
        // Backend der FT4_DEFAULT_DIALS-Fallback aus dem Bandplan.
        const ft4 = (b.freq_khz_ft4 != null && b.freq_khz_ft4 !== '')
          ? `, freq_khz_ft4: ${b.freq_khz_ft4}` : '';
        s += `${ind(2)}- { name: "${b.name}", freq_khz: ${b.freq_khz}${ft4} }\n`;
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
    s += `${ind(2)}cq_directed: ${yq(c.operating.cq_directed || '')}\n`;
    s += `${ind(2)}decoder_mode: ${yq(c.operating.decoder_mode || 'standard')}\n`;
    s += `${ind(2)}auto_notch_enabled: ${c.operating.auto_notch_enabled === false ? 'false' : 'true'}\n`;
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
    // v0.10.0 Hunt-Priority-Tiers — als YAML-Liste schreiben
    if (c.operating.hunt_priority && c.operating.hunt_priority.length) {
      s += `${ind(2)}hunt_priority:\n`;
      for (const t of c.operating.hunt_priority) {
        s += `${ind(4)}- ${yq(t)}\n`;
      }
    }
    s += `${ind(2)}tail_end_hunter_enabled: ${c.operating.tail_end_hunter_enabled === true}\n`;
    if (c.operating.dxped_ng3k_push_enabled !== undefined)
      s += `${ind(2)}dxped_ng3k_push_enabled: ${c.operating.dxped_ng3k_push_enabled === true}\n`;
    if (c.operating.dxped_ng3k_push_min_rarity !== undefined)
      s += `${ind(2)}dxped_ng3k_push_min_rarity: ${c.operating.dxped_ng3k_push_min_rarity}\n`;
    s += `${ind(2)}psk_reciprocity_enabled: ${c.operating.psk_reciprocity_enabled === true}\n`;
    s += `${ind(2)}psk_reciprocity_refresh_s: ${c.operating.psk_reciprocity_refresh_s ?? 600}\n`;
    // YAML 1.1: "off"/"on"/"yes"/"no" sind Boolean-Keywords → ohne
    // Quotes wird "off" als False geparst, Pydantic Literal-Check
    // schlaegt fehl. Sebastian-Bug 2026-05-24 nach Mode-Switch im UI.
    s += `${ind(2)}boot_mode: ${yq(c.operating.boot_mode || 'off')}\n`;
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

  // Standard-FT4-Dial-Frequenzen pro Band (Mirror der backend
  // FT4_DEFAULT_DIALS-Tabelle). Wird im Placeholder + als Default
  // beim Hinzufuegen neuer Baender genutzt. Quelle: WSJT-X / IARU.
  const FT4_DEFAULT_DIALS = {
    '160m':   1840, '80m':   3575, '60m':    5357, '40m':    7047,
    '30m':   10140, '20m':  14080, '17m':   18104, '15m':   21140,
    '12m':   24919, '10m':  28180, '6m':    50318, '2m':   144170,
  };
  // FT8-Standard-Dial-Frequenzen analog — fuer addBand-Default.
  const FT8_DEFAULT_DIALS = {
    '160m':   1840, '80m':   3573, '60m':    5357, '40m':    7074,
    '30m':   10136, '20m':  14074, '17m':   18100, '15m':   21074,
    '12m':   24915, '10m':  28074, '6m':    50313, '2m':   144174,
  };

  function addBand() {
    cfg.bands = [...(cfg.bands || []),
                 { name: '20m', freq_khz: 14074, freq_khz_ft4: 14080 }];
  }
  function removeBand(i) { cfg.bands = cfg.bands.filter((_, j) => j !== i); }

  function addAntenna() {
    cfg.antennas = [...(cfg.antennas || []), { name: 'new', bands: ['20m'] }];
  }
  function removeAntenna(i) { cfg.antennas = cfg.antennas.filter((_, j) => j !== i); }

  // v0.10.0 Hunt-Priority-Tiers: Sortier-Logik
  function moveTier(idx, delta) {
    const list = [...(cfg.operating.hunt_priority ?? [])];
    const newIdx = idx + delta;
    if (newIdx < 0 || newIdx >= list.length) return;
    [list[idx], list[newIdx]] = [list[newIdx], list[idx]];
    cfg.operating.hunt_priority = list;
  }

  // Drag&Drop für die Tier-Liste
  let _tierDragFrom = null;
  function onTierDragStart(e, idx) {
    _tierDragFrom = idx;
    e.dataTransfer.effectAllowed = 'move';
  }
  function onTierDrop(e, idx) {
    e.preventDefault();
    if (_tierDragFrom === null || _tierDragFrom === idx) return;
    const list = [...(cfg.operating.hunt_priority ?? [])];
    const [moved] = list.splice(_tierDragFrom, 1);
    list.splice(idx, 0, moved);
    cfg.operating.hunt_priority = list;
    _tierDragFrom = null;
  }
</script>

<div class="wrap">
  <header>
    <h2>Konfiguration</h2>
    <label class="mode-toggle">
      <input type="checkbox" bind:checked={yamlMode}/>
      <span>YAML-Modus</span>
    </label>
  </header>

  <!-- System-Update-Card: lädt asynchron, blockiert nichts, ist auch
       sichtbar wenn die Konfig selbst noch nicht geladen ist oder
       fehlschlägt — Self-Update soll dann gerade nutzbar bleiben. -->
  <SystemUpdateCard />

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
        {#each cfg.bands as b, i}
          {@const ft4Default = FT4_DEFAULT_DIALS[b.name]}
          <!-- FT8-Reihe (immer vorhanden) -->
          <div class="row band-row">
            <input type="text" bind:value={b.name} placeholder="20m"
                   class="band-name"/>
            <span class="mode-badge ft8">FT8</span>
            <input type="number" bind:value={b.freq_khz} placeholder="14074" min="1800"
                   class="band-freq"/>
            <button class="rm" onclick={() => removeBand(i)}
                    title="Band entfernen (beide Modi)">×</button>
          </div>
          <!-- FT4-Reihe (gleicher Band-Index, b.freq_khz_ft4) -->
          {#if b.freq_khz_ft4 != null || ft4Default != null}
            <div class="row band-row band-row-ft4">
              <span class="band-name-mirror">↪ {b.name}</span>
              <span class="mode-badge ft4">FT4</span>
              <input type="number" bind:value={b.freq_khz_ft4}
                     placeholder={ft4Default ?? 'auto'} min="1800"
                     class="band-freq"/>
              <button class="rm-ft4"
                      onclick={() => { b.freq_khz_ft4 = null; cfg.bands = cfg.bands; }}
                      title="FT4 für dieses Band entfernen">×&nbsp;FT4</button>
            </div>
          {:else}
            <div class="row band-row band-row-ft4-add">
              <span class="band-name-mirror">↪ {b.name}</span>
              <button class="add-ft4"
                      onclick={() => { b.freq_khz_ft4 = FT4_DEFAULT_DIALS[b.name] ?? null; cfg.bands = cfg.bands; }}>
                + FT4 für {b.name} aktivieren
              </button>
            </div>
          {/if}
        {/each}
      </section>

      <!-- Antennen (verweisen auf Bänder die sie abdecken) -->
      <section>
        <h3>Antennen <button class="add" onclick={addAntenna}>+ Antenne</button></h3>
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

        <h5 class="subgroup">Slot & Decoder</h5>
        <div class="grid">
          <label class="field"><span>Mode</span>
            <select bind:value={cfg.operating.mode}>
              <option value="FT8">FT8 — 15 s Slots (Standard)</option>
              <option value="FT4">FT4 — 7.5 s Slots (schneller, weniger DX)</option>
            </select>
          </label>
          <label class="field"><span>Decoder-Mode</span>
            <select bind:value={cfg.operating.decoder_mode}>
              <option value="standard">Standard — schnellste (Pi 4)</option>
              <option value="deep">Deep — JTDX-Niveau (1.5-2× CPU)</option>
              <option value="multi">Multi — Pass1+Pass2 (2-2.5× CPU, Pi 5)</option>
              <option value="extreme">Extreme — Subtract+Hint (3-4× CPU, Pi 5)</option>
            </select>
          </label>
          <label class="field toggle-field">
            <span>Auto-Notch <small>(lokale QRM-Linien filtern)</small></span>
            <button type="button" class="toggle"
                    class:on={cfg.operating.auto_notch_enabled}
                    onclick={() => cfg.operating.auto_notch_enabled = !cfg.operating.auto_notch_enabled}
                    aria-pressed={cfg.operating.auto_notch_enabled}>
              <span class="toggle-knob"></span>
            </button>
          </label>
        </div>

        <h5 class="subgroup">CQ-Verhalten</h5>
        <div class="grid">
          <label class="field"><span>Directed CQ <small>(leer = klassisch)</small></span>
            <input type="text" maxlength="4" placeholder="z.B. DX, EU, POTA"
                   bind:value={cfg.operating.cq_directed}
                   oninput={(e) => { e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ''); }}/>
          </label>
          <label class="field"><span>Auto-CQ Intervall <small>(s)</small></span>
            <input type="number" bind:value={cfg.operating.auto_cq_interval_s} min="15" max="300"/>
          </label>
        </div>

        <h5 class="subgroup">Sicherheits-Limits</h5>
        <div class="grid">
          <label class="field"><span>Max PTT <small>(s)</small></span>
            <input type="number" bind:value={cfg.operating.max_ptt_s} min="15" max="60"/>
          </label>
          <label class="field"><span>Max SWR</span>
            <input type="number" step="0.1" bind:value={cfg.operating.swr_max} min="1" max="5"/>
          </label>
        </div>

        <h5 class="subgroup">QSO-Verhalten</h5>
        <div class="grid">
          <label class="field"><span>QSO-Cooldown <small>(min, Station nicht erneut anrufen)</small></span>
            <input type="number" min="0" max="1440"
                   bind:value={cfg.operating.qso_cooldown_min}/>
          </label>
          <label class="field"><span>QSO-Geduld <small>(Slots bei Antwort-Ausbleiben)</small></span>
            <input type="number" min="2" max="20"
                   bind:value={cfg.operating.qso_max_stale_slots}/>
          </label>
        </div>

        <h4>Hunt-Priorität — wer wird zuerst gepickt?</h4>
        <div class="hunt-tier-list">
          {#each (cfg.operating.hunt_priority ?? []) as tier, idx (tier)}
            <div class="hunt-tier-row"
                 draggable="true"
                 ondragstart={(e) => onTierDragStart(e, idx)}
                 ondragover={(e) => e.preventDefault()}
                 ondrop={(e) => onTierDrop(e, idx)}>
              <span class="drag-handle" title="Drag um neu zu sortieren">☰</span>
              <span class="tier-rank">#{idx + 1}</span>
              <span class="tier-icon">{TIER_ICONS[tier] ?? '•'}</span>
              <span class="tier-label">{TIER_LABELS[tier] ?? tier}</span>
              <button type="button" class="tier-btn" title="Hoch"
                      disabled={idx === 0}
                      onclick={() => moveTier(idx, -1)}>▲</button>
              <button type="button" class="tier-btn" title="Runter"
                      disabled={idx === (cfg.operating.hunt_priority?.length ?? 0) - 1}
                      onclick={() => moveTier(idx, +1)}>▼</button>
            </div>
          {/each}
        </div>
        <h5 class="subgroup">🎯 Tail-End-Hunter</h5>
        <div class="grid">
          <label class="field toggle-field">
            <span>Tail-End-Hunter aktiv</span>
            <button type="button" class="toggle"
                    class:on={cfg.operating.tail_end_hunter_enabled}
                    onclick={() => cfg.operating.tail_end_hunter_enabled = !cfg.operating.tail_end_hunter_enabled}
                    aria-pressed={cfg.operating.tail_end_hunter_enabled}>
              <span class="toggle-knob"></span>
            </button>
          </label>
        </div>
        <h5 class="subgroup">📡 DXpedition-Pushes (NG3K-Auto-Watchlist)</h5>
        <div class="grid">
          <label class="field toggle-field">
            <span>Push bei Decode aktiv</span>
            <button type="button" class="toggle"
                    class:on={cfg.operating.dxped_ng3k_push_enabled}
                    onclick={() => cfg.operating.dxped_ng3k_push_enabled = !cfg.operating.dxped_ng3k_push_enabled}
                    aria-pressed={cfg.operating.dxped_ng3k_push_enabled}>
              <span class="toggle-knob"></span>
            </button>
          </label>
          <label class="field">
            <span>Rarity-Schwellwert <small>(0–100, höher = nur rare DX)</small></span>
            <input type="number" min="0" max="100" step="5"
                   bind:value={cfg.operating.dxped_ng3k_push_min_rarity}/>
          </label>
        </div>
        <h5 class="subgroup">PSK-Reciprocity</h5>
        <div class="grid">
          <label class="field toggle-field">
            <span>PSK-Reciprocity aktiv <small>(pskreporter.info)</small></span>
            <button type="button" class="toggle"
                    class:on={cfg.operating.psk_reciprocity_enabled}
                    onclick={() => cfg.operating.psk_reciprocity_enabled = !cfg.operating.psk_reciprocity_enabled}
                    aria-pressed={cfg.operating.psk_reciprocity_enabled}>
              <span class="toggle-knob"></span>
            </button>
          </label>
          <label class="field">
            <span>Refresh-Intervall <small>(s, ≥120 empfohlen)</small></span>
            <input type="number" min="120" max="3600"
                   bind:value={cfg.operating.psk_reciprocity_refresh_s}/>
          </label>
        </div>

        <h4>Auto-ALC (Audio-Gain-Regelung beim TX)</h4>
        <div class="grid grid-bottom">
          <label><span>ALC-Ziel unten <small>(%, Audio rauf wenn drunter)</small></span>
            <input type="number" min="0" max="50"
                   bind:value={cfg.operating.alc_target_low}/>
          </label>
          <label><span>ALC-Ziel oben <small>(%, Audio runter wenn drüber)</small></span>
            <input type="number" min="0" max="80"
                   bind:value={cfg.operating.alc_target_high}/>
          </label>
          <label><span>Start-Audio-Gain <small>(0.0–1.0, Boot-Default)</small></span>
            <input type="number" step="0.05" min="0.1" max="1.0"
                   bind:value={cfg.operating.audio_gain}/>
          </label>
        </div>
      </section>

      <!-- Integrations -->
      <section>
        <h3>Online-Dienste</h3>
        <div class="grid integration-toggles">
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
          {#if cfg.integrations.dx_cluster}
            <label class="check">
              <input type="checkbox" bind:checked={cfg.integrations.dx_cluster.enabled}/>
              <span>DX-Cluster (Telnet)</span>
            </label>
          {/if}
        </div>

        <h4>🌩️ Blitzortung-Warnung</h4>
        <div class="grid two-col">
          <label class="check">
            <input type="checkbox" bind:checked={cfg.integrations.blitzortung.enabled}/>
            <span>Live-Stream + ntfy-Push aktiv</span>
          </label>
          {#if cfg.integrations.blitzortung.enabled}
            <label><span>Alarm-Radius <small>(km um QTH)</small></span>
              <input type="number" min="1" max="500"
                     bind:value={cfg.integrations.blitzortung.alarm_radius_km}/>
            </label>
          {/if}
        </div>

        {#if cfg.integrations.qrz.enabled}
          <h4>QRZ-Credentials</h4>
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
          </div>
          <label class="check">
            <input type="checkbox"
                   bind:checked={cfg.integrations.qrz.logbook_auto_upload}/>
            <span>QSOs automatisch hochladen (auch nach Offline-Phase)</span>
          </label>
        {/if}

        <h4>Push-Notifications (ntfy.sh)</h4>
        <label class="check">
          <input type="checkbox" bind:checked={cfg.integrations.ntfy.enabled}/>
          <span>Push-Notifications aktiv</span>
        </label>
        {#if cfg.integrations.ntfy.enabled}
          <div class="grid">
            <label><span>ntfy.sh Topic</span>
              <input type="text" bind:value={cfg.integrations.ntfy.topic}
                     placeholder="ft8-hochgericht-xyz"/>
            </label>
          </div>
        {/if}
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
  /* Variante fuer Reihen mit ungleich langen Labels — Inputs unten
     bündig statt Top-Aligned. So sitzen die Eingabefelder auf
     gleicher Höhe egal ob das Label 1- oder 2-zeilig umbricht.
     Sebastian-Hinweis 2026-05-27 zu Auto-ALC-Block. */
  .grid-bottom { align-items: end; }
  /* 2-Spalten-Override mit ALIGN-end damit Blitzortung-Toggle + Radius-
     Eingabe nebeneinander auf gleicher Hoehe sitzen. */
  .grid.two-col {
    grid-template-columns: 1fr 1fr;
    align-items: end;
  }
  /* Online-Dienste-Toggles: ueberschreibt minmax 11rem auf 14rem damit
     drei Toggles pro Reihe statt vier eng zusammengequetscht — auf
     Desktop-Width entsteht eine 3x2-Matrix mit Platz zwischen den
     Spalten. Sebastian-Feedback 2026-05-27. */
  .integration-toggles {
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    column-gap: 1rem;
    row-gap: 0.6rem;
  }
  label { display: flex; flex-direction: column; gap: 0.2rem;
          font-size: 0.85rem; color: #cbd5e1; }
  label.check { flex-direction: row; align-items: center; gap: 0.4rem; }
  label span { color: #94a3b8; font-size: 0.75rem; text-transform: uppercase;
               letter-spacing: 0.05em; }
  label span small { text-transform: none; font-size: 0.9em; color: #64748b;
                     font-weight: 400; margin-left: 0.3em; letter-spacing: 0; }
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
  /* Bänder-Tabelle: 2 Rows pro physisches Band (FT8 + FT4). Sebastian
     v0.4.4 — vorher 2 Frequenz-Spalten, jetzt vertikal mit Mode-Badge. */
  .band-row { align-items: center; }
  .band-name { max-width: 5rem; flex: 0 0 5rem; }
  .band-name-mirror {
    max-width: 5rem; flex: 0 0 5rem;
    color: #64748b; font-size: 0.85rem; padding-left: 0.4rem;
    font-family: ui-monospace, monospace;
  }
  .band-freq { flex: 1; }
  .mode-badge {
    flex: 0 0 3.5rem; text-align: center;
    padding: 0.1rem 0.4rem; border-radius: 4px;
    font-size: 0.7rem; font-weight: 700; font-family: ui-monospace, monospace;
    border: 1px solid;
  }
  .mode-badge.ft8 {
    color: var(--accent); border-color: rgba(56,189,248,0.4);
    background: rgba(56,189,248,0.08);
  }
  .mode-badge.ft4 {
    color: #fb923c; border-color: rgba(251,146,60,0.4);
    background: rgba(251,146,60,0.10);
  }
  .band-row-ft4 { opacity: 0.85; margin-bottom: 0.6rem; }
  .band-row-ft4-add { opacity: 0.7; margin-bottom: 0.6rem; }
  .rm-ft4 {
    background: transparent; color: #64748b; border: 1px solid #334155;
    border-radius: 4px; padding: 0.1rem 0.4rem; cursor: pointer; font-size: 0.7rem;
  }
  .rm-ft4:hover { color: var(--danger); border-color: var(--danger); }
  .add-ft4 {
    background: transparent; color: #fb923c;
    border: 1px dashed rgba(251,146,60,0.4); border-radius: 4px;
    padding: 0.1rem 0.6rem; font-size: 0.75rem; cursor: pointer; flex: 1;
  }
  .add-ft4:hover { background: rgba(251,146,60,0.08); }
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

  /* v0.10.1: Sub-Group-Headers im Operating */
  h5.subgroup {
    margin: 1.4rem 0 0.5rem;
    padding-bottom: 0.3rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    color: #94a3b8;
    font-size: 0.85em;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  h5.subgroup:first-of-type { margin-top: 0.5rem; }

  /* v0.10.2: Konsistente Field-Layout — Labels haben FESTE Höhe damit
     Inputs IMMER auf gleicher Baseline sitzen, egal wie lang der
     Label-Text wird. Long labels umbrechen lassen + clamp auf 2 Zeilen.
     Sebastian-Feedback: "Überschriften und Eingabeboxen IMMER auf
     derselben Höhe" — vorheriges flex column gap konnte das nicht
     garantieren weil Spans verschiedene line-counts hatten. */
  .field {
    display: grid;
    grid-template-rows: 2.6em 2.4em;  /* feste Höhen Label + Input */
    gap: 0.3rem;
    align-items: end;
  }
  .field > span {
    font-size: 0.85em;
    color: #cbd5e1;
    font-weight: 500;
    line-height: 1.25;
    align-self: end;
    /* 2-Zeilen-Clamp falls Label sehr lang — bleibt in der 2.6em-Zelle */
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .field > span small {
    font-size: 0.9em; color: #64748b; font-weight: 400; margin-left: 0.3em;
  }
  .field > input[type="text"],
  .field > input[type="number"],
  .field > select {
    height: 2.4em;
    padding: 0 0.7em;
    background: rgba(15, 23, 42, 0.9);  /* dark statt rgba(white) damit
                                            select-Dropdown nicht weiß
                                            rendert (Sebastian-Bug v0.10.1) */
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 6px;
    color: #e2e8f0;
    font-size: 0.95em;
    transition: border-color 120ms;
    /* Native select-styling soweit möglich entfernen — appearance:none
       sorgt dafür dass option-Text auch die dark-Farben erbt */
    -webkit-appearance: none;
    -moz-appearance: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath fill='%2394a3b8' d='M2 4l4 4 4-4z'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 0.7em center;
    background-size: 10px;
    padding-right: 2em;
  }
  .field > input[type="text"],
  .field > input[type="number"] {
    /* Inputs sollen NICHT die select-Chevron-Background haben */
    background-image: none;
    padding-right: 0.7em;
  }
  .field > select option {
    background: #0f172a;
    color: #e2e8f0;
  }
  .field > input:focus,
  .field > select:focus {
    outline: none;
    border-color: var(--accent);
  }
  .field > input::placeholder { color: #475569; font-style: italic; }

  /* v0.10.1+v0.10.2: Toggle-Switch (statt Checkbox) — iOS-style.
     Toggle-Field überschreibt das Field-Grid-Layout: Label LINKS, Toggle RECHTS
     auf gleicher Baseline wie die anderen Inputs (Höhe 5em = 2.6em Label-
     Block + 2.4em Input-Block der Geschwister-Fields). */
  .toggle-field {
    display: flex;
    flex-direction: row;
    align-items: flex-end;
    justify-content: space-between;
    gap: 0.8rem;
    height: 5em;
    padding-bottom: 0.5em;  /* Toggle auf Höhe der Input-Mitte ausrichten */
    grid-template-rows: none;  /* override des .field-Grids */
  }
  .toggle-field > span {
    flex: 1;
    align-self: center;
    -webkit-line-clamp: unset;  /* override des Field-Span-Clamps */
    overflow: visible;
  }
  .toggle {
    position: relative;
    width: 44px;
    height: 24px;
    background: rgba(255, 255, 255, 0.1);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 12px;
    cursor: pointer;
    padding: 0;
    transition: background 180ms, border-color 180ms;
    flex-shrink: 0;
  }
  .toggle.on {
    background: var(--accent);
    border-color: var(--accent);
  }
  .toggle-knob {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 18px;
    height: 18px;
    background: #f1f5f9;
    border-radius: 50%;
    transition: transform 180ms cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
  }
  .toggle.on .toggle-knob { transform: translateX(20px); }
  .toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  /* v0.10.0 Hunt-Priority-Tier-Liste */
  .hunt-tier-list {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    margin-top: 0.5rem;
  }
  .hunt-tier-row {
    display: grid;
    grid-template-columns: 24px 32px 32px 1fr 32px 32px;
    align-items: center;
    gap: 0.4rem;
    padding: 0.4rem 0.5rem;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 6px;
    cursor: grab;
    transition: background 120ms;
  }
  .hunt-tier-row:hover { background: rgba(255, 255, 255, 0.08); }
  .hunt-tier-row:active { cursor: grabbing; }
  .drag-handle {
    font-size: 1.2em;
    color: #64748b;
    user-select: none;
    text-align: center;
  }
  .tier-rank {
    font-weight: 600;
    color: var(--accent);
    text-align: center;
    font-variant-numeric: tabular-nums;
  }
  .tier-icon { font-size: 1.1em; text-align: center; }
  .tier-label { color: #e2e8f0; }
  .tier-btn {
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 0.15);
    color: #94a3b8;
    border-radius: 4px;
    padding: 2px 6px;
    cursor: pointer;
    font-size: 0.9em;
  }
  .tier-btn:hover:not(:disabled) {
    background: rgba(255, 255, 255, 0.08);
    color: #e2e8f0;
  }
  .tier-btn:disabled { opacity: 0.3; cursor: not-allowed; }
</style>
