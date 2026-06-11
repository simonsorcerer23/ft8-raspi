"""UTC-aligned FT8 slot clock.

The orchestrator's main loop wants to wake up on every 15-second
boundary (xx:00, xx:15, xx:30, xx:45) and know precisely when that
moment was. This module wraps that timing into a tiny iterator:

    async for slot in SlotClock():
        # slot.utc_start is a UTC datetime exactly at the boundary
        # slot.posix is the floating-point timestamp
        # slot.index counts slots since process start
        ...

Implementation uses :func:`asyncio.sleep` against ``time.time()``. The
absolute time itself comes from chrony/GPS — we trust the system clock.

For tests we expose :class:`FakeSlotClock` that yields immediately so
the orchestrator can be smoke-tested without burning 15 seconds per
iteration.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

SLOT_SECONDS = 15.0  # FT8 default; FT4 uses 7.5s via the clock's slot_seconds arg.
FT4_SLOT_SECONDS = 7.5


@dataclass(frozen=True, slots=True)
class SlotTick:
    index: int  # 0, 1, 2, ... since clock start
    posix: float  # wall-clock POSIX timestamp of the slot start
    utc_start: datetime  # convenience: tz-aware UTC
    slot_seconds: float = 15.0  # FT8=15, FT4=7.5 — needed for slot_of_minute math

    @property
    def slot_of_minute(self) -> int:
        """0..(60/slot_seconds-1) — for FT8 (15s) that's 4 slots, for FT4 (7.5s) 8 slots."""
        return int((self.posix % 60) // self.slot_seconds)

    @property
    def is_even(self) -> bool:
        """True for the "even" TX windows. FT8: slots 0/2; FT4: slots 0/2/4/6."""
        return self.slot_of_minute % 2 == 0


class SlotClock:
    """Real-time UTC-aligned slot iterator.

    Default slot length is 15s (FT8). Pass ``slot_seconds=7.5`` to drive
    FT4. The clock aligns to ``posix % slot_seconds == 0`` boundaries —
    UTC-anchored as long as the system clock is sync'd (chrony/GPS).
    """

    def __init__(self, slot_seconds: float = SLOT_SECONDS) -> None:
        self._index = -1
        self._slot_seconds = float(slot_seconds)

    def set_slot_seconds(self, slot_seconds: float) -> None:
        """Change the slot cadence live (FT8 15 s ↔ FT4 7.5 s).

        The running iterator re-reads ``self._slot_seconds`` on every cycle
        (see ``_iter``), so a hot FT8↔FT4 mode-switch takes effect on the
        next slot boundary — no service restart needed. Sebastian 2026-06-11:
        switching mode in the UI must not require a restart.
        """
        self._slot_seconds = float(slot_seconds)

    def __aiter__(self) -> AsyncIterator[SlotTick]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[SlotTick]:
        while True:
            # Re-read each cycle (NOT captured once) so set_slot_seconds()
            # can retune a live, already-running clock on the fly.
            slot = self._slot_seconds
            now = time.time()
            wait = slot - (now % slot)
            # round-down very small waits — happens if we returned slightly
            # before the next boundary
            if wait < 0.005:
                wait += slot
            await asyncio.sleep(wait)
            self._index += 1
            posix = time.time()
            yield SlotTick(
                index=self._index,
                posix=posix,
                utc_start=datetime.fromtimestamp(posix, tz=UTC),
                slot_seconds=slot,
            )


class FakeSlotClock:
    """Deterministic, no-sleep clock for tests.

    Yields *count* ticks back-to-back. ``start_posix`` controls the first
    tick's timestamp; ticks step by ``slot_seconds`` (default 15).
    """

    def __init__(
        self,
        count: int = 1,
        start_posix: float = 1_700_000_000.0,
        slot_seconds: float = SLOT_SECONDS,
    ) -> None:
        self.count = count
        self.start = start_posix
        self.slot_seconds = float(slot_seconds)

    def __aiter__(self) -> AsyncIterator[SlotTick]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[SlotTick]:
        for i in range(self.count):
            posix = self.start + i * self.slot_seconds
            yield SlotTick(
                index=i,
                posix=posix,
                utc_start=datetime.fromtimestamp(posix, tz=UTC),
                slot_seconds=self.slot_seconds,
            )
            await asyncio.sleep(0)  # yield control
