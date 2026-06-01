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
