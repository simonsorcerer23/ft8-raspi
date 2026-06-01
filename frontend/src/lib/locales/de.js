// Deutsche UI-Strings. Pendant: en.js — JEDER Key MUSS in beiden existieren.
export const de = {
  // Navigation / Tabs
  'nav.funk': '🎙️ Funk',
  'nav.map': '🌍 Karte',
  'nav.log': '📒 Log',
  'nav.who': '📡 Empfänger',
  'nav.blacklist': '🚫 Blacklist',
  'nav.watchlist': '👀 Watchlist',
  'nav.reputation': '✋ Reputation',
  'nav.dxpedition': '📡 DXpedition',
  'nav.wifi': '📶 WLAN',
  'nav.config': '⚙️ Konfig',
  'nav.sound_on': 'Browser-Sound + Push-Benachrichtigungen AN — klick zum Stummschalten',
  'nav.sound_off': 'Browser-Sound + Push-Benachrichtigungen AUS — klick zum Aktivieren',
  'nav.lang_toggle': 'Sprache wechseln (Deutsch/English)',

  // Setup-Wizard
  'setup.skip': 'Wizard überspringen',

  // Footer
  'footer.tagline': 'DK9XR · FT8',

  // StatusBar
  'statusbar.mode_cq': '🛰️ CQ-MODUS',
  'statusbar.mode_hunt': '🎯 ANTWORTEN',
  'statusbar.mode_off': '— AUS —',
  'statusbar.state.IDLE': 'BEREIT',
  'statusbar.state.CQ_CALLING': 'sendet CQ',
  'statusbar.state.QSO_RESPOND': 'antwortet',
  'statusbar.state.QSO_REPORT': 'Report',
  'statusbar.state.QSO_LOG': 'loggt',
  'statusbar.state.TX_LOCKED': 'GESPERRT',
  'statusbar.state.UNKNOWN': '…',
  'statusbar.txcall_title': 'Aktuell gesendetes Rufzeichen',
  'statusbar.worked': 'GEARB.',

  // ControlPanel
  'control.tx_locked': 'TX gesperrt:',
  'control.unknown': 'unbekannt',
  'control.unlock': 'Sperre lösen',
  'control.cq': 'CQ',
  'control.cq_stop': 'STOP CQ',
  'control.answer': 'Antworten',
  'control.answer_stop': 'STOP Antworten',
  'control.skip_qso': 'QSO abbrechen (nicht loggen)',
  'control.cq_target': 'CQ-Target',
  'control.cq_target_ph': 'leer = klassisch (DX, EU, POTA, TEST …)',
  'control.filter_new_only': 'nur noch nie gearbeitete',
  'control.filter_dxcc_only': 'nur neue DXCC (Award-Modus)',
  'control.tx_power': 'TX-Leistung',
  'control.cap_title': 'Lizenzbedingtes Cap auf {band}',
  'control.cap_hint': '{lic} · max {w}W',
  'control.not_saved': '(nicht gespeichert)',
  'control.antenna': 'Antenne',
  'control.reboot': '🔁 Pi neu starten',
  'control.reboot_confirm': 'Pi wirklich neu starten? (~30 s Downtime)',
  'control.shutdown': '🌙 Pi herunterfahren',
  'control.shutdown_confirm': 'Pi wirklich herunterfahren?',

  // LoginGate
  'login.title': '🔒 FT8 — Anmeldung',
  'login.prompt': 'Passwort eingeben.',
  'login.placeholder': 'Passwort',
  'login.submit': 'Anmelden',
  'login.rejected': 'Token abgelehnt — bitte prüfen.',

  // QsoConversation
  'qso.title': 'Live-Konversation',
  'qso.empty': 'Noch keine Aktivität — wenn du CQ oder Antworten startest, taucht hier alles auf.',
  'qso.next_action': 'Nächste Aktion:',

  // OperatingLocationCard
  'oploc.title': 'Operating-Location',
  'oploc.current': 'Aktuell',
  'oploc.gps': 'GPS',
  'oploc.no_fix': 'kein Fix',
  'oploc.home_suffix': 'Heimat',
  'oploc.home': 'Deutschland (Heimat)',
  'oploc.choose_country': 'Land manuell wählen',
  'oploc.tx_as': 'Funke als',
  'oploc.tx_home': 'keiner (Heimat)',

  // DemoModeToggle
  'demo.title': 'Demo-Modus',
  'demo.on': 'AN — Simulator',
  'demo.off': 'AUS — echter RX',
  'demo.turn_on': 'Demo einschalten',
  'demo.turn_off': 'Demo ausschalten',
  'demo.confirm_on': 'Demo-Modus AN: Simulator-Decodes statt echtem RX. Dienst startet neu (~10s). Fortfahren?',
  'demo.confirm_off': 'Demo-Modus AUS: zurück auf echten RX. Dienst startet neu (~10s). Fortfahren?',
  'demo.restarting': 'Dienst startet neu … Seite in ~10 s neu laden.',
  'demo.loading': 'lädt …',

  // SolarWidget
  'solar.unavailable': 'Solar-Daten nicht verfügbar',

  // RigPanel
  'rig.rx_level': 'RX-PEGEL',

  // Gemeinsame Tabellen-/Formular-Begriffe
  'common.call': 'Call',
  'common.add': 'Hinzufügen',
  'common.remove': 'Entfernen',
  'common.added': 'Hinzugefügt',
  'common.note': 'Notiz',
  'common.reason': 'Grund',
  'common.error': 'Fehler',

  // StatsDashboard
  'stats.today': 'Heute',
  'stats.qsos_today': 'QSOs heute',
  'stats.dxcc_today': 'DXCC heute',
  'stats.qsos_7d': 'QSOs 7 Tage',
  'stats.decodes_h': 'Decodes / h',
  'stats.band_suggest': 'Band-Vorschlag',
  'stats.best_dx_today': 'Beste DX heute',

  // SystemPanel
  'system.title': 'Pi-Status',
  'system.disk': 'Disk',
  'system.uptime': 'Uptime',
  'system.throttle': 'Throttle',

  // WhoHeardMe
  'who.title': '📡 Wer hat mich gehört? (PSK Reporter)',
  'who.reporter': 'Reporter',
  'who.grid': 'Grid',
  'who.best_snr': 'Best SNR',
  'who.reports': 'Reports',
  'who.bands': 'Bänder',
  'who.last': 'Letzter',

  // BlacklistPanel
  'bl.title': 'Blacklist',
  'bl.call_ph': 'Call (z.B. W1ABC)',
  'bl.reason_ph': 'Grund (optional)',

  // WatchlistPanel
  'wl.title': '👀 Watchlist',
  'wl.call_ph': 'Call (z.B. ZL9HR)',
  'wl.note_ph': 'Notiz (optional)',
  'wl.watch': 'Beobachten',
  'wl.last_alert': 'Letzter Alarm',

  // ReputationPanel
  'rep.title': '✋ Soft-Blacklist (Call-Reputation)',
  'rep.score': 'Score',
  'rep.attempts': 'Versuche',
  'rep.successes': 'Erfolge',
  'rep.last_reason': 'Letzter Grund',
  'rep.last_attempt': 'Letzter Versuch',

  // DxpeditionPanel
  'dxp.title': '📡 DXpedition-Schedule',
  'dxp.call_ph': 'Call (z.B. ZL9HR)',
  'dxp.start_ph': 'Start',
  'dxp.end_ph': 'Ende',
  'dxp.note_ph': 'Notiz (z.B. Bouvet)',
  'dxp.source': 'Quelle',
  'dxp.start': 'Start',
  'dxp.end': 'Ende',
  'dxp.status': 'Status',
  'dxp.watchlist': 'Watchlist',

  // Charts
  'chart.active_hours': 'Aktive Stunden pro Kontinent',

  // ADIFTable / Log-Filter
  'log.f_call_ph': 'Call (Substring)',
  'log.f_prefix_ph': 'Präfix (z.B. "9A")',
  'log.all_bands': 'Alle Bänder',
  'log.all_modes': 'Alle Modi',
  'log.any_period': 'Beliebiger Zeitraum',
  'log.last_24h': 'letzte 24 h',
  'log.last_7d': 'letzte 7 Tage',
  'log.last_30d': 'letzte 30 Tage',
  'log.last_year': 'letztes Jahr',
  'log.all_continents': 'Alle Kontinente',
  'log.dxcc_ph': 'DXCC-Land (z.B. Spain)',
  'log.marine': 'Marine',
  'log.hits': 'Treffer',
  'log.loading': 'Lade…',
  'log.empty': 'Keine QSOs mit diesen Filtern.',
  'log.prefix': 'Präfix',
  'cont.EU': 'Europa',
  'cont.AF': 'Afrika',
  'cont.AS': 'Asien',
  'cont.NA': 'Nordamerika',
  'cont.SA': 'Südamerika',
  'cont.OC': 'Ozeanien',
  'cont.AN': 'Antarktis',
  'dl.title': 'Decodes',
  'dl.only_me': 'nur an mich',
  'dl.empty': 'Noch keine Decodes. Warte auf nächsten Slot…',
  'dl.tip_newgrid': 'Neuer Grid',
  'dl.tip_newgrid_band': 'Neuer Grid auf diesem Band',
  'dl.tip_pileup': 'Pile-Up: viele andere Stationen rufen → Picker laesst aus',
  'dl.tip_tailend': 'Tail-End: nach diesem RR73 anrufen wie nach CQ',
  'dl.tip_blacklist': 'Blacklisten',
  'chart.swr_trend': 'SWR-Trend',
  'chart.best_time': 'Beste Zeit für {band}',
  'access.login_pw': 'Login-Passwort',
  'access.login_pw_ph': 'z.B. hochgericht-73',
  'access.set': 'setzen',
  'access.min8': 'Mindestens 8 Zeichen.',
  'access.pw_set': 'Passwort gesetzt ✓',
  'access.failed': 'Fehlgeschlagen: ',
  'wiz.step1': '1/2 — Operator + Rig',
  'wiz.callsign': 'Rufzeichen',
  'wiz.locator': 'Locator (leer lassen = GPS-Auto)',
  'wiz.next': 'Weiter →',
  'wiz.antenna_name': 'Antennen-Name',
  'wiz.bands': 'Bänder (Komma-separiert)',
  'wiz.qrz_optional': 'QRZ.com (optional):',
  'wiz.qrz_user': 'QRZ User',
  'wiz.qrz_pw': 'QRZ Passwort',
  'wiz.back': '← Zurück',
  'wiz.saving': 'Speichere…',
  'wiz.finish': 'Fertig — Setup abschließen ✓',
};
