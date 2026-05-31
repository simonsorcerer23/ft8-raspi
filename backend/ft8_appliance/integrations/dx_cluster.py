"""DX Cluster client — light read-only Telnet consumer.

DX clusters (e.g. dxc.k1ttt.net:7373) stream spots as text lines like::

    DX de DL3XYZ:    14076.0  W1AW         FT8           1530Z

We parse those into a structured spot ring buffer that the Map can
overlay. Connection is best-effort — if the cluster's down we just
return empty results.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

log = logging.getLogger(__name__)

DEFAULT_HOST = "dxc.k1ttt.net"
DEFAULT_PORT = 7373


@dataclass(frozen=True, slots=True)
class DxSpot:
    ts: datetime
    spotter: str
    freq_hz: int
    spotted: str
    comment: str

    @property
    def band(self) -> str | None:
        """Bandname aus der Frequenz (z.B. '20m'). Der Orchestrator griff
        auf spot.band zu, das es nie gab (mypy-attr-defined-Audit) → Crash
        sobald DX-Cluster-Spots verarbeitet wurden. Property statt Feld,
        damit der frozen/slots-Dataclass unangetastet bleibt."""
        from ..util.bandplan import band_from_freq_hz
        return band_from_freq_hz(self.freq_hz)


_SPOT_RE = re.compile(
    r"DX de\s+(?P<spotter>[A-Z0-9/]+)[:\s]*"
    r"(?P<freq>\d+\.\d+)\s+(?P<call>[A-Z0-9/]+)\s+(?P<comment>.*?)$",
    re.IGNORECASE,
)


class DxClusterClient:
    """Connect, login with callsign, ingest spots into a ring buffer."""

    name = "dx_cluster"

    def __init__(
        self,
        *,
        callsign: str,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        enabled: bool = False,
        max_spots: int = 200,
    ) -> None:
        self.enabled = enabled
        self.callsign = callsign
        self.host = host
        self.port = port
        self.spots: deque[DxSpot] = deque(maxlen=max_spots)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self.enabled:
            return
        self._task = asyncio.create_task(self._run(), name="dx-cluster")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def recent(self, *, ft8_only: bool = True, minutes: int = 30) -> list[DxSpot]:
        cutoff_ts = datetime.now(UTC).timestamp() - minutes * 60
        out = [s for s in self.spots if s.ts.timestamp() >= cutoff_ts]
        if ft8_only:
            out = [s for s in out if "FT8" in (s.comment or "").upper()]
        return out

    async def _run(self) -> None:
        backoff = 5.0
        while True:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), timeout=10.0
                )
                writer.write(f"{self.callsign}\r\n".encode())
                await writer.drain()
                log.info("DX-Cluster connected to %s:%d", self.host, self.port)
                backoff = 5.0
                while True:
                    line = await reader.readline()
                    if not line:
                        raise ConnectionError("EOF")
                    self._ingest(line.decode("ascii", errors="replace"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("DX-Cluster error: %s; reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 60.0)

    def _ingest(self, line: str) -> None:
        m = _SPOT_RE.search(line)
        if not m:
            return
        try:
            freq_hz = int(float(m.group("freq")) * 1000)
        except ValueError:
            return
        spot = DxSpot(
            ts=datetime.now(UTC),
            spotter=m.group("spotter").upper(),
            freq_hz=freq_hz,
            spotted=m.group("call").upper(),
            comment=m.group("comment").strip(),
        )
        self.spots.append(spot)
