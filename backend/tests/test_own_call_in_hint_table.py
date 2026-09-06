"""Eigener Call + QSO-Partner in der Known-Call-Tabelle (2026-09-06)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.decode.ft8_native import lib


def _orch():
    from ft8_appliance.config import AntennaConfig, AppConfig, BandConfig, OperatingConfig, OperatorConfig
    from ft8_appliance.rig.rigctld_client import RigSnapshot
    from ft8_appliance.runtime import FakeSlotClock, Orchestrator

    cfg = AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[BandConfig(name="15m", freq_khz=21074, antenna="w")],
        antennas=[AntennaConfig(name="w", bands=["15m"])],
        operating=OperatingConfig(),
    )
    rig = AsyncMock(); rig.snapshot = AsyncMock(return_value=RigSnapshot(freq_hz=21_074_000)); rig.close = AsyncMock()
    gps = AsyncMock(); gps.snapshot = SimpleNamespace(mode=3, lat=0, lon=0, ts=None, lock_for_min=None, satellites_used=None); gps.close = AsyncMock()

    async def nd(tick):
        return []

    return Orchestrator(config=cfg, rig=rig, gps=gps, decode_source=nd, slot_clock=FakeSlotClock(count=1))


@pytest.mark.asyncio
async def test_own_call_lands_in_the_hint_table_every_slot() -> None:
    import datetime as _dt

    from ft8_appliance.runtime.slot_clock import SlotTick

    o = _orch()
    o.state_machine.ctx.callsign = "DL9TST"
    before = lib.ft8_shim_hash_table_count()
    posix = 1_700_000_015.0
    await o._refresh_hardware_state(SlotTick(index=1, posix=posix, utc_start=_dt.datetime.fromtimestamp(posix, tz=_dt.UTC)))
    n = lib.ft8_shim_hash_table_count()
    assert n >= before + 1
    lib.ft8_shim_hash_table_save(b"DL9TST", 0)   # schon drin -> kein weiterer Eintrag
    assert lib.ft8_shim_hash_table_count() == n
