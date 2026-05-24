"""Stats + recommendations endpoint — derived data, no I/O on hot path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from ...config import get_config
from ...db import session_scope
from ...db.models import Decode, Qso
from ...runtime import Orchestrator
from ...util.band_suggester import suggest_bands
from ...util.maidenhead import destination_point, great_circle, locator_to_latlon
from ..deps import get_orchestrator

router = APIRouter()


# ---------------------------------------------------------------------------
class BestDx(BaseModel):
    call: str
    grid: str | None
    band: str
    distance_km_estimate: int | None = None  # rough — Maidenhead -> haversine


class TodayStats(BaseModel):
    qso_today: int
    dxccs_today: int
    qso_7d: int
    qso_total: int
    decodes_last_hour: int
    best_dx_today: BestDx | None
    uptime_s: float


@router.get("/stats", response_model=TodayStats)
async def stats(orch: Orchestrator = Depends(get_orchestrator)) -> TodayStats:
    import time

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    hour_start = now - timedelta(hours=1)

    async with session_scope() as s:
        q_today_n = (await s.execute(
            select(func.count()).select_from(Qso).where(Qso.qso_start >= today_start)
        )).scalar_one()
        q_7d_n = (await s.execute(
            select(func.count()).select_from(Qso).where(Qso.qso_start >= week_start)
        )).scalar_one()
        q_total = (await s.execute(
            select(func.count()).select_from(Qso)
        )).scalar_one()
        d_h_n = (await s.execute(
            select(func.count()).select_from(Decode).where(Decode.ts >= hour_start)
        )).scalar_one()
        # today's QSO calls — for DXCC distinct count via cty.dat
        today_calls = list(
            (
                await s.execute(
                    select(Qso.call, Qso.band, Qso.grid_rcvd)
                    .where(Qso.qso_start >= today_start)
                    .order_by(desc(Qso.qso_start))
                )
            ).all()
        )

    dxccs_today_set: set[str] = set()
    best: BestDx | None = None
    best_dist = -1
    cty = orch.integrations.cty
    op_grid = orch.state_machine.ctx.my_grid
    for call, band, grid in today_calls:
        if cty is not None:
            rec = cty.lookup(call)
            if rec is not None:
                dxccs_today_set.add(rec.entity.name)
        # Distance estimate (Maidenhead centroid -> haversine)
        dist_km = _maidenhead_distance_km(op_grid, grid) if grid else None
        if dist_km is not None and dist_km > best_dist:
            best_dist = dist_km
            best = BestDx(
                call=call, grid=grid, band=band,
                distance_km_estimate=int(dist_km),
            )

    return TodayStats(
        qso_today=q_today_n,
        dxccs_today=len(dxccs_today_set),
        qso_7d=q_7d_n,
        qso_total=q_total,
        decodes_last_hour=d_h_n,
        best_dx_today=best,
        uptime_s=time.monotonic(),
    )


# ---------------------------------------------------------------------------
class BandSuggestion(BaseModel):
    band: str
    score: float
    reason: str
    current: bool = False


class BandSuggestionsResponse(BaseModel):
    current_band: str | None
    suggestions: list[BandSuggestion]


@router.get("/stats/band-suggestions", response_model=BandSuggestionsResponse)
async def band_suggestions(
    orch: Orchestrator = Depends(get_orchestrator),
) -> BandSuggestionsResponse:
    from ...util.bandplan import band_from_freq_hz

    # Pull SFI + activity from already-cached data
    sfi = k = None
    if orch.integrations.hamqsl:
        sd = await orch.integrations.hamqsl.solar()
        if sd is not None:
            sfi = sd.sfi
            k = sd.k_index

    # Decodes-per-band last hour from DB
    now = datetime.now(UTC)
    hour_start = now - timedelta(hours=1)
    async with session_scope() as s:
        rows = list(
            (
                await s.execute(
                    select(Decode.band, func.count())
                    .where(Decode.ts >= hour_start)
                    .group_by(Decode.band)
                )
            ).all()
        )
    per_band = {band: cnt for band, cnt in rows if band}

    current = band_from_freq_hz(orch._last_rig.freq_hz or 0)
    # Vorschläge auf konfigurierte + Rig-fähige + Antennen-abgedeckte
    # Bänder filtern. Sonst empfiehlt die Heuristik blind 10m obwohl
    # weder Antenne noch konfiguriert.
    cfg = orch.config
    configured = [b.name for b in cfg.bands]
    antenna_covers: set[str] = set()
    for a in cfg.antennas:
        antenna_covers.update(a.bands)
    sugs = suggest_bands(
        utc_hour=now.hour,
        sfi=sfi, k_index=k,
        decodes_per_band_last_hour=per_band,
        configured_bands=configured,
        rig_model=cfg.rig.model,
        antenna_covers=antenna_covers if antenna_covers else None,
    )
    return BandSuggestionsResponse(
        current_band=current,
        suggestions=[
            BandSuggestion(band=s.band, score=s.score, reason=s.reason,
                           current=(s.band == current))
            for s in sugs
        ],
    )


# ---------------------------------------------------------------------------
class HourBucket(BaseModel):
    utc_hour: int
    count: int


class BestTimeResponse(BaseModel):
    band: str
    buckets: list[HourBucket]


@router.get("/stats/best-time/{band}", response_model=BestTimeResponse)
async def best_time(band: str) -> BestTimeResponse:
    """Histogram of QSO count per UTC hour for *band*. Lets the UI tell
    Dad "20m worked best for me around 19-21 UTC last 30 days"."""
    cutoff = datetime.now(UTC) - timedelta(days=30)
    async with session_scope() as s:
        # SQLite trick: strftime('%H', qso_start)
        rows = list(
            (
                await s.execute(
                    select(
                        func.strftime("%H", Qso.qso_start),
                        func.count(),
                    )
                    .where(Qso.band == band)
                    .where(Qso.qso_start >= cutoff)
                    .group_by(func.strftime("%H", Qso.qso_start))
                )
            ).all()
        )
    counts = {int(h): n for h, n in rows if h is not None}
    return BestTimeResponse(
        band=band,
        buckets=[HourBucket(utc_hour=h, count=counts.get(h, 0)) for h in range(24)],
    )


# ---------------------------------------------------------------------------
def _maidenhead_distance_km(a: str | None, b: str | None) -> float | None:
    """Haversine between two Maidenhead locator centroids."""
    if not a or not b:
        return None
    try:
        lat1, lon1 = _locator_centre(a)
        lat2, lon2 = _locator_centre(b)
    except ValueError:
        return None
    from math import asin, cos, radians, sin, sqrt
    lat1r, lat2r = radians(lat1), radians(lat2)
    dlat = lat2r - lat1r
    dlon = radians(lon2 - lon1)
    h = sin(dlat / 2) ** 2 + cos(lat1r) * cos(lat2r) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


def _locator_centre(grid: str) -> tuple[float, float]:
    g = grid.upper()
    if len(g) < 4:
        raise ValueError("locator too short")
    lon = (ord(g[0]) - ord("A")) * 20 - 180 + int(g[2]) * 2 + 1.0
    lat = (ord(g[1]) - ord("A")) * 10 - 90 + int(g[3]) * 1 + 0.5
    if len(g) >= 6:
        lon += (ord(g[4]) - ord("A")) * (5 / 60) + 2.5 / 60 - 1.0
        lat += (ord(g[5]) - ord("A")) * (2.5 / 60) + 1.25 / 60 - 0.5
    return lat, lon


# ---------------------------------------------------------------------------
class SwrPoint(BaseModel):
    ts: str          # ISO-8601 UTC
    call: str
    band: str
    swr: float
    power_w: int | None = None


class SwrTrendResponse(BaseModel):
    """Per-QSO SWR-Werte über das gewünschte Zeitfenster.

    Wird vom Frontend als Liniendiagramm gerendert. Jeder Punkt ist
    ein abgeschlossenes QSO (swr_avg über die TX-Phase gemittelt).
    Threshold-Linie bei 2.0 ist im Frontend hartcodiert (entspricht
    OperatingConfig.swr_max-Default).
    """
    points: list[SwrPoint]
    threshold: float = 2.0


@router.get("/stats/swr-trend", response_model=SwrTrendResponse)
async def swr_trend(
    hours: int = Query(default=24, ge=1, le=720),
) -> SwrTrendResponse:
    """Liefere SWR-avg pro QSO der letzten *hours* Stunden, aufsteigend nach Zeit."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    async with session_scope() as s:
        rows = (await s.execute(
            select(Qso.qso_start, Qso.call, Qso.band, Qso.swr_avg, Qso.my_power_w)
            .where(Qso.qso_start >= cutoff)
            .where(Qso.swr_avg.is_not(None))
            .order_by(Qso.qso_start.asc())
        )).all()
    return SwrTrendResponse(
        points=[
            SwrPoint(
                ts=row.qso_start.isoformat() if hasattr(row.qso_start, "isoformat") else str(row.qso_start),
                call=row.call,
                band=row.band,
                swr=round(float(row.swr_avg), 2),
                power_w=row.my_power_w,
            )
            for row in rows
        ],
    )


# ---------------------------------------------------------------------------
class CoveragePoint(BaseModel):
    azimuth_deg: int      # Bin-Mitte 0..355 (in 5°-Schritten)
    distance_km: int      # Distanz zur weitesten Station in diesem Bin
    report_count: int     # Wieviele Reports kamen aus diesem Bin
    latest_age_h: float   # Wie alt ist der frischste Report im Bin (in Stunden)
    far_lat: float        # Lat des Polygon-Eckpunkts
    far_lon: float        # Lon des Polygon-Eckpunkts


class CoverageEnvelope(BaseModel):
    home_grid: str | None
    home_lat: float | None
    home_lon: float | None
    hours: int
    band_filter: str | None
    bin_size_deg: int
    total_reports: int
    bins: list[CoveragePoint]


@router.get("/stats/coverage-envelope", response_model=CoverageEnvelope)
async def coverage_envelope(
    orch: Orchestrator = Depends(get_orchestrator),
    hours: int = Query(default=24, ge=1, le=168),
    band: str | None = Query(default=None, description="z.B. '20m', None = alle Bänder"),
    bin_size: int = Query(default=5, ge=1, le=30, description="Azimut-Bin-Größe in Grad"),
) -> CoverageEnvelope:
    """Aggregiere PSK-Reporter who-heard-me Reports in Azimut-Bins.

    Pro Bin: die am weitesten entfernte Station die uns gehört hat in
    diesem Zeitfenster. Glätten via 3-Bin-Rolling-Max gegen einzelne
    Far-DX-Spikes. Resultat sind Polygon-Eckpunkte für die Coverage-
    Envelope auf der Karte.

    band=None aggregiert über alle Bänder (eher generelle Coverage),
    band='20m' filtert auf das eine Band (zeigt wo's am offensten ist).
    """
    cfg = get_config()
    my_call = cfg.operator.callsign
    my_grid = cfg.operator.default_locator or ""
    if not my_grid:
        return CoverageEnvelope(
            home_grid=None, home_lat=None, home_lon=None,
            hours=hours, band_filter=band, bin_size_deg=bin_size,
            total_reports=0, bins=[],
        )
    try:
        home_lat, home_lon = locator_to_latlon(my_grid)
    except ValueError:
        return CoverageEnvelope(
            home_grid=my_grid, home_lat=None, home_lon=None,
            hours=hours, band_filter=band, bin_size_deg=bin_size,
            total_reports=0, bins=[],
        )

    # PSK-Reporter via Integration-Container abfragen.
    psk = orch.integrations.psk_reporter
    if psk is None or not psk.enabled:
        return CoverageEnvelope(
            home_grid=my_grid, home_lat=home_lat, home_lon=home_lon,
            hours=hours, band_filter=band, bin_size_deg=bin_size,
            total_reports=0, bins=[],
        )
    reports = await psk.who_heard_me(my_call, hours=hours)

    n_bins = 360 // bin_size
    # Pro Bin: Liste von (distance, age_h).
    bin_data: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    now_utc = datetime.now(UTC)
    total = 0
    for r in reports:
        if band is not None and r.band != band:
            continue
        if not r.rx_grid:
            continue
        try:
            rx_lat, rx_lon = locator_to_latlon(r.rx_grid)
        except ValueError:
            continue
        d_km, bearing = great_circle(home_lat, home_lon, rx_lat, rx_lon)
        if d_km < 50:
            # Zu nah — meist Receiver direkt am Standort (eigene RX-Site,
            # WebSDR vorm Haus) und würde das Bin durch geringe Distanz
            # verzerren. Sub-50 km verwirren nur.
            continue
        bin_idx = int(bearing // bin_size) % n_bins
        # PSK-Reporter liefert .received_at teils ohne tzinfo — normalisieren.
        rcv = r.received_at if r.received_at.tzinfo else r.received_at.replace(tzinfo=UTC)
        age_h = (now_utc - rcv).total_seconds() / 3600.0
        bin_data[bin_idx].append((d_km, age_h))
        total += 1

    # Pro Bin: max(distance), count, min(age_h).
    raw_bins: list[tuple[float, int, float]] = []
    for entries in bin_data:
        if not entries:
            raw_bins.append((0.0, 0, 999.0))
        else:
            max_d = max(e[0] for e in entries)
            min_age = min(e[1] for e in entries)
            raw_bins.append((max_d, len(entries), min_age))

    # 3-Bin-Rolling-Max gegen einzelne Spikes. Bins ohne Reports bleiben
    # bei 0 — die werden im Frontend als "kein Coverage in dieser
    # Richtung" gerendert (kein Polygon-Punkt).
    out_bins: list[CoveragePoint] = []
    for i in range(n_bins):
        d_self, count_self, age_self = raw_bins[i]
        if count_self == 0:
            continue
        d_prev = raw_bins[(i - 1) % n_bins][0]
        d_next = raw_bins[(i + 1) % n_bins][0]
        d_smoothed = max(d_self, d_prev, d_next)
        bin_az = i * bin_size + bin_size // 2  # Mitte des Bins
        far_lat, far_lon = destination_point(home_lat, home_lon, bin_az, d_smoothed)
        out_bins.append(CoveragePoint(
            azimuth_deg=bin_az,
            distance_km=int(round(d_smoothed)),
            report_count=count_self,
            # PSK-Reporter-Server-Zeit kann minimal voraus sein → clamp 0.
            latest_age_h=round(max(0.0, age_self), 1),
            far_lat=round(far_lat, 4),
            far_lon=round(far_lon, 4),
        ))
    # Sortierung nach Azimut sicherstellen damit das Polygon in Reihen-
    # folge gezeichnet wird (kein Zickzack quer durch die Karte).
    out_bins.sort(key=lambda b: b.azimuth_deg)

    return CoverageEnvelope(
        home_grid=my_grid,
        home_lat=round(home_lat, 4),
        home_lon=round(home_lon, 4),
        hours=hours,
        band_filter=band,
        bin_size_deg=bin_size,
        total_reports=total,
        bins=out_bins,
    )
