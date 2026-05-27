"""Control endpoints — POST-only, mutate orchestrator state."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...runtime import Orchestrator
from ..deps import get_orchestrator

router = APIRouter()


class ControlResponse(BaseModel):
    ok: bool
    state: str
    detail: str | None = None


@router.post("/cq", response_model=ControlResponse)
async def start_cq(orch: Orchestrator = Depends(get_orchestrator)) -> ControlResponse:
    await orch.handle_start_cq()
    s = orch.status()
    return ControlResponse(
        ok=True, state=s.state, detail=s.last_lock_reason if s.state == "TX_LOCKED" else None
    )


@router.post("/stop", response_model=ControlResponse)
async def stop(orch: Orchestrator = Depends(get_orchestrator)) -> ControlResponse:
    await orch.handle_stop()
    return ControlResponse(ok=True, state=orch.status().state)


@router.post("/panic", response_model=ControlResponse)
async def panic(orch: Orchestrator = Depends(get_orchestrator)) -> ControlResponse:
    await orch.handle_panic()
    return ControlResponse(ok=True, state=orch.status().state, detail="panic")


class HuntFilterRequest(BaseModel):
    skip_worked: bool | None = None
    dxcc_only: bool | None = None


@router.post("/hunt-filter", response_model=ControlResponse)
async def hunt_filter(
    req: HuntFilterRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> ControlResponse:
    """Toggle der Hunting-Filter direkt vom Funk-Dashboard.

    Nicht-gesetzte Felder bleiben unverändert. Schreibt sowohl in
    den In-Memory-Config als auch auf Disk (Restart-fest).
    """
    if req.skip_worked is not None:
        orch.config.operating.hunt_skip_worked = req.skip_worked
    if req.dxcc_only is not None:
        orch.config.operating.hunt_dxcc_only = req.dxcc_only
    await orch.persist_config()
    return ControlResponse(
        ok=True, state=orch.status().state,
        detail=f"skip_worked={orch.config.operating.hunt_skip_worked} "
               f"dxcc_only={orch.config.operating.hunt_dxcc_only}",
    )


class SetFreqRequest(BaseModel):
    freq_hz: int


@router.post("/set-freq", response_model=ControlResponse)
async def set_freq(
    req: SetFreqRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> ControlResponse:
    """Setze die Rig-Frequenz direkt via CAT.

    Wird hauptsächlich von der ntfy-Action "Auf <Band> zurück" genutzt
    nachdem ein Frequenz-Tamper erkannt wurde — Tap aufs Handy schickt
    POST mit dem Soll-Wert, Pi schreibt's via Hamlib zurück ans Rig.
    """
    try:
        await orch.handle_set_freq(req.freq_hz)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"set_freq failed: {exc}")
    return ControlResponse(ok=True, state=orch.status().state,
                           detail=f"freq_hz={req.freq_hz}")


class SetModeRequest(BaseModel):
    mode: str
    bandwidth_hz: int = 2700


@router.post("/set-mode", response_model=ControlResponse)
async def set_mode(
    req: SetModeRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> ControlResponse:
    """Setze den Rig-Modus direkt via CAT.

    Wird vor allem vom ntfy-Tamper-Push "Auf PKTUSB zurueck"-Button genutzt
    wenn jemand am Frontpanel den Modus aus dem Daten-Mode rausgedreht
    hat (Sebastian 2026-05-24).
    """
    try:
        await orch.handle_set_mode(req.mode, req.bandwidth_hz)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"set_mode failed: {exc}")
    return ControlResponse(ok=True, state=orch.status().state,
                           detail=f"mode={req.mode} bw={req.bandwidth_hz}Hz")


@router.post("/shutdown", response_model=ControlResponse)
async def shutdown(orch: Orchestrator = Depends(get_orchestrator)) -> ControlResponse:
    """Sauberes System-Shutdown: STOP_TX, PTT off, ntfy-Push, dann poweroff.

    Dad kann den Pi danach gefahrlos vom Strom trennen (~30 s Wartezeit
    nach Response damit systemd Filesystem-Buffer flushen kann).
    """
    await orch.handle_shutdown()
    return ControlResponse(ok=True, state=orch.status().state, detail="shutdown")


@router.post("/reboot", response_model=ControlResponse)
async def reboot(orch: Orchestrator = Depends(get_orchestrator)) -> ControlResponse:
    """Sauberer System-Reboot: STOP_TX, PTT off, ntfy-Push, dann reboot.

    Sebastian 2026-05-26 v0.8.2: gleiche Sicherheitskette wie shutdown,
    aber Pi kommt nach ~30 s automatisch wieder hoch.
    """
    await orch.handle_reboot()
    return ControlResponse(ok=True, state=orch.status().state, detail="reboot")


@router.post("/reset-lock", response_model=ControlResponse)
async def reset_lock(orch: Orchestrator = Depends(get_orchestrator)) -> ControlResponse:
    await orch.handle_reset_lock()
    return ControlResponse(ok=True, state=orch.status().state)


class ReplyRequest(BaseModel):
    call_from: str
    call_to: str | None = None
    grid: str | None = None
    message: str
    snr_db: int | None = None
    dt_s: float | None = None
    freq_offset_hz: int | None = None
    band: str = "20m"


@router.post("/reply", response_model=ControlResponse)
async def reply(
    req: ReplyRequest, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    from datetime import UTC, datetime

    from ...statemachine import DecodedMsg

    decoded = DecodedMsg(
        ts=datetime.now(UTC),
        call_from=req.call_from,
        call_to=req.call_to,
        grid=req.grid,
        message=req.message,
        snr_db=req.snr_db,
        dt_s=req.dt_s,
        freq_offset_hz=req.freq_offset_hz,
        band=req.band,
    )
    await orch.handle_reply_to(decoded)
    return ControlResponse(ok=True, state=orch.status().state)


@router.post("/tail-end", response_model=ControlResponse)
async def tail_end(
    req: ReplyRequest, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    """v0.12.0 — User klickt 🎯 auf einen RR73/RRR/73-Decode.

    Wir rufen den call_from (= den Closer) direkt an wie nach einem CQ.
    Manueller Override des automatischen Tail-End-Hunters; setzt 24h-
    Cooldown nach Pick damit der Auto-Picker nicht doppelt feuert.
    """
    from datetime import UTC, datetime

    from ...statemachine import DecodedMsg

    decoded = DecodedMsg(
        ts=datetime.now(UTC),
        call_from=req.call_from,
        call_to=req.call_to,
        grid=req.grid,
        message=req.message,
        snr_db=req.snr_db,
        dt_s=req.dt_s,
        freq_offset_hz=req.freq_offset_hz,
        band=req.band,
    )
    await orch.handle_tail_end(decoded)
    return ControlResponse(ok=True, state=orch.status().state)


class AutoAnswerRequest(BaseModel):
    enabled: bool


@router.post("/auto-answer", response_model=ControlResponse)
async def auto_answer(
    req: AutoAnswerRequest, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    """Toggle hunting mode (auto-reply to any decoded CQ while idle)."""
    await orch.handle_set_auto_answer(req.enabled)
    return ControlResponse(
        ok=True,
        state=orch.status().state,
        detail=f"auto_answer={'on' if req.enabled else 'off'}",
    )


@router.post("/skip", response_model=ControlResponse)
async def skip_qso(orch: Orchestrator = Depends(get_orchestrator)) -> ControlResponse:
    """Drop the current QSO without logging."""
    await orch.handle_skip_qso()
    return ControlResponse(ok=True, state=orch.status().state, detail="QSO skipped")


class BlacklistAddRequest(BaseModel):
    call: str
    reason: str | None = None


@router.post("/blacklist", response_model=ControlResponse)
async def blacklist_add(
    req: BlacklistAddRequest, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    await orch.handle_blacklist_add(req.call, req.reason)
    return ControlResponse(ok=True, state=orch.status().state, detail=f"added {req.call.upper()}")


@router.delete("/blacklist/{call}", response_model=ControlResponse)
async def blacklist_remove(
    call: str, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    await orch.handle_blacklist_remove(call)
    return ControlResponse(ok=True, state=orch.status().state, detail=f"removed {call.upper()}")


# ---------------------------------------------------------------------------
# v0.14.0 Watchlist add/remove
class WatchlistAddRequest(BaseModel):
    call: str
    note: str | None = None


@router.post("/watchlist", response_model=ControlResponse)
async def watchlist_add(
    req: WatchlistAddRequest, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    await orch.handle_watchlist_add(req.call, req.note)
    return ControlResponse(
        ok=True, state=orch.status().state,
        detail=f"watching {req.call.upper()}",
    )


@router.delete("/watchlist/{call}", response_model=ControlResponse)
async def watchlist_remove(
    call: str, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    await orch.handle_watchlist_remove(call)
    return ControlResponse(
        ok=True, state=orch.status().state,
        detail=f"unwatched {call.upper()}",
    )


# ---------------------------------------------------------------------------
# v0.15.0 Reputation-Reset — User darf einen Call rehabilitieren.
@router.delete("/reputation/{call}", response_model=ControlResponse)
async def reputation_reset(
    call: str, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    await orch.handle_reputation_reset(call)
    return ControlResponse(
        ok=True, state=orch.status().state,
        detail=f"reputation reset for {call.upper()}",
    )


class TxPowerRequest(BaseModel):
    watts: int


@router.post("/tx-power", response_model=ControlResponse)
async def tx_power(
    req: TxPowerRequest, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    await orch.handle_tx_power(req.watts)
    return ControlResponse(ok=True, state=orch.status().state,
                           detail=f"tx_power_w={orch.status().tx_power_w}")


class AntennaRequest(BaseModel):
    name: str


@router.post("/antenna", response_model=ControlResponse)
async def set_antenna(
    req: AntennaRequest, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    await orch.handle_set_antenna(req.name)
    return ControlResponse(ok=True, state=orch.status().state,
                           detail=f"active_antenna={req.name}")


class BandSwitchRequest(BaseModel):
    band: str  # e.g. "20m"


@router.post("/band", response_model=ControlResponse)
async def switch_band(
    req: BandSwitchRequest, orch: Orchestrator = Depends(get_orchestrator)
) -> ControlResponse:
    """Switch the rig to the configured FT8 frequency of *band*."""
    band_cfg = next((b for b in orch.config.bands if b.name == req.band), None)
    if band_cfg is None:
        return ControlResponse(
            ok=False, state=orch.status().state,
            detail=f"band {req.band} not configured",
        )
    freq_hz = band_cfg.freq_khz * 1000
    try:
        await orch.handle_set_freq(freq_hz)
        # PKTUSB (= IC-7300 "USB-D") statt Plain-USB — sonst routet das
        # Rig die USB-CODEC-Audio nicht auf den Modulator → 0 W trotz PTT.
        # Sebastian 2026-05-23: auf 15 m gewechselt, plötzlich keine TX-Power
        # mehr; Ursache war dieser Band-Switch-Handler der Plain-USB schrieb.
        # Sebastian 2026-05-24: ueber handle_set_mode statt direkt auf
        # rig.set_mode, damit die Echo-Registrierung den Tamper-Detector
        # nicht versehentlich triggert.
        await orch.handle_set_mode("PKTUSB", 2700)
        # Auto-switch antenna if active one doesn't cover the new band
        ant = next(
            (a for a in orch.config.antennas if req.band in a.bands),
            None,
        )
        if ant is not None and ant.name != orch._active_antenna:
            await orch.handle_set_antenna(ant.name)
    except Exception as exc:
        return ControlResponse(
            ok=False, state=orch.status().state,
            detail=f"rig error: {exc}",
        )
    return ControlResponse(
        ok=True, state=orch.status().state,
        detail=f"band={req.band} freq={freq_hz}Hz",
    )
