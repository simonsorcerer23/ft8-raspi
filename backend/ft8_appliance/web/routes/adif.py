"""ADIF export of the QSO log.

ADIF is the ham-radio standard log interchange format. We support
output only — import comes later if ever. Format reference:
https://adif.org/314/ADIF_314.htm
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select

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


def _render_adif(qsos: list[Qso], active_op: str, cty) -> str:
    out = StringIO()
    progver = __version__
    out.write(
        f"FT8 Raspi Appliance — ADIF export v{progver}\n"
        f"<ADIF_VER:5>3.1.4 "
        f"<PROGRAMID:9>ft8-raspi "
        f"{_adif_field('PROGRAMVERSION', progver)} "
        f"<EOH>\n"
    )
    for q in qsos:
        date_on, time_on = _adif_date_time(q.qso_start)
        date_off, time_off = _adif_date_time(q.qso_end)
        op_call = (q.user_callsign or active_op).upper()
        # ADIF trennt die beiden Felder: OPERATOR ist die Person (Heimat-
        # Call), STATION_CALLSIGN das Rufzeichen, das ueber die Luft ging —
        # bei Auslandsbetrieb also "9A/DK9XR". Die Spalte wird beim Loggen
        # genau dafuer gefuellt (StateMachine._emit_log_qso), der Export las
        # sie nur nie und schrieb in beide Felder den Heimat-Call. Damit
        # bekamen LotW/eQSL/ClubLog aus diesem File DX-QSOs unter dem
        # falschen Rufzeichen. ClubLog- und QRZ-Direktupload machen es
        # schon richtig — nur der manuelle Export nicht.
        station_call = (q.station_callsign or op_call).upper()
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
            _adif_field("STATION_CALLSIGN", station_call),
            _adif_field("COUNTRY", dxcc_name),
            "<EOR>",
        ])
        out.write(fields + "\n")
    return out.getvalue()


def _operator_label(orch: Orchestrator, operator: str | None = None) -> str:
    raw = operator or orch.config.operator.callsign
    return raw.upper().strip()


def _manual_filename(operator: str, batch_id: str) -> str:
    return f"{operator.lower()}_clublog_manual_{batch_id}.adif"


class ClubLogManualExportRequest(BaseModel):
    operator: str | None = None


class ClubLogManualStatus(BaseModel):
    operator: str
    unexported_count: int
    exported_unconfirmed_count: int
    last_batch_id: str | None = None
    last_exported_at: datetime | None = None
    last_batch_qso_count: int = 0
    last_download_url: str | None = None


class ClubLogManualExportResponse(BaseModel):
    operator: str
    batch_id: str | None
    qso_count: int
    filename: str | None = None
    download_url: str | None = None


class ClubLogManualConfirmResponse(BaseModel):
    operator: str
    batch_id: str
    confirmed_count: int
    already_uploaded_count: int


@router.get("/log/adif", response_class=PlainTextResponse)
async def export_adif(
    orch: Orchestrator = Depends(get_orchestrator),
    operator: str | None = Query(
        default=None,
        description=(
            "Filter: nur QSOs dieses user_callsign exportieren. "
            "Default = alle. Beispiel: ?operator=DO3XR fuer ClubLog-Upload "
            "des DO3XR-Accounts."
        ),
    ),
) -> PlainTextResponse:
    """Return the QSO log as an ADIF file.

    Sebastian Audit 2026-05-24: dynamische Version + Multi-Op-Filename
    + STATION_CALLSIGN/OPERATOR/DXCC-Felder + COMMENT mit Software-Tag.

    v0.21.2 — optionaler ?operator=<call>-Filter fuer saubere ClubLog/QRZ-
    Account-Trennung. Ohne Parameter: alle QSOs (alter Default).
    """
    # Multi-Operator: aktiver Operator bestimmt Dateinamen. Wenn ein QSO
    # eine andere user_callsign hat (z.B. ein DK9XR-QSO im DO3XR-Export),
    # schreiben wir das per-row in OPERATOR + STATION_CALLSIGN — der
    # Dateiname zeigt aber den AKTIVEN Operator des Exports.
    active_op = orch.config.operator.callsign.upper()
    filter_op = operator.upper().strip() if operator else None
    cty = getattr(getattr(orch, "integrations", None), "cty", None)

    async with session_scope() as s:
        stmt = select(Qso).order_by(desc(Qso.qso_start))
        if filter_op:
            stmt = stmt.where(Qso.user_callsign == filter_op)
        rows = list((await s.execute(stmt)).scalars())

    label = (filter_op or active_op).lower()
    filename = f"{label}_ft8.adif"
    return PlainTextResponse(
        _render_adif(rows, active_op, cty),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/plain; charset=utf-8",
        },
    )


@router.get("/log/clublog-manual/status", response_model=ClubLogManualStatus)
async def clublog_manual_status(
    orch: Orchestrator = Depends(get_orchestrator),
    operator: str | None = Query(None),
) -> ClubLogManualStatus:
    op = _operator_label(orch, operator)
    async with session_scope() as s:
        unexported = int((await s.execute(
            select(func.count())
            .select_from(Qso)
            .where(Qso.user_callsign == op)
            .where(Qso.clublog_uploaded == False)  # noqa: E712
            .where(Qso.clublog_manual_export_batch.is_(None))
        )).scalar_one())
        exported_unconfirmed = int((await s.execute(
            select(func.count())
            .select_from(Qso)
            .where(Qso.user_callsign == op)
            .where(Qso.clublog_uploaded == False)  # noqa: E712
            .where(Qso.clublog_manual_export_batch.is_not(None))
        )).scalar_one())
        last = (await s.execute(
            select(Qso.clublog_manual_export_batch, Qso.clublog_manual_exported_at)
            .where(Qso.user_callsign == op)
            .where(Qso.clublog_manual_export_batch.is_not(None))
            .order_by(desc(Qso.clublog_manual_exported_at))
            .limit(1)
        )).first()
        last_batch_id = str(last[0]) if last and last[0] else None
        last_exported_at = last[1] if last else None
        last_count = 0
        if last_batch_id:
            last_count = int((await s.execute(
                select(func.count())
                .select_from(Qso)
                .where(Qso.user_callsign == op)
                .where(Qso.clublog_manual_export_batch == last_batch_id)
            )).scalar_one())
    last_download_url = (
        f"/api/log/clublog-manual/{last_batch_id}/adif?operator={op}"
        if last_batch_id else None
    )
    return ClubLogManualStatus(
        operator=op,
        unexported_count=unexported,
        exported_unconfirmed_count=exported_unconfirmed,
        last_batch_id=last_batch_id,
        last_exported_at=last_exported_at,
        last_batch_qso_count=last_count,
        last_download_url=last_download_url,
    )


@router.post("/log/clublog-manual/export", response_model=ClubLogManualExportResponse)
async def create_clublog_manual_export(
    payload: ClubLogManualExportRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> ClubLogManualExportResponse:
    op = _operator_label(orch, payload.operator)
    now = datetime.now(UTC)
    batch_id = f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    async with session_scope() as s:
        pending_batch = (await s.execute(
            select(Qso.clublog_manual_export_batch)
            .where(Qso.user_callsign == op)
            .where(Qso.clublog_uploaded == False)  # noqa: E712
            .where(Qso.clublog_manual_export_batch.is_not(None))
            .order_by(desc(Qso.clublog_manual_exported_at))
            .limit(1)
        )).scalar_one_or_none()
        if pending_batch:
            pending_count = int((await s.execute(
                select(func.count())
                .select_from(Qso)
                .where(Qso.user_callsign == op)
                .where(Qso.clublog_uploaded == False)  # noqa: E712
                .where(Qso.clublog_manual_export_batch == pending_batch)
            )).scalar_one())
            return ClubLogManualExportResponse(
                operator=op,
                batch_id=pending_batch,
                qso_count=pending_count,
                filename=_manual_filename(op, pending_batch),
                download_url=f"/api/log/clublog-manual/{pending_batch}/adif?operator={op}",
            )

        rows = list((await s.execute(
            select(Qso)
            .where(Qso.user_callsign == op)
            .where(Qso.clublog_uploaded == False)  # noqa: E712
            .where(Qso.clublog_manual_export_batch.is_(None))
            .order_by(Qso.qso_start.asc())
        )).scalars())
        for qso in rows:
            qso.clublog_manual_export_batch = batch_id
            qso.clublog_manual_exported_at = now
    if not rows:
        return ClubLogManualExportResponse(
            operator=op,
            batch_id=None,
            qso_count=0,
        )
    return ClubLogManualExportResponse(
        operator=op,
        batch_id=batch_id,
        qso_count=len(rows),
        filename=_manual_filename(op, batch_id),
        download_url=f"/api/log/clublog-manual/{batch_id}/adif?operator={op}",
    )


@router.get("/log/clublog-manual/{batch_id}/adif", response_class=PlainTextResponse)
async def export_clublog_manual_batch(
    batch_id: str,
    orch: Orchestrator = Depends(get_orchestrator),
    operator: str | None = Query(None),
) -> PlainTextResponse:
    op = _operator_label(orch, operator)
    cty = getattr(getattr(orch, "integrations", None), "cty", None)
    async with session_scope() as s:
        rows = list((await s.execute(
            select(Qso)
            .where(Qso.user_callsign == op)
            .where(Qso.clublog_manual_export_batch == batch_id)
            .order_by(Qso.qso_start.asc())
        )).scalars())
    if not rows:
        raise HTTPException(status_code=404, detail="Export-Batch nicht gefunden")
    return PlainTextResponse(
        _render_adif(rows, op, cty),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_manual_filename(op, batch_id)}"'
            ),
            "Content-Type": "text/plain; charset=utf-8",
        },
    )


@router.post(
    "/log/clublog-manual/{batch_id}/confirm",
    response_model=ClubLogManualConfirmResponse,
)
async def confirm_clublog_manual_batch(
    batch_id: str,
    payload: ClubLogManualExportRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> ClubLogManualConfirmResponse:
    op = _operator_label(orch, payload.operator)
    now = datetime.now(UTC)
    async with session_scope() as s:
        rows = list((await s.execute(
            select(Qso)
            .where(Qso.user_callsign == op)
            .where(Qso.clublog_manual_export_batch == batch_id)
        )).scalars())
        if not rows:
            raise HTTPException(status_code=404, detail="Export-Batch nicht gefunden")
        confirmed = 0
        already = 0
        for qso in rows:
            if qso.clublog_uploaded:
                already += 1
                continue
            qso.clublog_uploaded = True
            qso.clublog_manual_confirmed_at = now
            confirmed += 1
    return ClubLogManualConfirmResponse(
        operator=op,
        batch_id=batch_id,
        confirmed_count=confirmed,
        already_uploaded_count=already,
    )
