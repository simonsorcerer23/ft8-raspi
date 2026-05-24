"""Blitzortung.org lightning data — minimal interface.

The live data comes via a websocket (wss://ws1.blitzortung.org/) that
streams strike events. For the MVP we expose just the integration
*shape* — health, enable/disable, the "is there a strike within N km"
query — backed by an in-memory ring of recent strikes that a wiring
layer pushes into us. Real websocket plumbing lands when we connect it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import asin, cos, radians, sin, sqrt

from .base import Integration

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True, slots=True)
class Strike:
    ts: datetime
    lat: float
    lon: float


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(radians, a)
    lat2, lon2 = map(radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(h))


class BlitzortungClient(Integration):
    name = "blitzortung"

    def __init__(
        self,
        *,
        enabled: bool = True,
        alarm_radius_km: int = 30,
        retention_minutes: int = 30,
    ) -> None:
        super().__init__(enabled=enabled, base_url=None, timeout=5.0, cache_ttl_s=0.0)
        self.alarm_radius_km = alarm_radius_km
        self.retention = timedelta(minutes=retention_minutes)
        self._strikes: deque[Strike] = deque(maxlen=10_000)

    def ingest(self, strike: Strike) -> None:
        """Called by the websocket consumer once we wire it up."""
        if not self.enabled:
            return
        self._prune()
        self._strikes.append(strike)

    def nearest_strike_km(self, here: tuple[float, float]) -> float | None:
        if not self._strikes:
            return None
        self._prune()
        if not self._strikes:
            return None
        return min(haversine_km(here, (s.lat, s.lon)) for s in self._strikes)

    def is_storm_nearby(self, here: tuple[float, float]) -> bool:
        d = self.nearest_strike_km(here)
        return d is not None and d <= self.alarm_radius_km

    def _prune(self) -> None:
        cutoff = datetime.now(UTC) - self.retention
        while self._strikes and self._strikes[0].ts < cutoff:
            self._strikes.popleft()
