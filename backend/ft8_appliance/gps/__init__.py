"""GPS — gpsd TCP client."""

from __future__ import annotations

from .gpsd_client import GpsdClient, GpsSnapshot

__all__ = ["GpsSnapshot", "GpsdClient"]
