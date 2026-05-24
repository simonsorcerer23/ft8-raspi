"""ADIF export of the QSO log.

ADIF is the ham-radio standard log interchange format. We support
output only — import comes later if ever. Format reference:
https://adif.org/314/ADIF_314.htm
"""

from __future__ import annotations

from datetime import datetime
from io import StringIO

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc, select

from ...db import session_scope
from ...db.models import Qso

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


@router.get("/log/adif", response_class=PlainTextResponse)
async def export_adif() -> PlainTextResponse:
    """Return the entire QSO log as an ADIF file.

    Browser will download as ``log.adif`` if you GET it directly.
    """
    out = StringIO()
    out.write(
        "FT8 Hochgericht Appliance — ADIF export\n"
        f"<ADIF_VER:5>3.1.4 "
        f"<PROGRAMID:18>ft8-hochgericht-pi "
        f"<PROGRAMVERSION:5>0.1.0 "
        f"<EOH>\n"
    )

    async with session_scope() as s:
        rows = list(
            (await s.execute(select(Qso).order_by(desc(Qso.qso_start)))).scalars()
        )

    for q in rows:
        date_on, time_on = _adif_date_time(q.qso_start)
        date_off, time_off = _adif_date_time(q.qso_end)
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
            "<EOR>",
        ])
        out.write(fields + "\n")

    return PlainTextResponse(
        out.getvalue(),
        headers={
            "Content-Disposition": 'attachment; filename="dk9xr_ft8.adif"',
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
