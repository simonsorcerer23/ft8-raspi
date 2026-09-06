"""Rig zuruecksetzen (2026-09-06): PKTUSB, 2700 Hz, Dial — per Button oder
automatisch aus der Tamper-Erkennung. Anlass: Dad am IC-7300 mit USB und
350-Hz-Filter."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.rig.rigctld_client import RigSnapshot


def _orch(auto: bool = False):
    from ft8_appliance.config import AntennaConfig, AppConfig, BandConfig, OperatingConfig, OperatorConfig
    from ft8_appliance.runtime import FakeSlotClock, Orchestrator

    cfg = AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[BandConfig(name="20m", freq_khz=14074, antenna="w")],
        antennas=[AntennaConfig(name="w", bands=["20m"])],
        operating=OperatingConfig(rig_auto_restore=auto),
    )
    rig = AsyncMock(); rig.snapshot = AsyncMock(return_value=RigSnapshot(freq_hz=14_074_000)); rig.close = AsyncMock()
    gps = AsyncMock(); gps.snapshot = SimpleNamespace(mode=3, lat=0, lon=0, ts=None, lock_for_min=None, satellites_used=None); gps.close = AsyncMock()

    async def nd(tick):
        return []

    o = Orchestrator(config=cfg, rig=rig, gps=gps, decode_source=nd, slot_clock=FakeSlotClock(count=0))
    o._last_rig = RigSnapshot(freq_hz=14_074_000, mode="USB", bandwidth_hz=350)
    return o


@pytest.mark.asyncio
async def test_button_restores_mode_filter_and_leaves_power_alone() -> None:
    o = _orch()
    r = await o.handle_restore_rig_settings("button")
    assert r["ok"] is True
    o.rig.set_mode.assert_awaited_with("PKTUSB", 2700)
    o.rig.set_power.assert_not_awaited() if hasattr(o.rig, "set_power") else None


@pytest.mark.asyncio
async def test_restore_refuses_during_own_burst() -> None:
    o = _orch()
    o._tx_burst_active = True
    r = await o.handle_restore_rig_settings("button")
    assert r["ok"] is False
    o.rig.set_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_restore_only_with_flag_and_throttled() -> None:
    o = _orch(auto=False)
    o._schedule_rig_restore("mode-tamper")
    assert o._rig_restore_last_at == 0.0
    o = _orch(auto=True)
    o._schedule_rig_restore("mode-tamper")
    first = o._rig_restore_last_at
    assert first > 0.0
    o._schedule_rig_restore("filter-tamper")     # innerhalb 15 s: nichts Neues
    assert o._rig_restore_last_at == first


def test_route_exists() -> None:
    from fastapi.testclient import TestClient

    from ft8_appliance.web.app import create_app

    with TestClient(create_app()) as c:
        r = c.post("/api/control/restore-rig")
        assert r.status_code in (200, 401, 403, 503)  # 503: Orchestrator im Test-App nicht gestartet
