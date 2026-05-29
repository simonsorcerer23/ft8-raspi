"""Operator-Profile-Management (Sebastian 2026-05-23).

Endpoints:
* GET  /api/operators                — Liste aller Operator-Profile + aktiver
* GET  /api/operators/active         — aktueller Operator
* POST /api/operators/select         — aktiven Operator wechseln (Hot-Switch)
* POST /api/operators                — neues Operator-Profil anlegen
* DELETE /api/operators/{callsign}   — Profil loeschen (nur wenn nicht aktiv
                                       und kein QSO-Verlauf in der DB)

Operator-Profile sind in der YAML-Config gepflegt. Der Hot-Switch ruft
:meth:`Orchestrator.switch_operator` auf, der die State-Machine zurueck-
setzt und worked/blacklist fuer den neuen User aus der DB neu laedt.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ...config import OperatorConfig
from ...db import session_scope
from ...db.models import Qso
from ...runtime import Orchestrator
from ..deps import get_orchestrator

router = APIRouter()


class OperatorOut(BaseModel):
    callsign: str
    default_locator: str | None
    default_power_w: int
    license_class: str
    qrz_user: str | None
    has_qrz_credentials: bool
    # v0.21.0 — ClubLog. App-Password wird NICHT exposed (analog
    # qrz_password). has_clublog_credentials zeigt nur ob beide gesetzt
    # sind damit das UI einen Status anzeigen kann.
    clublog_email: str | None = None
    has_clublog_credentials: bool = False
    is_active: bool
    # v0.29.0 — zusaetzliche Sende-Calls (Prefix/Suffix) mit eigenem
    # QRZ-Logbook-Key. Nur die Calls, nicht die Keys.
    station_logbooks: list[str] = Field(default_factory=list)


class OperatorsResponse(BaseModel):
    operators: list[OperatorOut]
    active_callsign: str
    auto_login_seconds: int


class SelectRequest(BaseModel):
    callsign: str


class SelectResponse(BaseModel):
    ok: bool
    active_callsign: str
    message: str | None = None


def _to_out(op: OperatorConfig, active: str) -> OperatorOut:
    return OperatorOut(
        callsign=op.callsign,
        default_locator=op.default_locator,
        default_power_w=op.default_power_w,
        license_class=op.license_class,
        qrz_user=op.qrz_user,
        has_qrz_credentials=bool(op.qrz_user and op.qrz_logbook_api_key),
        clublog_email=op.clublog_email,
        has_clublog_credentials=bool(
            op.clublog_email and op.clublog_app_password and op.clublog_api_key
        ),
        is_active=(op.callsign == active),
        station_logbooks=sorted(op.qrz_logbooks.keys()),
    )


@router.get("/operators", response_model=OperatorsResponse)
async def list_operators(
    orch: Orchestrator = Depends(get_orchestrator),
) -> OperatorsResponse:
    cfg = orch.config
    active = cfg.active_callsign or (cfg.operators[0].callsign if cfg.operators else "")
    return OperatorsResponse(
        operators=[_to_out(op, active) for op in cfg.operators],
        active_callsign=active,
        auto_login_seconds=cfg.operator_auto_login_seconds,
    )


class PreflightSection(BaseModel):
    status: Literal["ok", "not_set_up", "warn", "error"]
    detail: str | None = None


class PreflightResponse(BaseModel):
    callsign: str          # geprueft: der On-Air-Call
    owner_callsign: str    # welches Profil ihn besitzt
    qrz: PreflightSection
    clublog: PreflightSection


@router.get("/operators/preflight", response_model=PreflightResponse)
async def preflight(
    callsign: str | None = None,
    orch: Orchestrator = Depends(get_orchestrator),
) -> PreflightResponse:
    """Pre-Flight-Setup-Check: ist ein On-Air-Call upload-bereit?

    Prueft QRZ (Key vorhanden + live) und ClubLog (Call registriert) fuer
    den angegebenen Call — default: der effektive TX-Call des aktiven
    Operators (inkl. DX-Prefix). Query-Param statt Pfad, weil DX-Calls
    einen Slash enthalten (9A/DO3XR). Macht je hoechstens EINE Netz-
    Anfrage pro Dienst (ClubLog-Firewall-safe).
    """
    call = (callsign or orch.effective_tx_call()).upper().strip()
    if not call:
        raise HTTPException(status_code=400, detail="kein Callsign")
    res = await orch.check_call_setup(call)
    return PreflightResponse(
        callsign=res["callsign"],
        owner_callsign=res["owner"],
        qrz=PreflightSection(**res["qrz"]),
        clublog=PreflightSection(**res["clublog"]),
    )


@router.get("/operators/active", response_model=OperatorOut)
async def get_active_operator(
    orch: Orchestrator = Depends(get_orchestrator),
) -> OperatorOut:
    op = orch.config.operator
    return _to_out(op, op.callsign)


@router.post("/operators/select", response_model=SelectResponse)
async def select_operator(
    payload: SelectRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> SelectResponse:
    """Aktiven Operator wechseln. Loest Hot-Swap im Orchestrator aus:
    State-Machine wird zurueckgesetzt, worked-Set + Blacklist werden
    fuer den neuen User aus der DB neu hydratiert, QRZ-Client mit den
    neuen Credentials neu initialisiert.

    Wenn gerade ein QSO laeuft → bail vor Switch (Partner kriegt Timeout).
    """
    target = payload.callsign.upper().strip()
    cfg = orch.config
    valid = {op.callsign for op in cfg.operators}
    if target not in valid:
        raise HTTPException(
            status_code=404,
            detail=f"unknown callsign {target!r}; have {sorted(valid)}",
        )
    if target == cfg.active_callsign:
        return SelectResponse(ok=True, active_callsign=target, message="bereits aktiv")
    try:
        await orch.switch_operator(target)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SelectResponse(ok=True, active_callsign=target, message="switched")


# ---------------------------------------------------------------------------
class CreateOperatorRequest(BaseModel):
    """Neues Operator-Profil anlegen.

    Validierung passiert ueber OperatorConfig — Callsign-Pattern und
    Locator-Pattern werden dort gegen Regexes geprueft. Die QRZ-Felder
    sind optional, brauchst sie aber wenn du Auto-Upload nutzen willst.
    """
    callsign: str
    default_locator: str | None = None
    default_power_w: int = Field(default=10, ge=1, le=750)
    license_class: Literal["A", "E", "N"] = "A"
    qrz_user: str | None = None
    qrz_password: str | None = None
    qrz_logbook_api_key: str | None = None
    clublog_email: str | None = None
    clublog_app_password: str | None = None
    clublog_api_key: str | None = None


class CreateOperatorResponse(BaseModel):
    ok: bool
    operator: OperatorOut


@router.post("/operators", response_model=CreateOperatorResponse, status_code=201)
async def create_operator(
    payload: CreateOperatorRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> CreateOperatorResponse:
    """Neues Operator-Profil anlegen.

    Validiert das Profil ueber OperatorConfig (Callsign-Regex, Locator-
    Regex), checked dass kein Operator mit dem Callsign schon existiert,
    haengt es an cfg.operators an und persistiert die Config auf Disk.
    Der neue Operator wird *nicht* automatisch aktiv — dafuer ist
    /operators/select da. Trennung damit man Profile vorbereiten kann
    ohne den laufenden Betrieb zu stoeren.
    """
    try:
        new_op = OperatorConfig(
            callsign=payload.callsign,
            default_locator=payload.default_locator,
            default_power_w=payload.default_power_w,
            license_class=payload.license_class,
            qrz_user=payload.qrz_user,
            qrz_password=payload.qrz_password,
            qrz_logbook_api_key=payload.qrz_logbook_api_key,
            clublog_email=payload.clublog_email,
            clublog_app_password=payload.clublog_app_password,
            clublog_api_key=payload.clublog_api_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid operator: {exc}")
    existing = {op.callsign for op in orch.config.operators}
    if new_op.callsign in existing:
        raise HTTPException(
            status_code=409,
            detail=f"operator {new_op.callsign} existiert bereits",
        )
    orch.config.operators.append(new_op)
    await orch.persist_config()
    return CreateOperatorResponse(
        ok=True,
        operator=_to_out(new_op, orch.config.active_callsign or ""),
    )


class DeleteOperatorResponse(BaseModel):
    ok: bool
    deleted: str


@router.delete("/operators/{callsign}", response_model=DeleteOperatorResponse)
async def delete_operator(
    callsign: str,
    force: bool = False,
    orch: Orchestrator = Depends(get_orchestrator),
) -> DeleteOperatorResponse:
    """Operator-Profil loeschen.

    Sicherheits-Gates:
    1. Aktiver Operator kann nicht geloescht werden — erst weg-switchen
    2. Operator mit QSO-Historie kann nicht geloescht werden ohne
       ``?force=true`` — sonst waeren die alten QSO-Rows in der DB
       waisenkinder (user_callsign zeigt auf einen Operator den's
       nicht mehr gibt). Mit force=true behalten wir die DB-Rows aber
       entfernen das Profil — bei Bedarf kann der Operator spaeter
       mit dem gleichen Callsign neu angelegt werden und sieht seine
       alten QSOs wieder.
    3. Der letzte Operator kann nicht geloescht werden — der Pi
       braucht mindestens einen.
    """
    target = callsign.upper().strip()
    cfg = orch.config
    if target == (cfg.active_callsign or ""):
        raise HTTPException(
            status_code=400,
            detail="Aktiver Operator kann nicht geloescht werden — vorher wechseln.",
        )
    matching = [op for op in cfg.operators if op.callsign == target]
    if not matching:
        raise HTTPException(status_code=404, detail=f"unknown callsign {target!r}")
    if len(cfg.operators) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Letzter Operator kann nicht geloescht werden.",
        )
    if not force:
        async with session_scope() as s:
            qso_count = (await s.execute(
                select(func.count()).select_from(Qso)
                .where(Qso.user_callsign == target)
            )).scalar_one()
        if qso_count > 0:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{target} hat {qso_count} QSOs in der DB. "
                    "Loeschen waeisst die Rows — sende force=true wenn das ok ist."
                ),
            )
    cfg.operators = [op for op in cfg.operators if op.callsign != target]
    await orch.persist_config()
    return DeleteOperatorResponse(ok=True, deleted=target)


# ---------------------------------------------------------------------------
# v0.29.0 — Sende-Call-Logbuecher (Prefix/Suffix) eines Operators verwalten.
# QRZ braucht pro On-Air-Call (DO3XR/AM, 9A/DO3XR) ein eigenes Logbuch mit
# eigenem Key — die werden hier in OperatorConfig.qrz_logbooks gepflegt.
class StationLogbookRequest(BaseModel):
    on_air_call: str           # z.B. "DO3XR/AM" oder "9A/DO3XR"
    qrz_logbook_api_key: str


def _resolve_person(orch: Orchestrator, callsign: str) -> OperatorConfig:
    target = callsign.upper().strip()
    for op in orch.config.operators:
        if op.callsign == target:
            return op
    raise HTTPException(status_code=404, detail=f"unknown operator {target!r}")


@router.put("/operators/{callsign}/logbook", response_model=OperatorOut)
async def upsert_station_logbook(
    callsign: str,
    payload: StationLogbookRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> OperatorOut:
    """QRZ-Logbook-Key fuer einen Sende-Call setzen/aktualisieren."""
    op = _resolve_person(orch, callsign)
    call = payload.on_air_call.upper().strip()
    key = payload.qrz_logbook_api_key.strip()
    if not call or not key:
        raise HTTPException(status_code=400, detail="on_air_call und Key noetig")
    op.qrz_logbooks = {**op.qrz_logbooks, call: key}
    await orch.persist_config()
    return _to_out(op, orch.config.active_callsign or "")


@router.delete("/operators/{callsign}/logbook", response_model=OperatorOut)
async def delete_station_logbook(
    callsign: str,
    on_air_call: str,
    orch: Orchestrator = Depends(get_orchestrator),
) -> OperatorOut:
    """Einen Sende-Call-Logbook-Eintrag entfernen."""
    op = _resolve_person(orch, callsign)
    call = on_air_call.upper().strip()
    op.qrz_logbooks = {k: v for k, v in op.qrz_logbooks.items() if k != call}
    await orch.persist_config()
    return _to_out(op, orch.config.active_callsign or "")
