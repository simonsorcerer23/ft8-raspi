"""In-process mock of ``rigctld`` (Hamlib's network rig daemon).

Just enough of the line-based protocol to drive our :class:`RigctldClient`
through happy and unhappy paths in tests. Reference for the protocol is the
``rigctl(1)`` man page and the daemon's source.

Usage::

    async with MockRigctld() as rig:
        rig.set_swr(1.4)
        # ... point your client at rig.port

The mock returns ``RPRT 0`` (OK) for known commands and ``RPRT -11``
(unimplemented) for the rest. State (frequency, mode, PTT, SWR, battery) is
held in memory and can be poked from tests.

Implemented commands (the subset our controller uses):

    f               get_freq        -> frequency in Hz
    F <hz>          set_freq
    m               get_mode        -> "<mode>\\n<width_hz>"
    M <mode> <hz>   set_mode
    t               get_ptt         -> 0/1
    T <0|1>         set_ptt
    l SWR           get_level SWR   -> float
    l RFPOWER       get_level RFPOWER -> float (0..1 normalised)
    L RFPOWER <v>   set_level RFPOWER
    q               quit / disconnect
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class RigState:
    freq_hz: int = 14_074_000  # 20m FT8
    mode: str = "USB"
    bandwidth_hz: int = 2700
    ptt: bool = False
    swr: float = 1.2
    rfpower_norm: float = 1.0    # 0..1
    rfpower_meter: float = 0.0   # actual TX power (only meaningful while ptt)
    s_meter_db: int = -73        # S6 noise floor by default
    alc: float = 0.0
    af_gain: float = 0.5
    rf_gain: float = 1.0
    nr_level: float = 0.0
    preamp_on: bool = False
    att_on: bool = False
    nb_on: bool = False
    agc_int: int = 3             # MEDIUM
    vfo: str = "VFOA"
    split_on: bool = False
    battery_v: float = 13.2
    internal_temp_c: float = 38.0


class MockRigctld:
    """Asyncio TCP server speaking the rigctld text protocol."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._requested_port = port
        self._server: asyncio.base_events.Server | None = None
        self.state = RigState()
        self.command_log: list[str] = field(default_factory=list)  # type: ignore[assignment]
        self.command_log = []

    # ------------------------------------------------------------------ lifecycle
    async def __aenter__(self) -> MockRigctld:
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._requested_port
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    # ------------------------------------------------------------------ test API
    def set_swr(self, swr: float) -> None:
        self.state.swr = swr

    def set_battery(self, volts: float) -> None:
        self.state.battery_v = volts

    # ------------------------------------------------------------------ wire
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            cmd = line.decode("ascii", errors="replace").rstrip("\r\n")
            self.command_log.append(cmd)
            response = self._dispatch(cmd)
            writer.write(response.encode("ascii"))
            await writer.drain()
            if cmd in ("q", "Q"):
                break
        writer.close()
        with contextlib_suppress(ConnectionResetError):  # noqa: F821 - injected below
            await writer.wait_closed()

    def _dispatch(self, cmd: str) -> str:
        parts = cmd.split()
        if not parts:
            return "RPRT -1\n"
        head, *args = parts
        try:
            return self._COMMANDS[head](self, args)  # type: ignore[no-any-return]
        except KeyError:
            return "RPRT -11\n"  # not implemented

    # ------------------------------------------------------------------ commands
    def _get_freq(self, _: list[str]) -> str:
        return f"{self.state.freq_hz}\n"

    def _set_freq(self, args: list[str]) -> str:
        self.state.freq_hz = int(float(args[0]))
        return "RPRT 0\n"

    def _get_mode(self, _: list[str]) -> str:
        return f"{self.state.mode}\n{self.state.bandwidth_hz}\n"

    def _set_mode(self, args: list[str]) -> str:
        self.state.mode = args[0]
        if len(args) > 1:
            self.state.bandwidth_hz = int(args[1])
        return "RPRT 0\n"

    def _get_ptt(self, _: list[str]) -> str:
        return f"{1 if self.state.ptt else 0}\n"

    def _set_ptt(self, args: list[str]) -> str:
        self.state.ptt = args[0] not in ("0", "false", "False")
        return "RPRT 0\n"

    def _get_level(self, args: list[str]) -> str:
        if not args:
            return "RPRT -1\n"
        name = args[0].upper()
        mapping = {
            "SWR": self.state.swr,
            "RFPOWER": self.state.rfpower_norm,
            "RFPOWER_METER": self.state.rfpower_meter,
            "STRENGTH": self.state.s_meter_db,
            "ALC": self.state.alc,
            "AF": self.state.af_gain,
            "RF": self.state.rf_gain,
            "NR": self.state.nr_level,
            "AGC": float(self.state.agc_int),
            "VOLTSEN": self.state.battery_v,
        }
        if name in mapping:
            return f"{mapping[name]}\n"
        return "RPRT -11\n"

    def _get_func(self, args: list[str]) -> str:
        if not args:
            return "RPRT -1\n"
        name = args[0].upper()
        m = {"PREAMP": self.state.preamp_on, "ATT": self.state.att_on,
             "NB": self.state.nb_on}
        if name in m:
            return f"{1 if m[name] else 0}\n"
        return "RPRT -11\n"

    def _get_vfo(self, _: list[str]) -> str:
        return f"{self.state.vfo}\n"

    def _get_split(self, _: list[str]) -> str:
        return f"{1 if self.state.split_on else 0} {self.state.vfo}\n"

    def _set_level(self, args: list[str]) -> str:
        if len(args) < 2:
            return "RPRT -1\n"
        name, value = args[0].upper(), float(args[1])
        if name == "RFPOWER":
            self.state.rfpower_norm = max(0.0, min(1.0, value))
            return "RPRT 0\n"
        return "RPRT -11\n"

    def _quit(self, _: list[str]) -> str:
        return "RPRT 0\n"

    _COMMANDS = {
        "f": _get_freq,
        "F": _set_freq,
        "m": _get_mode,
        "M": _set_mode,
        "t": _get_ptt,
        "T": _set_ptt,
        "l": _get_level,
        "L": _set_level,
        "u": _get_func,
        "v": _get_vfo,
        "s": _get_split,
        "q": _quit,
        "Q": _quit,
    }


# tiny shim so we don't pull in contextlib at module top
import contextlib  # noqa: E402

contextlib_suppress = contextlib.suppress
