"""Async TCP client for Hamlib's ``rigctld``.

Wire-compatible with both:
* the production ``rigctld -m 3085 -r /dev/serial/by-id/usb-Icom_Inc._IC-705-…``
* the in-process :class:`tests.mocks.mock_rigctld.MockRigctld`

The protocol is line-oriented text. Each command is one line; responses
either start with the requested value (for getters) or ``RPRT N``
(error code, 0 = OK). We expose just the commands the state machine /
guards need today.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


class RigctldError(RuntimeError):
    pass


@dataclass(slots=True)
class RigSnapshot:
    """All the values we typically poll every status tick.

    Each field is optional — when Hamlib (or the rig) doesn't support
    the level we leave it ``None`` rather than crash the snapshot.
    """

    freq_hz: int | None = None
    mode: str | None = None
    bandwidth_hz: int | None = None
    ptt: bool | None = None
    swr: float | None = None
    rfpower_norm: float | None = None    # TX power *setting* (0..1)
    rfpower_meter: float | None = None   # actual output (TX only)
    s_meter_db: int | None = None        # S-meter, RX only, in dB rel
    alc: float | None = None             # 0..1 — should be 0 for FT8
    af_gain: float | None = None         # speaker/headphone (0..1)
    rf_gain: float | None = None         # RX RF gain (0..1)
    nr_level: float | None = None        # noise reduction strength (0..1)
    preamp_on: bool | None = None
    att_on: bool | None = None
    nb_on: bool | None = None            # noise blanker
    agc_mode: str | None = None          # OFF | SLOW | MEDIUM | FAST | AUTO
    vfo: str | None = None               # VFOA | VFOB | MEM …
    split_on: bool | None = None
    battery_v: float | None = None
    internal_temp_c: float | None = None


class RigctldClient:
    """One client = one persistent TCP connection.

    Re-connects on demand if the socket drops. Safe to use from a single
    asyncio task; concurrent use needs its own lock.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 4532, timeout: float = 2.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ socket lifecycle
    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self.timeout
        )

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = self._writer = None

    async def _ensure_connected(self) -> None:
        if self._writer is None or self._writer.is_closing():
            await self.connect()

    async def _send(self, line: str) -> str:
        await self._ensure_connected()
        assert self._writer is not None and self._reader is not None
        self._writer.write((line + "\n").encode("ascii"))
        await self._writer.drain()
        try:
            resp = await asyncio.wait_for(self._reader.readline(), timeout=self.timeout)
        except (asyncio.TimeoutError, ConnectionError):
            # Critical: if we time out mid-response, the rigctld reply may
            # arrive later and pollute the buffer — every subsequent _send
            # would then read the WRONG line (response misalignment).
            # Drop the socket so the next _send rebuilds a clean stream.
            await self.close()
            raise
        return resp.decode("ascii", errors="replace").rstrip("\r\n")

    # ------------------------------------------------------------------ getters
    async def get_freq(self) -> int:
        async with self._lock:
            line = await self._send("f")
        return int(line)

    async def get_mode(self) -> tuple[str, int]:
        async with self._lock:
            mode = await self._send("m")
            assert self._reader is not None
            try:
                width = await asyncio.wait_for(self._reader.readline(), timeout=self.timeout)
            except (asyncio.TimeoutError, ConnectionError):
                # Same hazard as in _send: a late width-line on the socket
                # would shift every subsequent read. Drop the socket.
                await self.close()
                raise
        return mode, int(width.decode().rstrip())

    async def get_ptt(self) -> bool:
        async with self._lock:
            line = await self._send("t")
        return line.strip() == "1"

    async def get_level(self, name: str) -> float:
        async with self._lock:
            line = await self._send(f"l {name}")
        return float(line)

    async def get_battery_v(self) -> float | None:
        """Read the IC-705 internal battery voltage via Hamlib level VOLTSEN.

        Returns None if the rig doesn't expose it (e.g. external power).
        """
        try:
            return await self.get_level("VOLTSEN")
        except Exception:
            return None

    async def snapshot(self) -> RigSnapshot:
        """One round of all values we routinely care about.

        Each value is fetched individually but inside the same TCP session
        for cheaper latency. Errors are absorbed — the snapshot returns
        whatever it could collect, the rest stays ``None``.
        """
        snap = RigSnapshot()
        # freq
        try:
            snap.freq_hz = await self.get_freq()
        except Exception as exc:
            log.debug("get_freq failed: %s", exc)
        # mode + filter bandwidth
        try:
            mode, bw = await self.get_mode()
            snap.mode = mode
            snap.bandwidth_hz = bw
        except Exception as exc:
            log.debug("get_mode failed: %s", exc)
        # ptt
        try:
            snap.ptt = await self.get_ptt()
        except Exception as exc:
            log.debug("get_ptt failed: %s", exc)
        # swr — rig may not always report; only meaningful during TX
        try:
            snap.swr = await self.get_level("SWR")
        except Exception:
            pass
        # RFPOWER setting
        try:
            snap.rfpower_norm = await self.get_level("RFPOWER")
        except Exception:
            pass
        # Extra levels — wrapped individually so a single missing one
        # doesn't drop the whole snapshot
        for attr, level_name, cast in (
            ("rfpower_meter", "RFPOWER_METER", float),
            ("s_meter_db",    "STRENGTH",     int),
            ("alc",           "ALC",          float),
            ("af_gain",       "AF",           float),
            ("rf_gain",       "RF",           float),
            ("nr_level",      "NR",           float),
        ):
            try:
                setattr(snap, attr, cast(await self.get_level(level_name)))
            except Exception:
                pass
        # On/off functions (Hamlib: ``u <name>`` returns 0/1)
        for attr, func_name in (
            ("preamp_on", "PREAMP"),
            ("att_on",    "ATT"),
            ("nb_on",     "NB"),
        ):
            try:
                setattr(snap, attr, await self.get_func(func_name))
            except Exception:
                pass
        # AGC mode + VFO + Split
        try:
            snap.agc_mode = await self.get_agc_mode()
        except Exception:
            pass
        try:
            snap.vfo = await self.get_vfo()
        except Exception:
            pass
        try:
            snap.split_on = await self.get_split()
        except Exception:
            pass
        try:
            snap.battery_v = await self.get_battery_v()
        except Exception:
            pass
        return snap

    # ------------------------------------------------------------------ extra getters
    async def get_func(self, name: str) -> bool:
        async with self._lock:
            line = await self._send(f"u {name}")
        return line.strip() == "1"

    async def get_agc_mode(self) -> str | None:
        # Hamlib 'l AGC' returns numeric — we map back to strings
        async with self._lock:
            line = await self._send("l AGC")
        try:
            v = int(float(line))
        except ValueError:
            return None
        return {0: "OFF", 1: "SUPERFAST", 2: "FAST",
                3: "MEDIUM", 4: "SLOW", 5: "AUTO"}.get(v)

    async def get_vfo(self) -> str | None:
        async with self._lock:
            line = await self._send("v")
        return line.strip() or None

    async def get_split(self) -> bool:
        # rigctl 's' (get_split_vfo) returns TWO lines: split-state + tx-vfo.
        # If we don't consume the second line, every subsequent _send reads
        # a response shifted by one, leading to freq landing in
        # rfpower_meter, mode getting numeric values, etc.
        async with self._lock:
            line = await self._send("s")
            assert self._reader is not None
            try:
                await asyncio.wait_for(self._reader.readline(), timeout=self.timeout)
            except (asyncio.TimeoutError, ConnectionError):
                await self.close()
                raise
        return line.strip().startswith("1")

    # ------------------------------------------------------------------ setters
    async def set_freq(self, hz: int) -> None:
        async with self._lock:
            resp = await self._send(f"F {hz}")
        _check_rprt(resp)

    async def set_mode(self, mode: str, bandwidth_hz: int = 2700) -> None:
        async with self._lock:
            resp = await self._send(f"M {mode} {bandwidth_hz}")
        _check_rprt(resp)

    async def set_ptt(self, on: bool) -> None:
        async with self._lock:
            resp = await self._send(f"T {1 if on else 0}")
        _check_rprt(resp)

    async def set_rfpower(self, norm: float) -> None:
        norm = max(0.0, min(1.0, norm))
        async with self._lock:
            resp = await self._send(f"L RFPOWER {norm:.3f}")
        _check_rprt(resp)


def _check_rprt(line: str) -> None:
    if not line.startswith("RPRT"):
        raise RigctldError(f"unexpected response: {line!r}")
    parts = line.split()
    if len(parts) >= 2 and parts[1] != "0":
        raise RigctldError(f"command failed: {line}")
