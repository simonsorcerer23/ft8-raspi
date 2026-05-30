<script>
  import { onMount } from 'svelte';
  import StateIndicator from './components/StateIndicator.svelte';
  import StatusBadges   from './components/StatusBadges.svelte';
  import ControlPanel   from './components/ControlPanel.svelte';
  import DecodeList     from './components/DecodeList.svelte';
  import Map            from './components/Map.svelte';
  import ADIFTable      from './components/ADIFTable.svelte';
  import ConfigPanel    from './components/ConfigPanel.svelte';
  import WifiManager    from './components/WifiManager.svelte';
  import BlacklistPanel from './components/BlacklistPanel.svelte';
  import WatchlistPanel from './components/WatchlistPanel.svelte';
  import ReputationPanel from './components/ReputationPanel.svelte';
  import DxpeditionPanel from './components/DxpeditionPanel.svelte';
  import SolarWidget    from './components/SolarWidget.svelte';
  import StatusBar      from './components/StatusBar.svelte';
  import StatsDashboard from './components/StatsDashboard.svelte';
  import SystemPanel from './components/SystemPanel.svelte';
  import BestTimeChart  from './components/BestTimeChart.svelte';
  import ActiveHoursChart from './components/ActiveHoursChart.svelte';
  import OperatingLocationCard from './components/OperatingLocationCard.svelte';
  import OperatorAdminPanel from './components/OperatorAdminPanel.svelte';
  import FirstBootWizard from './components/FirstBootWizard.svelte';
  import QsoConversation from './components/QsoConversation.svelte';
  import RigPanel       from './components/RigPanel.svelte';
  import SwrTrendChart  from './components/SwrTrendChart.svelte';
  import WhoHeardMe     from './components/WhoHeardMe.svelte';
  import LoginGate      from './components/LoginGate.svelte';
  import { api, getToken } from './lib/api.js';
  import { statusStore, healthStore, decodeStore } from './lib/stores.svelte.js';
  import { attachStatusStream, attachDecodeStream,
           requestNotificationPermission,
           setSoundEnabled, isSoundEnabled } from './lib/sound.svelte.js';

  let soundOn = $state(true);
  let needsSetup = $state(false);
  // v0.37.0 — Token-Auth-Gate. Optimistisch authed wenn ein Token da ist;
  // ein 401 (via 'ft8-auth-required'-Event aus api.js) klappt das Gate auf.
  let authed = $state(!!getToken());
  let _detachers = [];
  function startBoot() {
    if (_detachers.length) return;
    statusStore.refresh();
    healthStore.refresh();
    const t1 = setInterval(() => statusStore.refresh(), 5_000);
    const t2 = setInterval(() => healthStore.refresh(), 15_000);
    const ds = attachStatusStream();
    const dd = attachDecodeStream(decodeStore);
    _detachers = [() => clearInterval(t1), () => clearInterval(t2), ds, dd];
  }
  function stopBoot() { _detachers.forEach((f) => f()); _detachers = []; }
  function handleAuthed() { authed = true; startBoot(); }

  // Pi-Identität in den Titel: ft8 vs ft8-2 unterscheidbar machen.
  // Quelle ist die URL der User benutzt (Tailscale-DNS, .local, oder IP).
  // First-segment vor "." extrahieren — bei Tailscale "ft8-2.tail9dd...ts.net"
  // ergibt das "ft8-2", bei "192.168.x.y" einfach die IP.
  const piLabel = (() => {
    const h = (typeof window !== 'undefined' ? window.location.hostname : '') || '';
    const first = h.split('.')[0];
    return first || 'Raspi';
  })();
  const pageTitle = `FT8 ${piLabel}`;
  if (typeof document !== 'undefined') document.title = pageTitle;

  async function checkSetup() {
    // localStorage so the dismiss persists across browser sessions —
    // sessionStorage would re-trigger the wizard on every new tab, which
    // is annoying once the operator is past the first-boot dance.
    if (localStorage.getItem('ft8_setup_done')) return false;
    if (localStorage.getItem('ft8_setup_skipped')) return false;
    try {
      const c = await api.config();
      // Trigger heuristic: no callsign yet, OR placeholder + bands look like
      // the unconfigured stub (no antennas at all).
      const needsCall = !c.operator.callsign;
      const noAntennas = !c.antennas || c.antennas.length === 0;
      return needsCall || noAntennas;
    } catch { return false; }
  }

  onMount(() => {
    const onAuthRequired = () => { authed = false; stopBoot(); };
    window.addEventListener('ft8-auth-required', onAuthRequired);
    (async () => {
      needsSetup = await checkSetup();
      if (authed) startBoot();
    })();
    return () => {
      window.removeEventListener('ft8-auth-required', onAuthRequired);
      stopBoot();
    };
  });

  function skipSetup() {
    localStorage.setItem('ft8_setup_skipped', '1');
    needsSetup = false;
  }

  async function toggleSound() {
    soundOn = !soundOn;
    setSoundEnabled(soundOn);
    if (soundOn) await requestNotificationPermission();
  }

  let tab = $state('main');

  // Map current rig dial freq to a band name for the BestTimeChart
  function _bandFromHz(hz) {
    if (!hz) return '20m';
    const M = hz / 1_000_000;
    if (M < 2)   return '160m';
    if (M < 4.5) return '80m';
    if (M < 8)   return '40m';
    if (M < 11)  return '30m';
    if (M < 15)  return '20m';
    if (M < 19)  return '17m';
    if (M < 22)  return '15m';
    if (M < 25)  return '12m';
    return '10m';
  }
  const currentBandName = $derived(_bandFromHz(statusStore.value.rig?.freq_hz));

  async function handleReply(decode) {
    try {
      await api.reply(decode);
    } catch (e) {
      console.error('reply failed', e);
      alert(`Reply fehlgeschlagen: ${e.message}`);
    }
  }

  async function handleTailEnd(decode) {
    try {
      await api.tailEnd(decode);
    } catch (e) {
      console.error('tail-end failed', e);
      alert(`Tail-End fehlgeschlagen: ${e.message}`);
    }
  }

  function handleBadgeDetail(key, section) {
    console.log('badge detail', key, section);
  }
</script>

{#if !authed}
  <LoginGate onAuthed={handleAuthed} />
{:else}

<header>
  <h1>{pageTitle}</h1>
  <SolarWidget />
  <nav>
    <button class:active={tab === 'main'}  onclick={() => tab = 'main'}>🎙️ Funk</button>
    <button class:active={tab === 'map'}   onclick={() => tab = 'map'}>🌍 Map</button>
    <button class:active={tab === 'log'}   onclick={() => tab = 'log'}>📒 Log</button>
    <button class:active={tab === 'who'}   onclick={() => tab = 'who'}>📡 Empfänger</button>
    <button class:active={tab === 'bl'}    onclick={() => tab = 'bl'}>🚫 Blacklist</button>
    <button class:active={tab === 'watch'} onclick={() => tab = 'watch'}>👀 Watchlist</button>
    <button class:active={tab === 'rep'}   onclick={() => tab = 'rep'}>✋ Reputation</button>
    <button class:active={tab === 'dxped'} onclick={() => tab = 'dxped'}>📡 DXpedition</button>
    <button class:active={tab === 'wifi'}  onclick={() => tab = 'wifi'}>📶 WLAN</button>
    <button class:active={tab === 'cfg'}   onclick={() => tab = 'cfg'}>⚙️ Konfig</button>
    <button class="sound" onclick={toggleSound}
            title={soundOn ? 'Browser-Sound + Push-Benachrichtigungen AN — klick zum Stummschalten'
                           : 'Browser-Sound + Push-Benachrichtigungen AUS — klick zum Aktivieren'}>
      {soundOn ? '🔊' : '🔇'}
    </button>
  </nav>
</header>

{#if needsSetup}
  <FirstBootWizard onDone={() => needsSetup = false} />
  <div style="text-align: center; margin-top: 1rem;">
    <button onclick={skipSetup} style="background: transparent; color: #94a3b8;
            border: none; cursor: pointer; font-size: 0.85rem;">
      Wizard überspringen
    </button>
  </div>
{:else}

<main>
  <StatusBar />
  <StatusBadges onDetail={handleBadgeDetail} />

  {#if tab === 'main'}
    <RigPanel />
    <OperatingLocationCard />
    <StatsDashboard />
    <QsoConversation />
    <DecodeList onReply={handleReply} onTailEnd={handleTailEnd} />
    <SwrTrendChart hours={24} />
    <BestTimeChart band={currentBandName} />
    <ActiveHoursChart />
    <SystemPanel />
    <!-- ControlPanel ganz ans Ende: TX-Leistung-Slider, Antenne-Dropdown,
         PANIC und Shutdown sind Tap-empfindlich. Sebastian 2026-05-24:
         beim Scrollen aufm Handy mit "Wurstfingern" zu leicht verstellt.
         CQ/Antworten-Buttons sind auch hier drin — fuer den Notfall-Switch
         scrollt man bis zum Ende, das ist akzeptabel weil der Notfall-
         State im StatusBar oben sichtbar bleibt. -->
    <ControlPanel />
  {:else if tab === 'map'}
    <Map />
  {:else if tab === 'log'}
    <ADIFTable />
  {:else if tab === 'who'}
    <WhoHeardMe />
  {:else if tab === 'bl'}
    <BlacklistPanel />
  {:else if tab === 'watch'}
    <WatchlistPanel />
  {:else if tab === 'rep'}
    <ReputationPanel />
  {:else if tab === 'dxped'}
    <DxpeditionPanel />
  {:else if tab === 'wifi'}
    <WifiManager />
  {:else if tab === 'cfg'}
    <OperatorAdminPanel />
    <ConfigPanel />
  {/if}
</main>

<footer>
  <small>DK9XR · FT8 · {new Date().toISOString().slice(0,10)}</small>
</footer>
{/if}

{/if}

<style>
  :global(body) { margin: 0; }
  header {
    position: sticky;
    top: 0;
    z-index: 10;
    background: #0b1220;
    border-bottom: 1px solid #1e293b;
    padding: 0.6rem 1rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  header h1 { margin: 0; font-size: 1.1rem; color: var(--accent); letter-spacing: 0.05em; }
  nav { display: flex; gap: 0.3rem; }
  nav button {
    background: transparent;
    color: var(--fg);
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 0.3rem 0.7rem;
    cursor: pointer;
    font-size: 0.9rem;
  }
  nav button.active { background: var(--accent); color: #0f172a; font-weight: 600; }
  main {
    display: flex;
    flex-direction: column;
    gap: 0.8rem;
    padding: 0.8rem;
    max-width: 720px;
    margin: 0 auto;
  }
  footer {
    text-align: center;
    color: #64748b;
    padding: 1rem;
    font-family: ui-monospace, monospace;
  }
  .placeholder {
    background: var(--panel);
    padding: 1rem;
    border-radius: 8px;
    color: #94a3b8;
  }
  .placeholder h2 { margin: 0 0 0.5rem; color: var(--accent); }
</style>
