"""Antenne: Bandwechsel ohne Abstimmung (2026-09-07). Vorbereitung fuer
Raymonds Multiband-Antenne — solange der Dipol haengt, bleibt der
Autopilot auf dem Band des Rigs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from ft8_appliance.config import AntennaConfig, AppConfig, BandConfig, OperatingConfig, OperatorConfig
from ft8_appliance.rig.rigctld_client import RigSnapshot
from ft8_appliance.runtime import FakeSlotClock, Orchestrator


def _orch(auto: bool):
    cfg = AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td", license_class="A"),
        bands=[BandConfig(name="20m", freq_khz=14074, antenna="w"), BandConfig(name="15m", freq_khz=21074, antenna="w")],
        antennas=[AntennaConfig(name="w", bands=["20m", "15m"], auto_band_switch=auto)],
        operating=OperatingConfig(autopilot_allowed_bands=["20m", "15m"]),
    )
    rig = AsyncMock(); rig.snapshot = AsyncMock(return_value=RigSnapshot(freq_hz=14_074_000)); rig.close = AsyncMock()
    gps = AsyncMock(); gps.snapshot = SimpleNamespace(mode=3, lat=0, lon=0, ts=None, lock_for_min=None, satellites_used=None); gps.close = AsyncMock()

    async def nd(tick):
        return []

    o = Orchestrator(config=cfg, rig=rig, gps=gps, decode_source=nd, slot_clock=FakeSlotClock(count=0))
    o._active_antenna = "w"
    o._last_rig = RigSnapshot(freq_hz=14_074_000)
    return o


def test_default_is_no_automatic_band_change() -> None:
    assert AntennaConfig(name="d", bands=["20m"]).auto_band_switch is False


def test_autopilot_stays_on_current_band_without_the_flag() -> None:
    o = _orch(auto=False)
    assert o._autopilot_allowed_bands() == ["20m"]


def test_autopilot_may_switch_with_the_flag() -> None:
    o = _orch(auto=True)
    assert o._autopilot_allowed_bands() == ["20m", "15m"]
