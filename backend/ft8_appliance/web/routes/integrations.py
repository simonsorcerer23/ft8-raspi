"""Online-integration endpoints — callsign-lookup, solar, PSK Reporter.

All integrations share the resilience pattern from
``integrations/base.py``: cached + circuit-broken + graceful degrade.
The web layer just exposes them; failures yield empty/null payloads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ...runtime import Orchestrator
from ..deps import get_orchestrator

router = APIRouter()


# ---------------------------------------------------------------------------
class CallsignInfo(BaseModel):
    call: str
    name: str | None = None
    qth: str | None = None
    grid: str | None = None
    country: str | None = None
    continent: str | None = None
    source: str | None = None  # "qrz" | "hamqth" | "cty"
    image_url: str | None = None


@router.get("/callsign/{call}", response_model=CallsignInfo)
async def callsign_lookup(
    call: str, orch: Orchestrator = Depends(get_orchestrator)
) -> CallsignInfo:
    """Best-effort callsign lookup. Falls back through QRZ → HamQTH → cty.dat."""
    call = call.upper().strip()
    info = CallsignInfo(call=call)

    # 1. QRZ
    if orch.integrations.qrz and orch.integrations.qrz.enabled:
        rec = await orch.integrations.qrz.callsign(call)
        if rec is not None:
            info.name = f"{rec.first_name or ''} {rec.last_name or ''}".strip() or None
            info.grid = rec.grid
            info.country = rec.country
            info.image_url = rec.image_url
            info.source = "qrz"
            return info

    # 2. HamQTH
    if orch.integrations.hamqth and orch.integrations.hamqth.enabled:
        rec = await orch.integrations.hamqth.callsign(call)
        if rec is not None:
            info.name = rec.name
            info.grid = rec.grid
            info.country = rec.country
            info.source = "hamqth"
            return info

    # 3. Offline cty.dat — country/continent only
    if orch.integrations.cty:
        rec = orch.integrations.cty.lookup(call)
        if rec is not None:
            info.country = rec.entity.name
            info.continent = rec.entity.continent
            info.source = "cty"
    return info


# ---------------------------------------------------------------------------
class SolarOut(BaseModel):
    sfi: int | None = None
    a_index: int | None = None
    k_index: int | None = None
    sunspots: int | None = None
    x_ray: str | None = None
    aurora: int | None = None
    updated: str | None = None
    available: bool = False


@router.get("/solar", response_model=SolarOut)
async def solar(orch: Orchestrator = Depends(get_orchestrator)) -> SolarOut:
    if not orch.integrations.hamqsl or not orch.integrations.hamqsl.enabled:
        return SolarOut()
    sd = await orch.integrations.hamqsl.solar()
    if sd is None:
        return SolarOut()
    return SolarOut(
        sfi=sd.sfi, a_index=sd.a_index, k_index=sd.k_index,
        sunspots=sd.sunspots, x_ray=sd.x_ray, aurora=sd.aurora,
        updated=sd.updated, available=True,
    )


# ---------------------------------------------------------------------------
class PskHeardRow(BaseModel):
    rx_call: str
    rx_grid: str | None = None
    snr_db: int | None = None
    band: str | None = None
    mode: str | None = None
    received_at: str


class PskHeardResponse(BaseModel):
    reports: list[PskHeardRow]


@router.get("/psk/who-heard-me", response_model=PskHeardResponse)
async def psk_who_heard_me(
    hours: int = Query(24, ge=1, le=72),
    orch: Orchestrator = Depends(get_orchestrator),
) -> PskHeardResponse:
    if not orch.integrations.psk_reporter or not orch.integrations.psk_reporter.enabled:
        return PskHeardResponse(reports=[])
    callsign = orch.state_machine.ctx.callsign
    rs = await orch.integrations.psk_reporter.who_heard_me(callsign, hours=hours)
    return PskHeardResponse(
        reports=[
            PskHeardRow(
                rx_call=r.rx_call, rx_grid=r.rx_grid, snr_db=r.snr_db,
                band=r.band, mode=r.mode, received_at=r.received_at.isoformat(),
            )
            for r in rs
        ]
    )


# ---------------------------------------------------------------------------
class BlitzortungStatus(BaseModel):
    enabled: bool
    alarm_radius_km: int
    nearest_km: float | None = None
    alarm: bool = False


@router.get("/blitzortung", response_model=BlitzortungStatus)
async def blitzortung_status(
    orch: Orchestrator = Depends(get_orchestrator),
) -> BlitzortungStatus:
    bz = orch.integrations.blitzortung
    if bz is None or not bz.enabled:
        return BlitzortungStatus(enabled=False, alarm_radius_km=30)
    gps = orch.gps.snapshot
    if gps.lat is None or gps.lon is None:
        return BlitzortungStatus(enabled=True, alarm_radius_km=bz.alarm_radius_km)
    nearest = bz.nearest_strike_km((gps.lat, gps.lon))
    return BlitzortungStatus(
        enabled=True,
        alarm_radius_km=bz.alarm_radius_km,
        nearest_km=nearest,
        alarm=bz.is_storm_nearby((gps.lat, gps.lon)),
    )


# ---------------------------------------------------------------------------
class DxSpotOut(BaseModel):
    ts: str
    spotter: str
    freq_hz: int
    spotted: str
    comment: str
    band: str | None
    # DX-Lookup via cty.dat — None when the call doesn't match any known
    # prefix or cty.dat isn't loaded. Lat/lon are the country centre, not
    # the actual operator location (cty.dat doesn't carry that).
    country: str | None = None
    continent: str | None = None
    lat: float | None = None
    lon: float | None = None


class DxSpotsResponse(BaseModel):
    enabled: bool
    spots: list[DxSpotOut]


@router.get("/dx-cluster", response_model=DxSpotsResponse)
async def dx_cluster_spots(
    minutes: int = Query(30, ge=1, le=180),
    ft8_only: bool = Query(True),
    orch: Orchestrator = Depends(get_orchestrator),
) -> DxSpotsResponse:
    """Recent DX cluster spots, FT8-filtered by default."""
    from ...util.bandplan import band_from_freq_hz

    cluster = orch.integrations.dx_cluster
    if cluster is None or not cluster.enabled:
        return DxSpotsResponse(enabled=False, spots=[])
    raw = cluster.recent(ft8_only=ft8_only, minutes=minutes)
    cty = orch.integrations.cty
    out: list[DxSpotOut] = []
    for s in raw:
        spot = DxSpotOut(
            ts=s.ts.isoformat(),
            spotter=s.spotter,
            freq_hz=s.freq_hz,
            spotted=s.spotted,
            comment=s.comment,
            band=band_from_freq_hz(s.freq_hz),
        )
        # Reichere mit cty.dat-Lookup an damit der Frontend-Renderer
        # die Spots auf der Welt-Karte platzieren kann (Country-Center
        # — Lat/Lon der einzelnen Op ist nicht im Cluster verfügbar).
        if cty is not None:
            rec = cty.lookup(s.spotted)
            if rec is not None:
                spot.country = rec.entity.name
                spot.continent = rec.entity.continent
                spot.lat = rec.entity.lat
                spot.lon = rec.entity.lon
        out.append(spot)
    return DxSpotsResponse(enabled=True, spots=out)
