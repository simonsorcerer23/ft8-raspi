"""ADIF-Log + Heard-Stations + Decodes endpoints.

* GET /api/log         — paginated QSO list with filters
* GET /api/heard       — recently heard stations (for the map)
* GET /api/decodes     — last N raw decodes
* GET /api/map         — combined worked + heard for map rendering
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from ...db import session_scope
from ...db.models import Blacklist, Decode, Heard, Qso
from ...integrations.flags import flag_for_call
from ...runtime import Orchestrator
from ..deps import get_orchestrator

router = APIRouter()


# ---------------------------------------------------------------------------
class QsoOut(BaseModel):
    id: int
    call: str
    band: str
    freq_hz: int
    mode: str
    rst_sent: int | None
    rst_rcvd: int | None
    grid_rcvd: str | None
    qso_start: datetime
    qso_end: datetime
    my_grid: str
    my_power_w: int | None
    swr_avg: float | None
    # Sebastian-Request 2026-05-24 (v0.3.0): Unicode-Flag-Emoji aus dem
    # Callsign via cty.dat → ISO2 → Flag. Leerer String wenn unbekannt
    # oder Sonder-DXCC ohne Flag-Mapping (ITU/UN/Antarktis).
    flag: str = ""


class LogResponse(BaseModel):
    total: int
    page: int
    page_size: int
    qsos: list[QsoOut]


SORTABLE_QSO_COLUMNS = {
    "qso_start": Qso.qso_start,
    "call": Qso.call,
    "band": Qso.band,
    "freq_hz": Qso.freq_hz,
    "rst_sent": Qso.rst_sent,
    "rst_rcvd": Qso.rst_rcvd,
    "grid_rcvd": Qso.grid_rcvd,
    "my_power_w": Qso.my_power_w,
    "swr_avg": Qso.swr_avg,
}


@router.get("/log", response_model=LogResponse)
async def get_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    call_filter: str | None = Query(None, description="substring match on callsign"),
    prefix: str | None = Query(
        None,
        description='DXCC-style prefix filter — matches "X*", "X/*", "*/X*"'
    ),
    band: str | None = Query(None),
    grid_filter: str | None = Query(None, description="substring on remote grid"),
    sort_by: str = Query("qso_start"),
    sort_dir: Literal["asc", "desc"] = Query("desc"),
    since_days: int | None = Query(None, ge=1, le=3650),
    min_snr_rcvd: int | None = Query(None),
    orch: Orchestrator = Depends(get_orchestrator),
) -> LogResponse:
    """Paginated QSO list with multi-axis filters and sortable columns.

    Multi-Operator (Sebastian 2026-05-23): standardmaessig nur QSOs des
    aktiven Operators. Andere User koennen ihre Logs nur sehen wenn sie
    auf ihr Profil wechseln.
    """
    my_call = orch.config.operator.callsign
    async with session_scope() as s:
        stmt = select(Qso).where(Qso.user_callsign == my_call)

        if call_filter:
            stmt = stmt.where(Qso.call.ilike(f"%{call_filter.upper()}%"))
        if prefix:
            p = prefix.upper().strip()
            # Match plain prefix at start ("9A1ABC"), portable-prefix form
            # ("9A/DK9XR") and trailing-prefix form ("DK9XR/9A"). Covers
            # the three ways callsigns get a DXCC tag in real ham logs.
            stmt = stmt.where(
                Qso.call.ilike(f"{p}%")
                | Qso.call.ilike(f"{p}/%")
                | Qso.call.ilike(f"%/{p}")
                | Qso.call.ilike(f"%/{p}/%")
            )
        if band:
            stmt = stmt.where(Qso.band == band)
        if grid_filter:
            stmt = stmt.where(Qso.grid_rcvd.ilike(f"{grid_filter.upper()}%"))
        if since_days is not None:
            cutoff = datetime.now(UTC) - timedelta(days=since_days)
            stmt = stmt.where(Qso.qso_start >= cutoff)
        if min_snr_rcvd is not None:
            stmt = stmt.where(Qso.rst_rcvd >= min_snr_rcvd)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await s.execute(count_stmt)).scalar_one()

        sort_col = SORTABLE_QSO_COLUMNS.get(sort_by, Qso.qso_start)
        stmt = stmt.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)
        rows = list((await s.execute(stmt)).scalars())

    cty = orch.integrations.cty
    qsos_out: list[QsoOut] = []
    for q in rows:
        item = QsoOut.model_validate(q, from_attributes=True)
        item.flag = flag_for_call(item.call, cty)
        qsos_out.append(item)
    return LogResponse(
        total=total,
        page=page,
        page_size=page_size,
        qsos=qsos_out,
    )


# ---------------------------------------------------------------------------
class HeardOut(BaseModel):
    call: str
    last_seen: datetime
    count: int
    grid: str | None
    best_snr: int | None
    flag: str = ""  # Flag-Emoji, siehe QsoOut.flag


class HeardResponse(BaseModel):
    stations: list[HeardOut]


@router.get("/heard", response_model=HeardResponse)
async def get_heard(
    minutes: int = Query(60, ge=1, le=24 * 60, description="time window"),
    orch: Orchestrator = Depends(get_orchestrator),
) -> HeardResponse:
    """Wer hat mich gehoert — nur fuer den aktiven Operator."""
    my_call = orch.config.operator.callsign
    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    async with session_scope() as s:
        stmt = (
            select(Heard)
            .where(Heard.last_seen >= cutoff)
            .where(Heard.user_callsign == my_call)
            .order_by(desc(Heard.last_seen))
        )
        rows = list((await s.execute(stmt)).scalars())
    cty = orch.integrations.cty
    stations = []
    for h in rows:
        item = HeardOut.model_validate(h, from_attributes=True)
        item.flag = flag_for_call(item.call, cty)
        stations.append(item)
    return HeardResponse(stations=stations)


# ---------------------------------------------------------------------------
class DecodeOut(BaseModel):
    id: int
    ts: datetime
    call_from: str | None
    call_to: str | None
    grid: str | None
    message: str
    snr_db: int | None
    dt_s: float | None
    freq_offset_hz: int | None
    band: str | None
    worked_before: bool = False
    blacklisted: bool = False
    # Multi-color highlighting (WSJT-Z style). Each flag drives a
    # separate accent in the UI so the operator can spot calls worth
    # answering at a glance, even with dozens of decodes per slot.
    is_new_dxcc: bool = False
    is_new_grid: bool = False
    is_new_grid_on_band: bool = False
    # Flag-Emoji des Senders (call_from) — Sebastian-Request v0.3.0.
    flag: str = ""


class DecodesResponse(BaseModel):
    decodes: list[DecodeOut]


@router.get("/decodes", response_model=DecodesResponse)
async def get_decodes(
    limit: int = Query(100, ge=1, le=1000),
    orch: Orchestrator = Depends(get_orchestrator),
) -> DecodesResponse:
    async with session_scope() as s:
        rows = list(
            (
                await s.execute(select(Decode).order_by(desc(Decode.ts)).limit(limit))
            ).scalars()
        )
    cty = orch.integrations.cty
    out: list[DecodeOut] = []
    for d in rows:
        item = DecodeOut.model_validate(d, from_attributes=True)
        item.worked_before = orch.is_worked_before(d.call_from)
        item.blacklisted = orch.is_blacklisted(d.call_from)
        item.is_new_dxcc = orch.is_new_dxcc_for(d.call_from)
        item.is_new_grid = orch.is_new_grid(d.grid)
        item.is_new_grid_on_band = orch.is_new_grid_on_band(d.grid, d.band)
        item.flag = flag_for_call(d.call_from, cty)
        out.append(item)
    return DecodesResponse(decodes=out)


# ---------------------------------------------------------------------------
class BlacklistEntry(BaseModel):
    call: str
    added: datetime
    reason: str | None


class BlacklistResponse(BaseModel):
    entries: list[BlacklistEntry]


@router.get("/blacklist", response_model=BlacklistResponse)
async def get_blacklist(
    orch: Orchestrator = Depends(get_orchestrator),
) -> BlacklistResponse:
    """Blacklist des aktiven Operators."""
    my_call = orch.config.operator.callsign
    async with session_scope() as s:
        rows = list(
            (await s.execute(
                select(Blacklist)
                .where(Blacklist.user_callsign == my_call)
                .order_by(desc(Blacklist.added))
            )).scalars()
        )
    return BlacklistResponse(
        entries=[BlacklistEntry.model_validate(r, from_attributes=True) for r in rows]
    )


# ---------------------------------------------------------------------------
class MapMarker(BaseModel):
    call: str
    grid: str
    lat: float
    lon: float
    kind: Literal["worked", "heard", "both"]
    last_seen: datetime | None = None
    last_worked: datetime | None = None
    snr_best: int | None = None
    count: int = 1
    band: str | None = None
    my_lat: float | None = None  # operator's location when worked (for arc)
    my_lon: float | None = None


class MapResponse(BaseModel):
    operator_lat: float | None
    operator_lon: float | None
    markers: list[MapMarker]


def _grid_to_latlon(grid: str | None) -> tuple[float, float] | None:
    """Maidenhead -> centre lat/lon. Accepts 4 or 6 char locators."""
    if not grid or len(grid) < 4:
        return None
    g = grid.upper()
    try:
        lon = (ord(g[0]) - ord("A")) * 20 - 180
        lat = (ord(g[1]) - ord("A")) * 10 - 90
        lon += int(g[2]) * 2
        lat += int(g[3]) * 1
        if len(g) >= 6:
            lon += (ord(g[4]) - ord("A")) * (5 / 60)
            lat += (ord(g[5]) - ord("A")) * (2.5 / 60)
            # centre of subsquare
            lon += 2.5 / 60
            lat += 1.25 / 60
        else:
            # centre of large square
            lon += 1.0
            lat += 0.5
    except (ValueError, IndexError):
        return None
    return lat, lon


@router.get("/map", response_model=MapResponse)
async def get_map(
    mode: Literal["all", "worked", "heard"] = Query("all"),
    minutes_heard: int = Query(60, ge=1, le=24 * 60),
    orch: Orchestrator = Depends(get_orchestrator),
) -> MapResponse:
    """Combined worked + heard map data.

    *mode*=
      - "worked": only stations we've already worked
      - "heard":  only currently-heard stations (within *minutes_heard*)
      - "all":    both, with "both" kind for overlap
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=minutes_heard)
    # Multi-Operator (Sebastian 2026-05-23): Map zeigt nur Daten des
    # gerade aktiven Operators — sonst sah DO3XR die worked/heard-Stationen
    # von DK9XR auf der Karte, was sich falsch anfuehlt.
    my_call = orch.config.operator.callsign
    worked: dict[str, Qso] = {}
    heard: dict[str, Heard] = {}
    async with session_scope() as s:
        if mode in ("all", "worked"):
            rows = list(
                (await s.execute(
                    select(Qso)
                    .where(Qso.user_callsign == my_call)
                    .order_by(desc(Qso.qso_start))
                )).scalars()
            )
            for q in rows:
                if q.grid_rcvd:
                    # keep the most-recent qso per call
                    if q.call not in worked:
                        worked[q.call] = q
        if mode in ("all", "heard"):
            rows = list(
                (
                    await s.execute(
                        select(Heard)
                        .where(Heard.user_callsign == my_call)
                        .where(Heard.last_seen >= cutoff)
                    )
                ).scalars()
            )
            for h in rows:
                if h.grid:
                    heard[h.call] = h

    markers: list[MapMarker] = []
    for call in set(worked) | set(heard):
        q = worked.get(call)
        h = heard.get(call)
        grid = (h.grid if h else None) or (q.grid_rcvd if q else None)
        if grid is None:
            continue
        latlon = _grid_to_latlon(grid)
        if latlon is None:
            continue
        lat, lon = latlon
        if q is not None and h is not None:
            kind = "both"
        elif q is not None:
            kind = "worked"
        else:
            kind = "heard"
        markers.append(
            MapMarker(
                call=call,
                grid=grid,
                lat=lat,
                lon=lon,
                kind=kind,
                last_seen=h.last_seen if h else None,
                last_worked=q.qso_start if q else None,
                snr_best=h.best_snr if h else None,
                count=h.count if h else 1,
                band=(q.band if q else None),
                my_lat=None,  # TODO: per-QSO operator location when we wire GPS-per-QSO
                my_lon=None,
            )
        )

    gps = orch.gps.snapshot
    # Operator location prefers GPS (sky-view) but falls back to the
    # configured default_locator (indoor installs). Without that, the
    # operator pin and great-circle arcs to worked/heard stations stay
    # blank — confusing UX.
    op_lat = gps.lat
    op_lon = gps.lon
    if (op_lat is None or op_lon is None) and orch.config.operator.default_locator:
        ll = _grid_to_latlon(orch.config.operator.default_locator)
        if ll is not None:
            op_lat, op_lon = ll
    return MapResponse(
        operator_lat=op_lat,
        operator_lon=op_lon,
        markers=markers,
    )


# ---------------------------------------------------------------------------
class OperatingLocation(BaseModel):
    lat: float
    lon: float
    qso_count: int
    first_qso: datetime
    last_qso: datetime
    bands: list[str]


class OperatingLocationsResponse(BaseModel):
    locations: list[OperatingLocation]


@router.get("/operating-locations", response_model=OperatingLocationsResponse)
async def operating_locations(
    orch: Orchestrator = Depends(get_orchestrator),
) -> OperatingLocationsResponse:
    """Distinct operator locations from the QSO log (Bonus 12).

    Buckets by ~0.05° (≈5 km) so we don't end up with one pin per QSO
    when stationary. Returned for the Map "Standorte"-Layer.

    Multi-Operator (Sebastian 2026-05-23): nur Standorte des aktiven
    Operators — DO3XR sieht nicht die Urlaubsstandorte von DK9XR.
    """
    my_call = orch.config.operator.callsign
    async with session_scope() as s:
        rows = list(
            (await s.execute(
                select(Qso.my_lat, Qso.my_lon, Qso.qso_start, Qso.band)
                .where(Qso.user_callsign == my_call)
                .where(Qso.my_lat.is_not(None))
                .where(Qso.my_lon.is_not(None))
            )).all()
        )
    buckets: dict[tuple[int, int], dict] = {}
    for lat, lon, ts, band in rows:
        key = (int(lat * 20), int(lon * 20))  # 0.05° grid
        b = buckets.setdefault(key, {
            "lat": lat, "lon": lon, "qso_count": 0,
            "first_qso": ts, "last_qso": ts, "bands": set(),
        })
        b["qso_count"] += 1
        if ts < b["first_qso"]: b["first_qso"] = ts
        if ts > b["last_qso"]:  b["last_qso"] = ts
        if band: b["bands"].add(band)
    return OperatingLocationsResponse(
        locations=[
            OperatingLocation(
                lat=b["lat"], lon=b["lon"], qso_count=b["qso_count"],
                first_qso=b["first_qso"], last_qso=b["last_qso"],
                bands=sorted(b["bands"]),
            )
            for b in buckets.values()
        ]
    )


# ---------------------------------------------------------------------------
class HeardHeatPoint(BaseModel):
    lat: float
    lon: float
    weight: float   # 0..1


class HeardHeatResponse(BaseModel):
    points: list[HeardHeatPoint]


@router.get("/heard/heatmap", response_model=HeardHeatResponse)
async def heard_heatmap(
    minutes: int = Query(360, ge=15, le=24 * 60),
    orch: Orchestrator = Depends(get_orchestrator),
) -> HeardHeatResponse:
    """Heard-stations density for the map heatmap layer.

    Weight = log(count + 1) * recency-factor. The exact scale doesn't
    matter — Leaflet.heat normalises internally.

    Multi-Operator (Sebastian 2026-05-23): Heatmap zeigt nur Heard-Spots
    des aktiven Operators.
    """
    import math

    my_call = orch.config.operator.callsign
    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    async with session_scope() as s:
        rows = list((
            await s.execute(
                select(Heard)
                .where(Heard.user_callsign == my_call)
                .where(Heard.last_seen >= cutoff)
            )
        ).scalars())

    points: list[HeardHeatPoint] = []
    now = datetime.now(UTC)
    for h in rows:
        if h.grid is None or len(h.grid) < 4:
            continue
        ll = _grid_to_latlon(h.grid)
        if ll is None:
            continue
        last_seen = h.last_seen if h.last_seen.tzinfo else h.last_seen.replace(tzinfo=UTC)
        age_h = (now - last_seen).total_seconds() / 3600
        recency = max(0.2, 1.0 - age_h / 24)
        weight = math.log10(h.count + 1) + 0.5
        points.append(HeardHeatPoint(
            lat=ll[0], lon=ll[1], weight=weight * recency,
        ))
    return HeardHeatResponse(points=points)
