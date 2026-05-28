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

Sebastian-Note 2026-05-28: ClubLog akzeptiert nur Application Passwords
(generiert in Settings → Application Passwords), NICHT das normale Login-
Passwort. Application Passwords sind 1× sichtbar bei Erstellung; danach
weg. Format z.B. ``REDACTED-OLD-APP-PASSWORD-WAS-HERE``.
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
    my_call: str,
    qso: Qso,
    *,
    timeout: float = 15.0,
) -> None:
    """POST one QSO to ClubLog realtime endpoint. Raises on any non-OK response.

    Antwort-Erkennung: HTTP 200 + Body startet mit "OK" → Erfolg.
    Sonst Body als ClubLogError werfen (z.B. "Authentication failed",
    "Duplicate QSO", "Bad ADIF").
    """
    adif = _qso_to_adif(qso, my_call)
    body = urlencode({
        "email": email,
        "password": app_password,
        "callsign": my_call,
        "adif": adif,
        "api": "ft8-appliance/0.21.0",  # client identifier (ClubLog logs)
    })
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            CLUBLOG_URL,
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    # HTTP-Fehler: 401/403 = auth, 5xx = ClubLog down → beides als Error werfen,
    # Orchestrator retried bei 5xx automatisch.
    if r.status_code != 200:
        raise ClubLogError(
            f"ClubLog HTTP {r.status_code}: {r.text[:200]}"
        )
    body_text = (r.text or "").strip()
    # ClubLog antwortet typisch mit leerem Body bei OK ODER "OK" — beides
    # akzeptieren. Fehler kommen als Klartext zurueck (z.B. "Duplicate
    # QSO ignored", "Login failed").
    if body_text and not body_text.upper().startswith("OK"):
        raise ClubLogError(f"ClubLog rejected: {body_text[:200]}")
    # Erfolg — kein Return-Value (anders als QRZ kein logbook_id).
