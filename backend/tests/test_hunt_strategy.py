"""Antwortstrategie (2026-09-07) aus der Pick-Telemetrie: 381 Picks, 7 %
vollendet; unter -13 dB 3 %, darueber 12 %; 339 Picks mit nur einem
Kandidaten. Drei Hebel: Schwach-Gate mit PSK-Ausnahme, CQ-Fallback,
A/B der Antwortfrequenz, dazu die lernende Kontinent-Prior."""

from __future__ import annotations

import datetime as _dt

from ft8_appliance.runtime.slot_clock import SlotTick
from ft8_appliance.statemachine import DecodedMsg
from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import HUNT_TIERS, StateMachine, _tier_continent_prior
from ft8_appliance.statemachine.states import MachineContext, State


def _d(msg: str, frm: str, hz: int = 1500, snr: int = -8) -> DecodedMsg:
    return DecodedMsg(ts=_dt.datetime.now(_dt.UTC), call_from=frm, call_to=None, grid="JN58",
                      message=msg, snr_db=snr, dt_s=0.1, freq_offset_hz=hz, band="20m")


def _sm(**ctx) -> StateMachine:
    c = MachineContext(callsign="DK9XR", my_grid="JN58ch", auto_answer=True, my_continent="EU", **ctx)
    return StateMachine(ctx=c)


def _tick(i: int) -> SlotTick:
    posix = 1_700_000_000.0 + 15.0 * i
    return SlotTick(index=i, posix=posix, utc_start=_dt.datetime.fromtimestamp(posix, tz=_dt.UTC))


# ---------------------------------------------------------------- Schwach-Gate

def test_weak_targets_need_psk_confirmation() -> None:
    sm = _sm(hunt_weak_requires_psk=True)
    assert sm._pick_hunt_target([_d("CQ K1ABC FN42", "K1ABC", snr=-16)]) is None
    sm.ctx.psk_heard_us = {"K1ABC"}
    assert sm._pick_hunt_target([_d("CQ K1ABC FN42", "K1ABC", snr=-16)]) is not None
    sm2 = _sm(hunt_weak_requires_psk=False)
    assert sm2._pick_hunt_target([_d("CQ K1ABC FN42", "K1ABC", snr=-16)]) is not None
    assert sm._pick_hunt_target([_d("CQ K1ABC FN42", "K1ABC", snr=-12)]) is not None   # ueber der Schwelle


# ---------------------------------------------------------------- A/B Antwortfrequenz

def test_ab_test_alternates_reply_kind_and_records_it() -> None:
    sm = _sm(hunt_reply_ab_test=True)
    crowd = [_d(f"CQ DL{i}AAA JN58", f"DL{i}AAA", hz=1500 + 30 * i) for i in range(1, 6)]
    kinds = []
    for n in range(4):
        sm.state = State.IDLE; sm.qso = None
        target = _d("CQ K1ABC FN42", "K1ABC", hz=1500, snr=-5)
        sm.on_decodes(HardwareState(), crowd + [target]); sm.drain_actions()
        kinds.append(sm.ctx.hunt_attempt_meta["K1ABC"]["reply_kind"])
        sm.ctx.hunt_attempt_meta.clear()
    assert kinds == ["on_freq", "quiet", "on_freq", "quiet"]


# ---------------------------------------------------------------- CQ-Fallback

def test_cq_fallback_starts_after_n_empty_slots_and_yields_to_a_caller() -> None:
    sm = _sm(hunt_cq_fallback=True, hunt_cq_fallback_after_slots=2)
    hw = HardwareState()
    for i in range(2):
        sm.on_decodes(hw, [])                 # kein Rufer
        sm.on_slot_tick(hw, _tick(i))
    assert sm.state is State.CQ_CALLING and sm.ctx.cq_fallback_active
    assert sm.ctx.cq_fallback_starts == 1
    actions = list(sm.drain_actions())
    for i in range(2, 6):                     # Slot-Paritaet: spaetestens im uebernaechsten Tick kommt das CQ
        sm.on_decodes(hw, []); sm.on_slot_tick(hw, _tick(i)); actions += list(sm.drain_actions())
    assert any(a.kind == "TX_MESSAGE" and "CQ" in str(a.payload) for a in actions)
    assert sm.state is State.CQ_CALLING
    # Ein brauchbarer Rufer beendet den Fallback: antworten statt weiter CQ
    sm.on_decodes(hw, [_d("CQ K1ABC FN42", "K1ABC", snr=-5)])
    assert sm.state is State.QSO_RESPOND and sm.qso is not None and sm.qso.their_call == "K1ABC"
    assert sm.ctx.cq_fallback_active is False


def test_cq_fallback_can_be_switched_off() -> None:
    sm = _sm(hunt_cq_fallback=False)
    hw = HardwareState()
    for i in range(5):
        sm.on_decodes(hw, []); sm.on_slot_tick(hw, _tick(i))
    assert sm.state is State.IDLE and sm.ctx.cq_fallback_starts == 0


# ---------------------------------------------------------------- Kontinent-Prior

def test_continent_prior_tier() -> None:
    ctx = MachineContext(callsign="DK9XR", my_grid="JN58ch")
    ctx.call_to_continent = {"W1AW": "NA", "DL1AAA": "EU", "JA1XYZ": "AS"}
    assert _tier_continent_prior(_d("CQ W1AW FN31", "W1AW"), ctx) == 1          # keine Daten: neutral
    ctx.continent_success = {"EU": 0.14, "NA": 0.05}; ctx.continent_success_overall = 0.07
    assert _tier_continent_prior(_d("CQ DL1AAA JN58", "DL1AAA"), ctx) == 1
    assert _tier_continent_prior(_d("CQ W1AW FN31", "W1AW"), ctx) == 0
    assert _tier_continent_prior(_d("CQ JA1XYZ PM95", "JA1XYZ"), ctx) == 1      # zu wenig Daten fuer AS
    assert "continent_prior" in HUNT_TIERS


def test_cq_fallback_pauses_after_max_unanswered_cqs() -> None:
    sm = _sm(hunt_cq_fallback=True, hunt_cq_fallback_after_slots=1, hunt_cq_fallback_max_cqs=3,
             hunt_cq_fallback_pause_min=10, cq_tx_slot_parity="any")
    hw = HardwareState()
    sm.on_decodes(hw, []); sm.on_slot_tick(hw, _tick(0))
    assert sm.state is State.CQ_CALLING
    i = 1
    while sm.state is State.CQ_CALLING and i < 20:
        sm.on_decodes(hw, []); sm.on_slot_tick(hw, _tick(i)); i += 1
    assert sm.state is State.IDLE and sm.ctx.cq_fallback_active is False
    assert sm.ctx.cq_fallback_paused_until > _tick(i).posix
    # waehrend der Pause kein neuer Fallback
    for j in range(i, i + 4):
        sm.on_decodes(hw, []); sm.on_slot_tick(hw, _tick(j))
    assert sm.state is State.IDLE
    # nach der Pause wieder
    late = SlotTick(index=99, posix=sm.ctx.cq_fallback_paused_until + 15, utc_start=_dt.datetime.now(_dt.UTC))
    sm.on_decodes(hw, []); sm.on_slot_tick(hw, late)
    assert sm.state is State.CQ_CALLING


def test_update_safe_in_status_during_fallback_cq() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from ft8_appliance.config import AntennaConfig, AppConfig, BandConfig, OperatingConfig, OperatorConfig
    from ft8_appliance.rig.rigctld_client import RigSnapshot
    from ft8_appliance.runtime import FakeSlotClock, Orchestrator

    cfg = AppConfig(operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
                    bands=[BandConfig(name="20m", freq_khz=14074, antenna="w")],
                    antennas=[AntennaConfig(name="w", bands=["20m"])], operating=OperatingConfig())
    rig = AsyncMock(); rig.snapshot = AsyncMock(return_value=RigSnapshot(freq_hz=14_074_000)); rig.close = AsyncMock()
    gps = AsyncMock(); gps.snapshot = SimpleNamespace(mode=3, lat=0, lon=0, ts=None, lock_for_min=None, satellites_used=None); gps.close = AsyncMock()

    async def nd(tick):
        return []

    o = Orchestrator(config=cfg, rig=rig, gps=gps, decode_source=nd, slot_clock=FakeSlotClock(count=0))
    o._rx_audio_dbfs_peak = None; o._rx_audio_dbfs_peak_ts = 0.0
    assert o.status().update_safe is True                      # IDLE
    o.state_machine.state = State.CQ_CALLING
    assert o.status().update_safe is False                     # manueller CQ-Modus
    o.state_machine.ctx.cq_fallback_active = True
    assert o.status().update_safe is True                      # Fallback: unterbrechbar
    o.state_machine.state = State.QSO_RESPOND
    assert o.status().update_safe is False
