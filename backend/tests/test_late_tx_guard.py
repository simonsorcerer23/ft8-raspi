"""Stufe-2-Decodes duerfen nie in einen laufenden Burst senden (2026-09-06).

Live 17:35:49: Stufe 2 fand einen CQ, der Picker antwortete, waehrend der
Slot-Handler noch den Burst an OD5ZZ abwartete (_in_slot_tick blieb True).
Folge: zweiter TX mit "Device or resource busy" und danach PTT aus — mitten
im laufenden Burst. Ausserdem zaehlte der spaete TX als slot-getrieben
(3x -> Decoder-Auto-Fallback auf standard).
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.runtime.slot_clock import SlotTick


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

    o = Orchestrator(config=cfg, rig=rig, gps=gps, decode_source=nd, slot_clock=FakeSlotClock(count=0))
    o._rx_audio_dbfs_peak = None; o._rx_audio_dbfs_peak_ts = 0.0
    return o


def _msg(text, frm, to):
    from ft8_appliance.statemachine import DecodedMsg
    return DecodedMsg(ts=_dt.datetime.now(_dt.UTC), call_from=frm, call_to=to, grid="JN58",
                      message=text, snr_db=-12, dt_s=0.1, freq_offset_hz=1500, band="15m", late=True)


@pytest.mark.asyncio
async def test_no_second_burst_while_one_is_playing() -> None:
    o = _orch()
    played = []
    o.playback = SimpleNamespace(play=lambda pcm: played.append(len(pcm)))
    o.rig.set_ptt = AsyncMock()
    o._tx_burst_active = True                  # Burst laeuft
    o._slot_phase_s = lambda: 0.4              # type: ignore[method-assign]
    await o._do_tx_message({"text": "PP5HR DK9XR JN58", "audio_freq_hz": 1600, "amplitude": 0.4})
    assert played == []
    o.rig.set_ptt.assert_not_called()          # vor allem: kein set_ptt(False) in den fremden Burst


@pytest.mark.asyncio
async def test_late_ingest_counts_as_manual_even_inside_the_slot_handler() -> None:
    o = _orch()
    o._in_slot_tick = True                     # Slot-Handler wartet noch den Burst ab
    o._in_late_ingest = True
    o._slot_phase_s = lambda: 4.6              # type: ignore[method-assign]
    assert o._record_tx_start_offset() is False   # B4: entfaellt
    assert o._consecutive_late_tx == 0            # zaehlt nicht gegen den Decoder
    o._in_late_ingest = False
    o._slot_phase_s = lambda: 0.4              # type: ignore[method-assign]
    assert o._record_tx_start_offset() is True


@pytest.mark.asyncio
async def test_ingest_sets_and_clears_the_late_flag() -> None:
    o = _orch()
    seen = []
    orig = o._drain_actions

    async def spy():
        seen.append(o._in_late_ingest)
        await orig()

    o._drain_actions = spy                     # type: ignore[method-assign]
    posix = 1_700_000_015.0
    await o._ingest_late_decodes([_msg("CQ W1AW FN31", "W1AW", None)],
                                 SlotTick(index=3, posix=posix, utc_start=_dt.datetime.fromtimestamp(posix, tz=_dt.UTC)))
    assert seen == [True] and o._in_late_ingest is False
