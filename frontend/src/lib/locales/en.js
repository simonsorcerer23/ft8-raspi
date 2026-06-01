// English UI strings. Counterpart: de.js — EVERY key MUST exist in both.
export const en = {
  // Navigation / tabs
  'nav.funk': '🎙️ Operate',
  'nav.map': '🌍 Map',
  'nav.log': '📒 Log',
  'nav.who': '📡 Receivers',
  'nav.blacklist': '🚫 Blacklist',
  'nav.watchlist': '👀 Watchlist',
  'nav.reputation': '✋ Reputation',
  'nav.dxpedition': '📡 DXpedition',
  'nav.wifi': '📶 WiFi',
  'nav.config': '⚙️ Config',
  'nav.sound_on': 'Browser sound + push notifications ON — click to mute',
  'nav.sound_off': 'Browser sound + push notifications OFF — click to enable',
  'nav.lang_toggle': 'Switch language (German/English)',

  // Setup wizard
  'setup.skip': 'Skip wizard',

  // Footer
  'footer.tagline': 'DK9XR · FT8',

  // StatusBar
  'statusbar.mode_cq': '🛰️ CQ MODE',
  'statusbar.mode_hunt': '🎯 ANSWERING',
  'statusbar.mode_off': '— OFF —',
  'statusbar.state.IDLE': 'READY',
  'statusbar.state.CQ_CALLING': 'calling CQ',
  'statusbar.state.QSO_RESPOND': 'answering',
  'statusbar.state.QSO_REPORT': 'report',
  'statusbar.state.QSO_LOG': 'logging',
  'statusbar.state.TX_LOCKED': 'LOCKED',
  'statusbar.state.UNKNOWN': '…',
  'statusbar.txcall_title': 'Currently transmitted callsign',
  'statusbar.worked': 'WORKED',

  // ControlPanel
  'control.tx_locked': 'TX locked:',
  'control.unknown': 'unknown',
  'control.unlock': 'Release lock',
  'control.cq': 'CQ',
  'control.cq_stop': 'STOP CQ',
  'control.answer': 'Answer',
  'control.answer_stop': 'STOP answering',
  'control.skip_qso': "Abort QSO (don't log)",
  'control.cq_target': 'CQ target',
  'control.cq_target_ph': 'empty = classic (DX, EU, POTA, TEST …)',
  'control.filter_new_only': 'never-worked only',
  'control.filter_dxcc_only': 'new DXCC only (award mode)',
  'control.tx_power': 'TX power',
  'control.cap_title': 'License cap on {band}',
  'control.cap_hint': '{lic} · max {w}W',
  'control.not_saved': '(not saved)',
  'control.antenna': 'Antenna',
  'control.reboot': '🔁 Restart Pi',
  'control.reboot_confirm': 'Really restart the Pi? (~30 s downtime)',
  'control.shutdown': '🌙 Shut down Pi',
  'control.shutdown_confirm': 'Really shut down the Pi?',

  // LoginGate
  'login.title': '🔒 FT8 — Sign in',
  'login.prompt': 'Enter password.',
  'login.placeholder': 'Password',
  'login.submit': 'Sign in',
  'login.rejected': 'Token rejected — please check.',

  // QsoConversation
  'qso.title': 'Live conversation',
  'qso.empty': 'No activity yet — once you start CQ or answering, everything shows up here.',
  'qso.next_action': 'Next action:',

  // OperatingLocationCard
  'oploc.title': 'Operating location',
  'oploc.current': 'Current',
  'oploc.gps': 'GPS',
  'oploc.no_fix': 'no fix',
  'oploc.home_suffix': 'home',
  'oploc.home': 'Germany (home)',
  'oploc.choose_country': 'Choose country manually',
  'oploc.tx_as': 'Transmit as',
  'oploc.tx_home': 'none (home)',

  // DemoModeToggle
  'demo.title': 'Demo mode',
  'demo.on': 'ON — simulator',
  'demo.off': 'OFF — real RX',
  'demo.turn_on': 'Enable demo',
  'demo.turn_off': 'Disable demo',
  'demo.confirm_on': 'Demo mode ON: simulator decodes instead of real RX. Service restarts (~10s). Continue?',
  'demo.confirm_off': 'Demo mode OFF: back to real RX. Service restarts (~10s). Continue?',
  'demo.restarting': 'Service restarting … reload the page in ~10 s.',
  'demo.loading': 'loading …',

  // SolarWidget
  'solar.unavailable': 'Solar data unavailable',
};
