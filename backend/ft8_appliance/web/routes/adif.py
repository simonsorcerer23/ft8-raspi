"""ADIF export of the QSO log.

ADIF is the ham-radio standard log interchange format. We support
output only — import comes later if ever. Format reference:
https://adif.org/314/ADIF_314.htm
"""

from __future__ import annotations

from datetime import datetime
from io import StringIO

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc, select

from ..._version import __version__
from ...db import session_scope
from ...db.models import Qso
from ...runtime import Orchestrator
from ..deps import get_orchestrator

router = APIRouter()


def _adif_field(tag: str, value: str | int | float | None) -> str:
    """Format one ADIF field. None or empty strings are omitted."""
    if value is None or value == "":
        return ""
    s = str(value)
    return f"<{tag}:{len(s)}>{s}"


def _adif_date_time(dt: datetime) -> tuple[str, str]:
    """Return (YYYYMMDD, HHMM) per ADIF spec."""
    return dt.strftime("%Y%m%d"), dt.strftime("%H%M")


def _dxcc_for_call(call: str | None, cty) -> str | None:
    """Best-effort DXCC-Entity-Name via cty.dat lookup.
    None wenn cty nicht geladen oder Lookup fehlschlaegt."""
    if not call or cty is None:
        return None
    try:
        rec = cty.lookup(call)
    except Exception:
        return None
    if rec is None:
        return None
    return rec.entity.name


@router.get("/log/adif", response_class=PlainTextResponse)
async def export_adif(
    orch: Orchestrator = Depends(get_orchestrator),
) -> PlainTextResponse:
    """Return the entire QSO log as an ADIF file.

    Sebastian Audit 2026-05-24: dynamische Version + Multi-Op-Filename
    + STATION_CALLSIGN/OPERATOR/DXCC-Felder + COMMENT mit Software-Tag.
    """
    out = StringIO()
    progver = __version__
    out.write(
        f"FT8 Raspi Appliance — ADIF export v{progver}\n"
        f"<ADIF_VER:5>3.1.4 "
        f"<PROGRAMID:9>ft8-raspi "
        f"{_adif_field('PROGRAMVERSION', progver)} "
        f"<EOH>\n"
    )

    # Multi-Operator: aktiver Operator bestimmt Dateinamen. Wenn ein QSO
    # eine andere user_callsign hat (z.B. ein DK9XR-QSO im DO3XR-Export),
    # schreiben wir das per-row in OPERATOR + STATION_CALLSIGN — der
    # Dateiname zeigt aber den AKTIVEN Operator des Exports.
    active_op = orch.config.operator.callsign.upper()
    cty = getattr(getattr(orch, "integrations", None), "cty", None)

    async with session_scope() as s:
        rows = list(
            (await s.execute(select(Qso).order_by(desc(Qso.qso_start)))).scalars()
        )

    for q in rows:
        date_on, time_on = _adif_date_time(q.qso_start)
        date_off, time_off = _adif_date_time(q.qso_end)
        op_call = (q.user_callsign or active_op).upper()
        dxcc_name = _dxcc_for_call(q.call, cty)
        fields = "".join([
            _adif_field("CALL", q.call),
            _adif_field("QSO_DATE", date_on),
            _adif_field("TIME_ON", time_on),
            _adif_field("QSO_DATE_OFF", date_off),
            _adif_field("TIME_OFF", time_off),
            _adif_field("BAND", q.band),
            _adif_field("FREQ", f"{q.freq_hz / 1_000_000:.6f}"),
            _adif_field("MODE", q.mode),
            _adif_field("RST_SENT", q.rst_sent),
            _adif_field("RST_RCVD", q.rst_rcvd),
            _adif_field("GRIDSQUARE", q.grid_rcvd),
            _adif_field("MY_GRIDSQUARE", q.my_grid),
            _adif_field("TX_PWR", q.my_power_w),
            # Sebastian-Audit v0.3.3: zusaetzliche Standard-Felder fuer
            # LotW / eQSL / ClubLog-Upload-Kompatibilitaet.
            _adif_field("OPERATOR", op_call),
            _adif_field("STATION_CALLSIGN", op_call),
            _adif_field("COUNTRY", dxcc_name),
            "<EOR>",
        ])
        out.write(fields + "\n")

    filename = f"{active_op.lower()}_ft8.adif"
    return PlainTextResponse(
        out.getvalue(),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
