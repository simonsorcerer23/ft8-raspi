"""Async TCP client for ``gpsd``.

Wire-compatible with both:
* the production ``gpsd`` daemon (default port 2947)
* the in-process :class:`tests.mocks.mock_gpsd.MockGpsd`

We open the socket, send ``?WATCH={"enable":true,"json":true};``, and
then read line-delimited JSON. We expose a typed snapshot of the latest
TPV+SKY plus a coroutine to poll on demand.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

WATCH_CMD = b'?WATCH={"enable":true,"json":true};\n'


@dataclass(slots=True)
class GpsSnapshot:
    """Latest known fix + sky data."""

    mode: int = 0  # 0=no, 2=2D, 3=3D
    lat: float | None = None
    lon: float | None = None
    alt: float | None = None
    time_iso: str | None = None
    sats_seen: int = 0
    sats_used: int = 0


class GpsdClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 2947, timeout: float = 2.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.snapshot = GpsSnapshot()

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self.timeout
        )
        # Drain the connect-time banner (VERSION + DEVICES)
        for _ in range(2):
            try:
                await asyncio.wait_for(self._reader.readline(), timeout=self.timeout)
            except TimeoutError:
                break
        # Subscribe
        self._writer.write(WATCH_CMD)
        await self._writer.drain()

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = self._writer = None

    async def run_forever(self) -> None:
        """Consume gpsd's stream, update :attr:`snapshot` in place.

        Designed to be spawned as a background task. Reconnects on
        connection drop after a short cool-off.
        """
        while True:
            try:
                if self._reader is None or self._writer is None or self._writer.is_closing():
                    await self.connect()
                assert self._reader is not None
                line = await self._reader.readline()
                if not line:
                    raise ConnectionError("gpsd EOF")
                self._ingest(line)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("gpsd stream error: %s; reconnecting in 5s", exc)
                await self.close()
                await asyncio.sleep(5)

    def _ingest(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        cls = msg.get("class")
        if cls == "TPV":
            self.snapshot.mode = int(msg.get("mode", 0))
            if "lat" in msg:
                self.snapshot.lat = float(msg["lat"])
            if "lon" in msg:
                self.snapshot.lon = float(msg["lon"])
            if "alt" in msg:
                self.snapshot.alt = float(msg["alt"])
            if "time" in msg:
                self.snapshot.time_iso = msg["time"]
        elif cls == "SKY":
            # gpsd sendet pro Sekunde ZWEI SKY-Messages: die erste mit
            # vollem satellites-Array + nSat/uSat, die zweite nur mit
            # DOP-Werten (ohne satellites). Vorher haben wir blind
            # sats_seen=len([])=0 gesetzt sobald die zweite kam — daher
            # zeigte das UI "0/0" obwohl der Empfaenger 12 von 23 Sats
            # nutzte (Sebastian 2026-05-24).
            # Bevorzugt die kompakten nSat/uSat-Felder; nur als Fallback
            # zaehlen wir das satellites-Array. Wenn beides fehlt, lassen
            # wir den alten Wert stehen.
            if "nSat" in msg or "uSat" in msg:
                self.snapshot.sats_seen = int(msg.get("nSat", self.snapshot.sats_seen))
                self.snapshot.sats_used = int(msg.get("uSat", self.snapshot.sats_used))
            elif "satellites" in msg:
                sats = msg.get("satellites") or []
                self.snapshot.sats_seen = len(sats)
                self.snapshot.sats_used = sum(1 for s in sats if s.get("used"))
