"""QRZ.com Logbook API uploader — offline-tolerant.

Different beast from :mod:`qrz` (XML lookup). The Logbook API lives at
``https://logbook.qrz.com/api`` and accepts ADIF records via POST. The
auth is an API key (different from the XML subscription's user/password),
generated from the QRZ logbook settings page.

We keep the network layer dumb: one synchronous POST per QSO, short
timeout, return ``logbook_id`` on success or raise. The orchestrator's
background task handles batching, retry, and queue depth — this module
just translates a :class:`Qso` row into ADIF and parses the response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import unquote_plus, urlencode

import httpx

from ..db.models import Qso

log = logging.getLogger(__name__)

QRZ_LOGBOOK_URL = "https://logbook.qrz.com/api"


class QrzLogbookError(RuntimeError):
    """Raised when QRZ refuses an upload (bad key, duplicate, malformed ADIF)."""


@dataclass(slots=True)
class QrzLogbookResult:
    logbook_id: str
    count: int  # records accepted (1 for INSERT)


@dataclass(slots=True)
class QrzLogbookStatus:
    """Ergebnis von ACTION=STATUS — fuer den Pre-Flight-Setup-Check.

    ``ok`` = Key gueltig. ``callsign``/``book_name``/``owner`` so weit QRZ
    sie liefert (die STATUS-Felder sind nicht streng spezifiziert; ``raw``
    haelt alles roh fuer den Fall, dass QRZ andere Namen verwendet)."""
    ok: bool
    reason: str | None = None
    callsign: str | None = None
    book_name: str | None = None
    owner: str | None = None
    qso_count: int | None = None
    confirmed: int | None = None
    dxcc_count: int | None = None
    raw: dict[str, str] = field(default_factory=dict)


def _qso_to_adif(qso: Qso, my_call: str) -> str:
    """Convert a :class:`Qso` row to a single-record ADIF string.

    Only fields QRZ recognises are emitted — extras would be ignored
    anyway but make debug logs noisy.
    """

    def fld(name: str, value: object) -> str:
        if value is None:
            return ""
        s = str(value)
        return f"<{name}:{len(s)}>{s}"

    # QRZ requires both date and time in their own fields.
    start: datetime = qso.qso_start
    qso_date = start.strftime("%Y%m%d")
    qso_time = start.strftime("%H%M%S")

    # v0.22.0 — wenn das QSO mit DX-Prefix gesendet wurde (z.B. 9A/DK9XR
    # aus Kroatien), nutze den station_callsign aus dem QSO-Row.
    # operator-Feld bleibt der Heimat-Call. ADIF-Standard: station_callsign
    # ist der Call der gesendet wurde, operator ist die physische Person.
    station_call = qso.station_callsign or my_call
    parts = [
        fld("call", qso.call),
        fld("qso_date", qso_date),
        fld("time_on", qso_time),
        fld("band", qso.band),
        fld("freq", f"{qso.freq_hz / 1_000_000:.4f}"),  # MHz
        fld("mode", qso.mode),
        fld("station_callsign", station_call),
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


# ADIF-Tag-Pattern: data-Felder haben "<NAME:LENGTH[:TYPE]>", Marker
# wie <EOR> und <EOH> haben NUR den Namen. group(2) None = Marker.
_ADIF_TAG = __import__("re").compile(
    r"<([A-Za-z_][A-Za-z0-9_]*)(?::(\d+)(?::[A-Za-z])?)?>",
)


def parse_adif(adif_text: str) -> list[dict[str, str]]:
    """O(n)-Parser für ADIF (ADI-Format).

    Single-Pass mit re.finditer + Substring-Slice — kein wiederholtes
    string-slicing für die Such-Operation. Bei 10k QSOs sind das
    Millisekunden statt Sekunden (vorher O(N²) durch slice-in-loop
    hatte uns den Event-Loop blockiert).

    Extrahiert <FIELD:LENGTH>VALUE-Pärchen, separiert auf <EOR>.
    Header (alles vor <EOH>) wird übersprungen. Keys lowercase.
    """
    import re
    text = adif_text
    eoh = re.search(r"<EOH>", text, flags=re.IGNORECASE)
    if eoh:
        text = text[eoh.end():]
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    pos = 0
    text_lower = text  # case-sensitive match via flags below
    while pos < len(text):
        m = _ADIF_TAG.search(text, pos)
        if m is None:
            break
        name_lc = m.group(1).lower()
        length_group = m.group(2)
        if length_group is None:
            # Marker-Tag ohne Längen-Angabe: <EOR> beendet Record,
            # <EOH> trennt Header (sollte schon abgeschnitten sein),
            # andere wie <USERID> ignorieren wir.
            if name_lc == "eor":
                if current.get("call"):
                    records.append(current)
                current = {}
            pos = m.end()
            continue
        length = int(length_group)
        value_start = m.end()
        current[name_lc] = text[value_start:value_start + length]
        pos = value_start + length
    # Letzter Record ohne EOR — selten, aber sicher ist sicher
    if current.get("call"):
        records.append(current)
    return records


async def fetch_log_adif(
    api_key: str,
    *,
    timeout: float = 60.0,
    options: str = "",
) -> list[dict[str, str]]:
    """Lade Dads komplettes QRZ-Logbook via ACTION=FETCH.

    Liefert eine Liste geparster Records (call/band/mode/gridsquare/
    dxcc/qso_date/...). Kann minutenlang dauern bei Tausenden von
    QSOs — daher 60 s Default-Timeout.
    """
    body = urlencode({
        "KEY": api_key,
        "ACTION": "FETCH",
        "OPTION": options,  # leer = alles
    })
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            QRZ_LOGBOOK_URL, content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    r.raise_for_status()
    text = r.text
    # Response-Format: "RESULT=OK&COUNT=...&ADIF=<...records...>"
    # ABER QRZ liefert die ADIF-Tags HTML-encoded: &lt;CALL:5&gt;
    # statt <CALL:5>. Plus &amp;-Escapes wo das ADIF-Value selbst
    # ein & enthält. Decodieren bevor wir parsen.
    import html
    idx = text.find("ADIF=")
    if idx < 0:
        if "RESULT=FAIL" in text or "RESULT=AUTH" in text:
            reason = ""
            for k in ("REASON=", "EXTENDED="):
                if k in text:
                    reason = text.split(k, 1)[-1].split("&", 1)[0].strip()
                    if reason:
                        break
            raise QrzLogbookError(reason or "QRZ FETCH failed")
        return []
    adif_text = html.unescape(text[idx + len("ADIF="):])
    return parse_adif(adif_text)


async def upload_qso(api_key: str, my_call: str, qso: Qso, *, timeout: float = 10.0) -> QrzLogbookResult:
    """POST one QSO to the QRZ logbook. Raises on any non-OK response.

    QRZ accepts ``ADIF=<record>`` form-encoded with ``ACTION=INSERT`` and
    the API key. Response is also URL-encoded key=value pairs.
    """
    adif = _qso_to_adif(qso, my_call)
    body = urlencode({
        "KEY": api_key,
        "ACTION": "INSERT",
        "ADIF": adif,
    })
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            QRZ_LOGBOOK_URL, content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    r.raise_for_status()
    # Response shape: "RESULT=OK&COUNT=1&LOGID=12345" (or RESULT=FAIL&REASON=...)
    fields = dict(part.split("=", 1) for part in r.text.split("&") if "=" in part)
    if fields.get("RESULT") != "OK":
        raise QrzLogbookError(
            fields.get("REASON") or fields.get("STATUS") or "QRZ rejected upload"
        )
    return QrzLogbookResult(
        logbook_id=fields.get("LOGID", ""),
        count=int(fields.get("COUNT", "1")),
    )


async def status(api_key: str, *, timeout: float = 10.0) -> QrzLogbookStatus:
    """ACTION=STATUS — Key validieren + Logbuch-Metadaten lesen.

    Fuer den Pre-Flight-Setup-Check: bestaetigt dass der Key live ist und
    zu welchem Logbuch/Call er gehoert, damit der Operator sieht ob er zum
    geplanten On-Air-Call passt. Eine einzige Anfrage, kein Retry.
    """
    body = urlencode({"KEY": api_key, "ACTION": "STATUS"})
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            QRZ_LOGBOOK_URL, content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    r.raise_for_status()
    fields = {
        k: unquote_plus(v)
        for k, v in (part.split("=", 1) for part in r.text.split("&") if "=" in part)
    }
    if fields.get("RESULT") != "OK":
        return QrzLogbookStatus(
            ok=False,
            reason=fields.get("REASON") or fields.get("STATUS") or "QRZ STATUS failed",
            raw=fields,
        )

    def _pick(*names: str) -> str | None:
        for n in names:
            for key in (n, n.upper(), n.lower()):
                if fields.get(key):
                    return fields[key]
        return None

    def _int(*names: str) -> int | None:
        v = _pick(*names)
        return int(v) if v and str(v).strip().isdigit() else None

    return QrzLogbookStatus(
        ok=True,
        callsign=_pick("CALLSIGN", "Callsign"),
        book_name=_pick("BOOK_NAME", "BOOKNAME", "Book Name", "BookName", "NAME"),
        owner=_pick("OWNER", "Owner", "BOOK_OWNER"),
        qso_count=_int("COUNT", "BOOK_COUNT", "Count"),
        confirmed=_int("CONFIRMED", "Confirmed"),
        dxcc_count=_int("DXCC_COUNT", "DXCC", "DXCCTOTAL"),
        raw=fields,
    )
