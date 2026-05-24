"""In-process mock of ``gpsd``.

gpsd speaks line-delimited JSON over TCP (default port 2947). Each line
is an object whose ``class`` field discriminates the message type. We
implement the subset the controller needs:

    VERSION   sent on connect
    DEVICES   sent on connect
    WATCH     received from client, then echoed back, then we stream TPV/SKY
    TPV       Time-Position-Velocity (the fix)
    SKY       satellites in view / used

The mock can be advanced in tests via :meth:`set_fix` / :meth:`emit_tpv`.

Reference: https://gpsd.gitlab.io/gpsd/gpsd_json.html
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class GpsFix:
    mode: int = 3  # 0=no fix, 2=2D, 3=3D
    lat: float = 49.4639  # JN58td (Bavaria-ish)
    lon: float = 11.0997
    alt: float = 320.0
    speed: float = 0.0
    track: float = 0.0
    sats_seen: int = 11
    sats_used: int = 8
    time: str | None = None  # ISO UTC; defaults to "now" if None


class MockGpsd:
    """Asyncio TCP server speaking gpsd's JSON dialect."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._requested_port = port
        self._server: asyncio.base_events.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self.fix = GpsFix()

    # ------------------------------------------------------------------ lifecycle
    async def __aenter__(self) -> MockGpsd:
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._requested_port
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        for w in self._writers:
            w.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    # ------------------------------------------------------------------ test API
    def set_fix(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self.fix, k, v)

    async def emit_tpv(self) -> None:
        """Push the current fix as a TPV message to all connected clients."""
        msg = self._tpv_message()
        await self._broadcast(msg)

    async def emit_sky(self) -> None:
        msg = self._sky_message()
        await self._broadcast(msg)

    # ------------------------------------------------------------------ wire
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writers.append(writer)
        await self._send(writer, {"class": "VERSION", "release": "3.20", "proto_major": 3})
        await self._send(
            writer,
            {
                "class": "DEVICES",
                "devices": [{"path": "/dev/ttyACM0", "driver": "u-blox", "activated": "now"}],
            },
        )
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                # We treat any input as a ?WATCH or ?POLL; respond with a fix.
                text = line.decode("ascii", errors="replace").strip()
                if text.startswith("?WATCH"):
                    await self._send(
                        writer, {"class": "WATCH", "enable": True, "json": True}
                    )
                if text.startswith("?POLL") or text.startswith("?WATCH"):
                    await self._send(writer, self._tpv_message())
                    await self._send(writer, self._sky_message())
        finally:
            if writer in self._writers:
                self._writers.remove(writer)
            writer.close()

    async def _broadcast(self, msg: dict) -> None:
        dead: list[asyncio.StreamWriter] = []
        for w in list(self._writers):
            try:
                await self._send(w, msg)
            except (ConnectionResetError, BrokenPipeError):
                dead.append(w)
        for w in dead:
            self._writers.remove(w)

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, msg: dict) -> None:
        writer.write((json.dumps(msg) + "\n").encode("ascii"))
        await writer.drain()

    def _tpv_message(self) -> dict:
        return {
            "class": "TPV",
            "device": "/dev/ttyACM0",
            "mode": self.fix.mode,
            "time": self.fix.time or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "lat": self.fix.lat,
            "lon": self.fix.lon,
            "alt": self.fix.alt,
            "speed": self.fix.speed,
            "track": self.fix.track,
        }

    def _sky_message(self) -> dict:
        sats = [
            {"PRN": i + 1, "used": i < self.fix.sats_used}
            for i in range(self.fix.sats_seen)
        ]
        return {"class": "SKY", "device": "/dev/ttyACM0", "satellites": sats}
