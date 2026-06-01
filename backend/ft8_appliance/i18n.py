"""Backend-side i18n for operator-facing strings the backend *generates*.

Scope: messages produced by the backend itself — TX-lock reasons (guards),
conversation/state hints, ntfy push bodies. These are deliberately separate
from the ~450 frontend UI keys (those live in
``frontend/src/lib/locales/*.js`` and cover frontend-rendered text). There is
no overlap: backend keys = backend-generated text, frontend keys =
frontend-rendered text.

Two language sources, matching where the string is produced:

* **Browser-facing** strings (status/SSE/control responses): the lock reason
  is generated in the slot loop (no request context), so we store a stable
  *code* + *params* on the state machine and translate at serialize time
  using the language the browser passes via ``?lang=`` (see :func:`ui_lang`).
* **ntfy pushes** (no browser request — they go to the phone): translated at
  generation time using the configured default language (:func:`default_lang`,
  fed from ``config.ui.language`` at startup).

The catalog is a flat ``key -> str`` dict per language; ``{name}`` style
placeholders are filled via :func:`translate`.
"""

from __future__ import annotations

_DE: dict[str, str] = {
    # --- TX-lock reasons (from statemachine/guards.py) ---
    "guard.time_no_sync": "Kein GPS-Fix und Chrony nicht synchron",
    "guard.time_offset": "Zeit-Offset {offset} s > {max} s erlaubt",
    "guard.swr": "SWR {swr} ueber Limit {max} — Antenne pruefen",
    "guard.alc": "ALC {alc} % > {max} % — Audio-Pegel zu hoch",
    "guard.battery": "Akku {volts} V < {min} V — Netzteil anschliessen",
    "guard.temp": "CPU-Temperatur {temp} °C > {max} °C — Pi kuehlen",
    "guard.audio_drift": "Audio-Drift {drift} Samples — Kalibrierung kaputt",
    "guard.antenna": (
        "Aktive Antenne deckt das aktuelle Band nicht ab — "
        "Antenne wechseln oder Band aendern"
    ),
    # --- other lock reasons (orchestrator) ---
    "lock.ptt_stuck": "PTT-stuck recovery",
    "lock.tx_locked_prefix": "TX gesperrt: {reason}",
    # --- conversation / state hints (web/routes/status.py) ---
    "hint.cq_calling": "sendet weiter CQ {call} bis jemand antwortet",
    "hint.qso_respond": "erwartet Signal-Report von {call}",
    "hint.qso_report": "erwartet RR73 von {call}",
    "hint.qso_grace": (
        "QSO mit {call} abgeschlossen — lauscht noch einen Slot "
        "ob er Wiederholung schickt"
    ),
    "hint.idle_hunt_cq": "Hunt + CQ aktiv: hört auf CQs und ruft selber wenn nichts kommt",
    "hint.idle_hunt": "hört auf hörbare CQs zum Beantworten",
    "hint.idle_cq": "wartet auf naechsten Slot um CQ zu rufen",
    "hint.idle_wait": "wartet — drücke CQ oder Antworten",
    # --- ntfy push bodies / titles / action labels (orchestrator) ---
    "push.preflight_title": "⚠ {call}: Upload-Setup unvollstaendig",
    "push.shutdown_msg": "Pi wird heruntergefahren — sicher zum Stecker-Ziehen in ~30 s",
    "push.shutdown_title": "🌙 FT8 Pi: shutdown",
    "push.reboot_msg": "Pi wird neu gestartet — kommt in ca. 30 s zurück",
    "push.reboot_title": "🔁 FT8 Pi: reboot",
    "push.badge_dxpedition": "🎯 DXpedition",
    "push.badge_watchlist": "👀 Watchlist",
    "push.watchlist_msg": "{kind} auf {band} ({snr})",
    "push.act_back_to_power": "⏮ Auf {watts}W zurück",
    "push.power_tamper_msg": (
        "TX-Leistung am Rig auf {rig}W verstellt (App-Stand war {expected}W). "
        "Jemand pfuscht am Rig."
    ),
    "push.tamper_settings_title": "🛠 Rig-Settings extern geaendert",
    "push.act_back_to_mode": "⏮ Auf {mode} zurück",
    "push.mode_tamper_msg": (
        "Rig-Modus ist {rig} statt {expected}. Damit funkt FT8 nicht richtig "
        "(USB-MOD-Audio wird nicht zum Modulator geroutet)."
    ),
    "push.tamper_mode_title": "🛠 Rig-Modus extern geaendert",
    "push.act_stop_cq": "⏹ STOP CQ",
    "push.act_to_hunting": "🎯 Auf Hunting",
    "push.cq_idle_msg": (
        "CQ ruft seit {min} min ohne Antwort ({count} CQs gesendet). "
        "Band evtl. tot oder QRG belegt — STOP oder auf Hunting wechseln? "
        "Pi laeuft weiter bis du was machst."
    ),
    "push.cq_idle_title": "📡 CQ-Idle ohne Antwort",
    "push.bw_tamper_msg": "Filterbreite am Rig auf {rig} Hz verstellt (Soll {expected} Hz).",
    "push.tamper_filter_title": "🛠 Rig-Filter extern geaendert",
    "push.silence_msg": (
        "Keine Decodes seit {stale} min UND kein RX-Audio seit {audio} min — "
        "Antenne / Audio-Kabel / Rig prüfen!"
    ),
    "push.silence_title": "📡 FT8 Pi: Funkstille (Audio tot)",
    "push.decoder_late_msg": (
        "Decoder-Late: {count} Slots >80% Laufzeit (max {dur}s). "
        "CPU vermutlich am Limit — Deep-Mode aus, andere Loads reduzieren?"
    ),
    "push.decoder_late_title": "🐢 FT8 Pi: Decoder-Last hoch",
    "push.act_back_to_band": "🔄 Auf {band} zurück",
    "push.freq_tamper_msg": (
        "Rig ist auf {mhz} MHz ({delta} Hz von {band}/{khz} kHz). Wer hat gedreht?"
    ),
    "push.freq_tamper_title": "📻 Frequenz wurde verstellt",
    "push.act_start_hunting": "Hunting starten",
    "push.act_start_cq": "CQ starten",
    "push.mode_alert_title": "⚠️ FT8 {host}: Auto-Modus inaktiv",
    "push.mode_idle_bootmode": "Pi steht still — boot_mode={bm} aber kein Modus aktiv",
    "push.mode_idle_long": "Pi ist seit {min} min ohne Auto-Modus. Hunting starten?",
    "push.daily_title": "🌅 FT8 Pi: 24h-Übersicht",
    "push.daily_qsos": "📡 QSOs letzte 24h: {n} ({uniq} unique Calls)",
    "push.daily_dxccs": "🌍 DXCCs gesamt: {n}",
    "push.daily_bestdx": "⭐ Best DX: {call} ({grid})",
    "push.daily_pending": "⏳ QRZ-Pending: {n} (warten auf Connectivity)",
    "push.daily_all_uploaded": "✅ Alle QSOs bei QRZ",
    "push.storm_closer": "Gewitter rueckt naeher — {km} km Entfernung",
    "push.storm_msg": "Gewitter in {km} km Entfernung (Radius {radius} km)",
    "push.storm_title": "Gewitterwarnung",
    "push.country_dx_title": "📍 DX-Operating?",
    "push.country_dx_msg": (
        "GPS sagt du bist seit 30+ min in {name} ({code}). "
        "Auf {code}/{call} umstellen?"
    ),
    "push.country_home_title": "🏠 Wieder daheim?",
    "push.country_home_msg": (
        "GPS sagt du bist zuhause aber sendest noch als {current}/{call}. "
        "Auf Heimat-Call zurueck?"
    ),
    "push.country_mismatch_title": "⚠️ Operating-Country Mismatch",
    "push.country_mismatch_msg": (
        "Du sendest {current}/{call} ({curname}) aber GPS sagt du bist in "
        "{detname} ({detected})."
    ),
    "push.act_unlock": "🔓 Sperre lösen",
    "push.swr_runaway_msg": (
        "SWR auf {swr} gestiegen (Limit {hard}) — TX wurde sofort abgebrochen. "
        "Antenne pruefen!"
    ),
    "push.swr_runaway_title": "🚨 SWR-Notabschaltung",
    "push.swr_warn_msg": (
        "SWR steigt auf {swr} (Warn-Schwelle {warn}, Lock bei {hard}). "
        "Antenne checken — Stehwelle könnte sich verschlechtert haben. "
        "Bei Erreichen von {hard} sperrt der Pi TX automatisch."
    ),
    "push.swr_warn_title": "⚠ SWR-Vorwarnung",
    "push.alc_warn_msg": (
        "ALC bei {alc}% (Warn-Schwelle {warn}%). Audio-Pegel zu hoch — der "
        "Closed-Loop sollte das selbst runter trimmen. Falls's nicht zurückgeht, "
        "manuell im Konfig audio_gain reduzieren oder TX-Leistung am Rig anpassen."
    ),
    "push.alc_warn_title": "⚠ ALC-Vorwarnung",
    "push.clipping_msg": (
        "RX-Pegel bei {dbfs} dBFS seit {secs}s — nahe am Clipping. Im IC-7300 "
        "MENU → SET → Connectors → AF/SQL Control → USB AF Output Level "
        "reduzieren (Default ~13, probier 8-10)."
    ),
    "push.clipping_title": "🔊 FT8 {host} — RX-Pegel zu hoch",
    "push.new_dxcc_prefix": "🆕 New DXCC! ",
    "push.qso_complete_prefix": "📡 QSO complete: ",
    "push.mf_suffix": " ⚓ Marinefunker MF #{nr}",
    "push.act_stop": "⏹ Stoppen",
    "push.act_hunting": "🎯 Hunting",
    "push.act_cq": "📢 CQ",
    "push.upload_giveup_msg": (
        "{service}-Upload fuer QSO {call} nach {attempts} Versuchen aufgegeben. "
        "QSO bleibt lokal im Log + ADIF — bei Bedarf manuell hochladen."
    ),
    "push.upload_giveup_title": "⚠️ Upload aufgegeben",
    "push.spill_msg": (
        "QSO {call} konnte nicht in die DB geschrieben werden — auf Spill-Datei "
        "gesichert, wird automatisch nachgetragen. Bitte Speicherplatz/DB pruefen."
    ),
    "push.spill_title": "⚠️ QSO-Log-Fehler",
    "push.dxped_reminder_title": "📡 DXpedition morgen QRV: {call}",
    "push.act_release_lock": "Sperre lösen",
    "push.tx_lock_title": "⚠️ FT8 {host} — TX-Lock",
}

_EN: dict[str, str] = {
    "guard.time_no_sync": "No GPS fix and chrony not synced",
    "guard.time_offset": "Time offset {offset} s > {max} s allowed",
    "guard.swr": "SWR {swr} above limit {max} — check antenna",
    "guard.alc": "ALC {alc} % > {max} % — audio level too high",
    "guard.battery": "Battery {volts} V < {min} V — connect power supply",
    "guard.temp": "CPU temperature {temp} °C > {max} °C — cool the Pi",
    "guard.audio_drift": "Audio drift {drift} samples — calibration broken",
    "guard.antenna": (
        "Active antenna doesn't cover the current band — "
        "switch antenna or change band"
    ),
    "lock.ptt_stuck": "PTT-stuck recovery",
    "lock.tx_locked_prefix": "TX locked: {reason}",
    "hint.cq_calling": "keeps calling CQ {call} until someone answers",
    "hint.qso_respond": "awaiting signal report from {call}",
    "hint.qso_report": "awaiting RR73 from {call}",
    "hint.qso_grace": (
        "QSO with {call} complete — listening one more slot "
        "in case of a repeat"
    ),
    "hint.idle_hunt_cq": "Hunt + CQ active: listens for CQs and calls itself if nothing comes",
    "hint.idle_hunt": "listens for audible CQs to answer",
    "hint.idle_cq": "waiting for the next slot to call CQ",
    "hint.idle_wait": "waiting — press CQ or Answer",
    # --- ntfy push bodies / titles / action labels (orchestrator) ---
    "push.preflight_title": "⚠ {call}: upload setup incomplete",
    "push.shutdown_msg": "Pi is shutting down — safe to unplug in ~30 s",
    "push.shutdown_title": "🌙 FT8 Pi: shutdown",
    "push.reboot_msg": "Pi is rebooting — back in ~30 s",
    "push.reboot_title": "🔁 FT8 Pi: reboot",
    "push.badge_dxpedition": "🎯 DXpedition",
    "push.badge_watchlist": "👀 Watchlist",
    "push.watchlist_msg": "{kind} on {band} ({snr})",
    "push.act_back_to_power": "⏮ Back to {watts}W",
    "push.power_tamper_msg": (
        "TX power changed at the rig to {rig}W (app had {expected}W). "
        "Someone is fiddling with the rig."
    ),
    "push.tamper_settings_title": "🛠 Rig settings changed externally",
    "push.act_back_to_mode": "⏮ Back to {mode}",
    "push.mode_tamper_msg": (
        "Rig mode is {rig} instead of {expected}. FT8 won't work like this "
        "(USB-MOD audio isn't routed to the modulator)."
    ),
    "push.tamper_mode_title": "🛠 Rig mode changed externally",
    "push.act_stop_cq": "⏹ STOP CQ",
    "push.act_to_hunting": "🎯 To hunting",
    "push.cq_idle_msg": (
        "CQ has been calling for {min} min with no answer ({count} CQs sent). "
        "Band may be dead or the QRG busy — STOP or switch to hunting? "
        "The Pi keeps running until you act."
    ),
    "push.cq_idle_title": "📡 CQ idle, no answer",
    "push.bw_tamper_msg": "Filter width changed at the rig to {rig} Hz (should be {expected} Hz).",
    "push.tamper_filter_title": "🛠 Rig filter changed externally",
    "push.silence_msg": (
        "No decodes for {stale} min AND no RX audio for {audio} min — "
        "check antenna / audio cable / rig!"
    ),
    "push.silence_title": "📡 FT8 Pi: radio silence (audio dead)",
    "push.decoder_late_msg": (
        "Decoder late: {count} slots >80% runtime (max {dur}s). "
        "CPU likely maxed out — turn off deep mode, reduce other loads?"
    ),
    "push.decoder_late_title": "🐢 FT8 Pi: decoder load high",
    "push.act_back_to_band": "🔄 Back to {band}",
    "push.freq_tamper_msg": (
        "Rig is on {mhz} MHz ({delta} Hz off {band}/{khz} kHz). Who turned the dial?"
    ),
    "push.freq_tamper_title": "📻 Frequency was changed",
    "push.act_start_hunting": "Start hunting",
    "push.act_start_cq": "Start CQ",
    "push.mode_alert_title": "⚠️ FT8 {host}: auto mode inactive",
    "push.mode_idle_bootmode": "Pi is idle — boot_mode={bm} but no mode active",
    "push.mode_idle_long": "Pi has had no auto mode for {min} min. Start hunting?",
    "push.daily_title": "🌅 FT8 Pi: 24h summary",
    "push.daily_qsos": "📡 QSOs last 24h: {n} ({uniq} unique calls)",
    "push.daily_dxccs": "🌍 DXCCs total: {n}",
    "push.daily_bestdx": "⭐ Best DX: {call} ({grid})",
    "push.daily_pending": "⏳ QRZ pending: {n} (waiting for connectivity)",
    "push.daily_all_uploaded": "✅ All QSOs at QRZ",
    "push.storm_closer": "Thunderstorm getting closer — {km} km away",
    "push.storm_msg": "Thunderstorm {km} km away (radius {radius} km)",
    "push.storm_title": "Thunderstorm warning",
    "push.country_dx_title": "📍 DX operating?",
    "push.country_dx_msg": (
        "GPS says you've been in {name} ({code}) for 30+ min. "
        "Switch to {code}/{call}?"
    ),
    "push.country_home_title": "🏠 Back home?",
    "push.country_home_msg": (
        "GPS says you're home but you're still transmitting as {current}/{call}. "
        "Back to the home call?"
    ),
    "push.country_mismatch_title": "⚠️ Operating-country mismatch",
    "push.country_mismatch_msg": (
        "You're transmitting {current}/{call} ({curname}) but GPS says you're in "
        "{detname} ({detected})."
    ),
    "push.act_unlock": "🔓 Release lock",
    "push.swr_runaway_msg": (
        "SWR rose to {swr} (limit {hard}) — TX was aborted immediately. "
        "Check the antenna!"
    ),
    "push.swr_runaway_title": "🚨 SWR emergency cut-off",
    "push.swr_warn_msg": (
        "SWR is rising to {swr} (warn threshold {warn}, lock at {hard}). "
        "Check the antenna — the standing wave may have worsened. "
        "On reaching {hard} the Pi locks TX automatically."
    ),
    "push.swr_warn_title": "⚠ SWR pre-warning",
    "push.alc_warn_msg": (
        "ALC at {alc}% (warn threshold {warn}%). Audio level too high — the "
        "closed loop should trim it down itself. If it doesn't recover, reduce "
        "audio_gain in the config manually or adjust TX power at the rig."
    ),
    "push.alc_warn_title": "⚠ ALC pre-warning",
    "push.clipping_msg": (
        "RX level at {dbfs} dBFS for {secs}s — near clipping. On the IC-7300 "
        "reduce MENU → SET → Connectors → AF/SQL Control → USB AF Output Level "
        "(default ~13, try 8-10)."
    ),
    "push.clipping_title": "🔊 FT8 {host} — RX level too high",
    "push.new_dxcc_prefix": "🆕 New DXCC! ",
    "push.qso_complete_prefix": "📡 QSO complete: ",
    "push.mf_suffix": " ⚓ Maritime op MF #{nr}",
    "push.act_stop": "⏹ Stop",
    "push.act_hunting": "🎯 Hunting",
    "push.act_cq": "📢 CQ",
    "push.upload_giveup_msg": (
        "{service} upload for QSO {call} given up after {attempts} attempts. "
        "QSO stays local in the log + ADIF — upload manually if needed."
    ),
    "push.upload_giveup_title": "⚠️ Upload given up",
    "push.spill_msg": (
        "QSO {call} couldn't be written to the DB — saved to the spill file, "
        "will be added automatically. Please check disk space / DB."
    ),
    "push.spill_title": "⚠️ QSO log error",
    "push.dxped_reminder_title": "📡 DXpedition QRV tomorrow: {call}",
    "push.act_release_lock": "Release lock",
    "push.tx_lock_title": "⚠️ FT8 {host} — TX lock",
}

_MESSAGES: dict[str, dict[str, str]] = {"de": _DE, "en": _EN}

_default_lang = "de"


def set_default_lang(lang: str) -> None:
    """Wire the appliance default language (from ``config.ui.language``)."""
    global _default_lang
    if lang in _MESSAGES:
        _default_lang = lang


def default_lang() -> str:
    return _default_lang


def translate(key: str, lang: str | None = None, /, **params: object) -> str:
    """Look up *key* in *lang* (falling back to the default, then DE, then key)."""
    table = _MESSAGES.get(lang or _default_lang) or _MESSAGES[_default_lang]
    template = table.get(key)
    if template is None:
        template = _MESSAGES["de"].get(key)
    if template is None:
        return key
    if params:
        try:
            return template.format(**params)
        except (KeyError, IndexError):
            return template
    return template
