"""ClubLog real-time uploader — offline-tolerant, parallel zu QRZ-Logbook.

ClubLog (https://clublog.org) ist Michael G7VJR's DXCC-Tracker mit
DXpedition-Match-Engine + OQRS. API:

* Endpoint: ``https://clublog.org/realtime.php`` (one-QSO-per-call)
* Form-encoded POST mit ``email``, ``password`` (= Application Password),
  ``callsign`` (Logger-Call) und ``adif`` (Single-Record-ADIF).
* Response: ``200 OK`` + plain "OK" bei Erfolg, sonst Fehlertext im Body.

Wir bleiben bei dem dummen Network-Layer-Schema von qrz_logbook: ein POST
pro QSO, kurzes Timeout, return None bei OK oder raise. Drain-Logik (batch,
retry, queue depth) lebt im Orchestrator.

Note: ClubLog akzeptiert nur Application Passwords (generiert in
Settings → Application Passwords), NICHT das normale Login-Passwort.
Application Passwords sind 1× sichtbar bei Erstellung; danach weg.
Plus separater API-Key (40-char hex) via clublog.org/requestapikey.php.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlencode

import httpx

from ..db.models import Qso

log = logging.getLogger(__name__)

CLUBLOG_URL = "https://clublog.org/realtime.php"


class ClubLogError(RuntimeError):
    """Raised when ClubLog refuses an upload (bad credentials, duplicate, ...)."""


def _qso_to_adif(qso: Qso, my_call: str) -> str:
    """Convert a :class:`Qso` row to a single-record ADIF string.

    ClubLog akzeptiert das ADI-Format (Tags <FIELD:LEN>VALUE) — selbe
    Convention wie QRZ. ClubLog ignoriert unbekannte Felder, also ist
    der Output identisch zu QRZ-Upload.
    """

    def fld(name: str, value: object) -> str:
        if value is None:
            return ""
        s = str(value)
        return f"<{name}:{len(s)}>{s}"

    start: datetime = qso.qso_start
    qso_date = start.strftime("%Y%m%d")
    qso_time = start.strftime("%H%M%S")

    parts = [
        fld("call", qso.call),
        fld("qso_date", qso_date),
        fld("time_on", qso_time),
        fld("band", qso.band),
        fld("freq", f"{qso.freq_hz / 1_000_000:.4f}"),  # MHz
        fld("mode", qso.mode),
        fld("station_callsign", my_call),
        fld("operator", my_call),
    ]
    if qso.rst_sent is not None:
        parts.append(fld("rst_sent", qso.rst_sent))
    if qso.rst_rcvd is not None:
        parts.append(fld("rst_rcvd", qso.rst_rcvd))
    if qso.grid_rcvd:
        parts.append(fld("gridsquare", qso.grid_rcvd))
    if qso.my_grid:
        parts.append(fld("my_gridsquare", qso.my_grid))
    if qso.my_power_w is not None:
        parts.append(fld("tx_pwr", qso.my_power_w))
    if qso.notes:
        parts.append(fld("comment", qso.notes))
    parts.append("<eor>")
    return "".join(parts)


async def upload_qso(
    email: str,
    app_password: str,
    api_key: str,
    my_call: str,
    qso: Qso,
    *,
    timeout: float = 15.0,
) -> None:
    """POST one QSO to ClubLog realtime endpoint. Raises on any non-OK response.

    Erforderliche Credentials (alle 3):
      - email          → ClubLog-Account-Email
      - app_password   → Application Password aus Settings → App Passwords
      - api_key        → 40-char Hex-API-Key via clublog.org/requestapikey.php

    Response-Body-Erkennung (HTTP 200, beobachtete Varianten live 2026-05-28):
      - "OK"            → neu gespeichert (häufigste Antwort)
      - "QSO OK"        → ebenfalls Erfolg (Doku-Variante)
      - "Updated QSO"   → existierender QSO wurde aktualisiert (Erfolg)
      - "QSO Modified"  → ClubLog hat Korrekturen vorgenommen (Erfolg)
      - "Duplicate"     → schon im Log (kein Fehler, idempotent)
      - "QSO Duplicate" → dito (Doku-Variante)
      - Anderer Text    → Error (ClubLogError) — Drain-Loop entscheidet
        anhand des Wortlauts ob hard-reject (auth/login/bad adif) oder
        soft-defer (Netz/Throttle).
    """
    adif = _qso_to_adif(qso, my_call)
    body = urlencode({
        "email": email,
        "password": app_password,
        "callsign": my_call,
        "adif": adif,
        "api": api_key,
    })
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            CLUBLOG_URL,
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.status_code != 200:
        raise ClubLogError(
            f"ClubLog HTTP {r.status_code}: {r.text[:200]}"
        )
    body_text = (r.text or "").strip()
    upper = body_text.upper()
    # Erfolgs-Marker — ClubLog antwortet in mehreren Varianten je nachdem
    # ob der QSO neu, updated oder duplicate ist. Alle sind aus unserer
    # Sicht Erfolg (Idempotenz inklusive).
    success_markers = (
        "OK",            # häufigste Antwort live
        "QSO OK",
        "UPDATED QSO",   # existierender Eintrag wurde geupdated
        "QSO MODIFIED",
        "DUPLICATE",
        "QSO DUPLICATE",
    )
    if any(upper.startswith(m) or m in upper for m in success_markers):
        return
    # Leerer Body defensiv als OK (sehr selten, manche Endpoints).
    if not body_text:
        return
    raise ClubLogError(f"ClubLog rejected: {body_text[:200]}")
