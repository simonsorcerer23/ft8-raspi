"""Fremde Tastung am Rig ist nicht unser Burst (2026-09-06).

Live 20:53: am IC-7300 wurde von Hand abgestimmt (PTT, AM/USB, Leistungs-
knopf, SWR 2,58). Die ALC-Regelung nahm die Tastung als eigene Bursts und
schnitt den Gain 0,45 -> 0,05; der SWR-Runaway schaltete PTT weg; und der
Guard sperrte spaeter mit dem alten ALC-Wert 92 %.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.rig.rigctld_client import RigSnapshot
from ft8_appliance.statemachine.states import State


def _orch():
    from ft8_appliance.config import AntennaConfig, AppConfig, BandConfig, OperatingConfig, OperatorConfig
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

    return Orchestrator(config=cfg, rig=rig, gps=gps, decode_source=nd, slot_clock=FakeSlotClock(count=0))


def test_manual_ptt_does_not_feed_the_alc_loop() -> None:
    o = _orch()
    o._audio_gain = 0.45
    o._last_rig = RigSnapshot(freq_hz=21_074_000, ptt=True, alc=0.92)
    o._apply_alc_closed_loop()             # PTT an, aber kein eigener Burst
    o._last_rig = RigSnapshot(freq_hz=21_074_000, ptt=False, alc=0.0)
    o._apply_alc_closed_loop()             # "Burst-Ende"
    assert o._audio_gain == 0.45
    assert o._last_alc_pct is None


def test_own_burst_still_feeds_the_alc_loop() -> None:
    o = _orch()
    o._audio_gain = 0.45
    o._tx_burst_active = True
    o._last_rig = RigSnapshot(freq_hz=21_074_000, ptt=True, alc=0.92)
    o._apply_alc_closed_loop()             # PTT-Flanke: Samples zuruecksetzen
    o._apply_alc_closed_loop()             # erstes Sample
    o._tx_burst_active = False
    o._last_rig = RigSnapshot(freq_hz=21_074_000, ptt=False, alc=0.0)
    o._apply_alc_closed_loop()
    assert o._last_alc_pct == 92
    assert o._audio_gain < 0.45            # Watchdog hat geschnitten


@pytest.mark.asyncio
async def test_manual_tuning_with_high_swr_does_not_cut_or_lock() -> None:
    o = _orch()
    o._last_rig = RigSnapshot(freq_hz=21_074_000, ptt=True, swr=2.6)
    o._check_swr_warn()
    assert o.state_machine.state is not State.TX_LOCKED
    assert o._swr_runaway_active is False
