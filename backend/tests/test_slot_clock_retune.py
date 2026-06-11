"""Regression: FT8↔FT4 slot-cadence retune must work on a LIVE running clock.

Sebastian 2026-06-11: switching mode in the UI required a service restart —
the running SlotClock iterator had captured slot_seconds once, so the decoder
flipped to FT4 (7.5 s windows) while the clock kept firing every 15 s →
"short by samples, zero-padded". The fix makes _iter re-read _slot_seconds each
cycle so set_slot_seconds() retunes an already-running iterator.
"""
from __future__ import annotations

import pytest

import ft8_appliance.runtime.slot_clock as sc


@pytest.mark.asyncio
async def test_running_slotclock_picks_up_retune(monkeypatch) -> None:
    clk = sc.SlotClock(15.0)
    assert clk._slot_seconds == 15.0

    # Fake wall clock + sleep so we don't burn real seconds: every awaited
    # sleep just advances our virtual clock by the requested amount.
    fake = {"t": 1_000_000.0}
    monkeypatch.setattr(sc.time, "time", lambda: fake["t"])

    async def _fake_sleep(s: float) -> None:
        fake["t"] += s

    monkeypatch.setattr(sc.asyncio, "sleep", _fake_sleep)

    it = clk.__aiter__()
    t1 = await it.__anext__()
    assert t1.slot_seconds == 15.0  # FT8 cadence

    # Hot mode-switch FT8 → FT4 on the SAME running iterator.
    clk.set_slot_seconds(7.5)
    t2 = await it.__anext__()
    assert t2.slot_seconds == 7.5, "running clock must pick up the live retune"

    # And back FT4 → FT8.
    clk.set_slot_seconds(15.0)
    t3 = await it.__anext__()
    assert t3.slot_seconds == 15.0
