<script>
  // 2-step setup: Operator+Rig, then Antenna+optional QRZ.
  // PSK Reporter is implicit (default on), ntfy.sh isn't in the wizard
  // (configurable later in ConfigPanel for those who want push alerts).
  import { api } from '../lib/api.js';

  let { onDone = () => {} } = $props();

  // Stock max-power per rig. Keep in sync with backend _RIG_TABLE.
  const RIG_DEFAULTS = {
    ic705:  { label: 'Icom IC-705 (QRP portable, 10 W)',  max_power_w: 10  },
    ic7300: { label: 'Icom IC-7300 (Desktop, 100 W)',     max_power_w: 100 },
    ic9700: { label: 'Icom IC-9700 (VHF/UHF, 100 W)',     max_power_w: 100 },
    ic7610: { label: 'Icom IC-7610 (Top-Class, 100 W)',   max_power_w: 100 },
  };

  let step = $state(1);
  let cfg = $state({
    operator: { callsign: '', default_locator: '', default_power_w: 10 },
    rig:      { model: 'ic705', serial_device: '/dev/serial/by-id/usb-Icom_Inc._IC-705-if00',
                cat_baud: 19200, max_power_w: null },
    antennas: [{ name: 'main', bands: ['20m', '40m'] }],
    bands: [
      { name: '20m', freq_khz: 14074, freq_khz_ft4: 14080, antenna: 'main' },
      { name: '40m', freq_khz: 7074,  freq_khz_ft4: 7047,  antenna: 'main' },
    ],
    qrz: { user: '', password: '' },
  });
  let saving = $state(false);
  let error = $state(null);
  let detecting = $state(false);
  let detectMsg = $state(null);

  function next() { step++; }
  function back() { step--; }

  async function detectRig() {
    detecting = true; detectMsg = null;
    try {
      const r = await api.detectRig();
      const cands = r.candidates || [];
      if (cands.length === 0) {
        detectMsg = { kind: 'warn', text: 'Kein bekanntes Rig am USB gefunden. Ist der Stecker drin?' };
      } else {
        const top = cands[0];
        cfg.rig.model = top.model;
        cfg.rig.serial_device = top.serial_device;
        cfg.operator.default_power_w = RIG_DEFAULTS[top.model].max_power_w;
        detectMsg = { kind: 'ok', text: `Erkannt: ${top.description}` };
      }
    } catch (e) {
      detectMsg = { kind: 'warn', text: `Erkennung fehlgeschlagen: ${e.message}` };
    } finally { detecting = false; }
  }

  function onRigChange() {
    // When the rig switches, snap default_power_w to the new rig's stock
    // max so we don't ship a 100W Default into a 10W radio (or vice versa).
    cfg.operator.default_power_w = RIG_DEFAULTS[cfg.rig.model].max_power_w;
    // Adjust the serial-by-id hint to match (operator can edit on page 1).
    if (cfg.rig.model === 'ic705') {
      cfg.rig.serial_device = '/dev/serial/by-id/usb-Icom_Inc._IC-705-if00';
    } else if (cfg.rig.model === 'ic7300') {
      cfg.rig.serial_device = '/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller-if00-port0';
    }
  }

  function configToYaml() {
    const c = cfg;
    const qrzEnabled = !!(c.qrz.user && c.qrz.password);
    return `operator:
  callsign: ${c.operator.callsign.toUpperCase()}
${c.operator.default_locator ? `  default_locator: ${c.operator.default_locator}\n` : ''}  default_power_w: ${c.operator.default_power_w}

rig:
  model: ${c.rig.model}
  serial_device: "${c.rig.serial_device}"
  cat_baud: ${c.rig.cat_baud}

bands:
${c.bands.map(b => {
  const ft4 = b.freq_khz_ft4 ? `, freq_khz_ft4: ${b.freq_khz_ft4}` : '';
  return `  - { name: "${b.name}", freq_khz: ${b.freq_khz}${ft4}, antenna: ${b.antenna} }`;
}).join('\n')}

antennas:
${c.antennas.map(a => `  - { name: ${a.name}, bands: [${a.bands.map(x => `"${x}"`).join(', ')}] }`).join('\n')}

integrations:
  qrz:
    enabled: ${qrzEnabled}
${qrzEnabled ? `    user: ${c.qrz.user}\n    password: ${c.qrz.password}\n` : ''}  psk_reporter:
    enabled: true
    upload_decodes: true
`;
  }

  async function finish() {
    saving = true; error = null;
    try {
      await api.saveConfig(configToYaml());
      localStorage.setItem('ft8_setup_done', '1');
      onDone();
    } catch (e) { error = e.message; }
    finally { saving = false; }
  }
</script>

<div class="wrap">
  <div class="progress">
    <div class:done={step >= 1}>1</div>
    <div class:done={step >= 2}>2</div>
  </div>

  {#if step === 1}
    <h2>1/2 — Operator + Rig</h2>
    <p>Rufzeichen und welches Gerät hängt am Pi?</p>
    <div class="form">
      <label>
        <span>Rufzeichen</span>
        <input type="text" bind:value={cfg.operator.callsign} required
               style="text-transform: uppercase; font-family: ui-monospace, monospace"
               placeholder="DK9XR"/>
      </label>
      <label>
        <span>Locator (leer lassen = GPS-Auto)</span>
        <input type="text" bind:value={cfg.operator.default_locator} maxlength="6"
               style="font-family: ui-monospace, monospace"
               placeholder="JN58td"/>
      </label>
      <label>
        <span>Funkgerät
          <button type="button" class="detect-btn" onclick={detectRig} disabled={detecting}>
            {detecting ? '🔍…' : '🔍 Auto-Detect'}
          </button>
        </span>
        <select bind:value={cfg.rig.model} onchange={onRigChange}>
          {#each Object.entries(RIG_DEFAULTS) as [id, info]}
            <option value={id}>{info.label}</option>
          {/each}
        </select>
        {#if detectMsg}
          <small class={detectMsg.kind === 'ok' ? 'detect-ok' : 'detect-warn'}>
            {detectMsg.text}
          </small>
        {/if}
      </label>
      <label>
        <span>Standard-Sendeleistung (max {RIG_DEFAULTS[cfg.rig.model].max_power_w} W)</span>
        <input type="number" bind:value={cfg.operator.default_power_w}
               min="1" max={RIG_DEFAULTS[cfg.rig.model].max_power_w}/>
      </label>
    </div>
    <div class="nav">
      <button class="primary" onclick={next}
              disabled={!cfg.operator.callsign}>Weiter →</button>
    </div>

  {:else if step === 2}
    <h2>2/2 — Antenne (+ optional QRZ)</h2>
    <p>Welche Antenne, welche Bänder?</p>
    <div class="form">
      <label>
        <span>Antennen-Name</span>
        <input type="text" bind:value={cfg.antennas[0].name} placeholder="endfed_2040"/>
      </label>
      <label>
        <span>Bänder (Komma-separiert)</span>
        <input type="text" value={cfg.antennas[0].bands.join(', ')}
               onchange={(e) => {
                 cfg.antennas[0].bands = e.target.value.split(',').map(s => s.trim()).filter(Boolean);
                 const FT8_DEFAULTS = { '160m': 1840, '80m': 3573, '60m': 5357, '40m': 7074,
                   '30m': 10136, '20m': 14074, '17m': 18100,
                   '15m': 21074, '12m': 24915, '10m': 28074 };
                 const FT4_DEFAULTS = { '160m': 1840, '80m': 3575, '60m': 5357, '40m': 7047,
                   '30m': 10140, '20m': 14080, '17m': 18104,
                   '15m': 21140, '12m': 24919, '10m': 28180 };
                 cfg.bands = cfg.antennas[0].bands.map(name => ({
                   name,
                   freq_khz: FT8_DEFAULTS[name] ?? 14074,
                   freq_khz_ft4: FT4_DEFAULTS[name] ?? null,
                   antenna: cfg.antennas[0].name,
                 }));
               }}
               placeholder="20m, 40m"/>
      </label>
      <small class="hint">
        FT8-Standardfrequenzen werden automatisch zugeordnet (14074 für 20m etc.).
      </small>

      <hr style="border: 0; border-top: 1px solid #334155; margin: 0.6rem 0 0.2rem;"/>
      <p class="qrz-hint">QRZ.com (optional — falls leer wird's nicht aktiviert):</p>
      <label>
        <span>QRZ User</span>
        <input type="text" bind:value={cfg.qrz.user} placeholder="dk9xr"/>
      </label>
      <label>
        <span>QRZ Passwort</span>
        <input type="password" bind:value={cfg.qrz.password}/>
      </label>
      <small class="hint">
        PSK Reporter ist immer aktiv (keine Anmeldung nötig).
      </small>
    </div>
    <div class="nav">
      <button onclick={back}>← Zurück</button>
      <button class="primary" onclick={finish} disabled={saving}>
        {saving ? 'Speichere…' : 'Fertig — Setup abschließen ✓'}
      </button>
    </div>
    {#if error}<div class="err">⚠ {error}</div>{/if}
  {/if}
</div>

<style>
  .wrap {
    max-width: 36rem; margin: 2rem auto; background: var(--panel);
    border-radius: 12px; padding: 1.5rem; border: 1px solid #334155;
  }
  .progress { display: flex; gap: 0.5rem; justify-content: center;
              margin-bottom: 1.5rem; }
  .progress div {
    width: 2rem; height: 2rem; border-radius: 50%;
    background: #1e293b; color: #94a3b8; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    font-family: ui-monospace, monospace;
  }
  .progress div.done { background: var(--accent); color: #0f172a; }
  h2 { margin: 0 0 0.5rem; color: var(--accent); }
  p  { color: #cbd5e1; margin: 0 0 1rem; }
  .qrz-hint { margin: 0.2rem 0 0.4rem; font-size: 0.85rem; color: #94a3b8; }
  .form { display: flex; flex-direction: column; gap: 0.7rem; }
  label { display: flex; flex-direction: column; gap: 0.25rem; }
  label span { color: #94a3b8; font-size: 0.8rem; }
  input[type=text], input[type=number], input[type=password], select {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 6px; padding: 0.5rem 0.7rem; font-size: 1rem;
  }
  .hint { color: #94a3b8; font-size: 0.8rem; }
  .nav { display: flex; gap: 0.7rem; justify-content: space-between;
         margin-top: 1.5rem; }
  .nav button {
    background: transparent; color: var(--fg);
    border: 1px solid #334155; border-radius: 6px;
    padding: 0.6rem 1.2rem; cursor: pointer;
  }
  .primary {
    background: var(--accent) !important; color: #0f172a !important;
    font-weight: 600; border: none !important;
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .err { color: var(--danger); margin-top: 0.7rem; }
  .detect-btn {
    background: transparent; color: var(--accent); border: 1px solid var(--accent);
    border-radius: 4px; padding: 0.1rem 0.5rem; font-size: 0.7rem; cursor: pointer;
    margin-left: 0.5rem;
  }
  .detect-ok { color: var(--ok); font-size: 0.8rem; margin-top: 0.2rem; }
  .detect-warn { color: var(--danger); font-size: 0.8rem; margin-top: 0.2rem; }
</style>
