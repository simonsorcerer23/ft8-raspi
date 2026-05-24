"""External-service integration clients.

All HTTP clients follow the resilience pattern from
``architecture.md`` §6.6: aggressive timeouts, TTL cache, circuit
breaker, graceful degradation.
"""

from __future__ import annotations

from .base import (
    AsyncTTLCache,
    CircuitBreaker,
    CircuitOpenError,
    Integration,
    IntegrationHealth,
)
from .blitzortung import BlitzortungClient, Strike, haversine_km
from .cty_dat import CtyDat, DxccEntity, DxccLookupResult
from .dx_cluster import DxClusterClient, DxSpot
from .hamqsl import HamQslClient, SolarData
from .hamqth import HamQthClient, HamQthRecord
from .ntfy import NtfyClient
from .psk_reporter import HeardReport, PskReporterClient
from .qrz import QrzClient, QrzRecord

__all__ = [
    "AsyncTTLCache",
    "BlitzortungClient",
    "CircuitBreaker",
    "CircuitOpenError",
    "CtyDat",
    "DxccEntity",
    "DxccLookupResult",
    "HamQslClient",
    "HamQthClient",
    "HamQthRecord",
    "HeardReport",
    "Integration",
    "IntegrationHealth",
    "NtfyClient",
    "PskReporterClient",
    "QrzClient",
    "QrzRecord",
    "SolarData",
    "Strike",
    "haversine_km",
]
