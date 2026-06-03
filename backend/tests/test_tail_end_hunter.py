"""Tests fuer den v0.11.0 Tail-End-Hunter.

Sebastian-Wunsch: wenn Station X ihr QSO mit Y beendet (RR73/73), darf
unser Hunting-Picker X im naechsten Slot direkt anrufen wie nach einem
CQ — etablierte FT8-Praxis die WSJT-X nicht automatisch macht.
"""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime

import pytest

from ft8_appliance.config.models import OperatingConfig
from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import (
    HUNT_TIERS,
    StateMachine,
    TAIL_END_COOLDOWN_S,
    _compute_tier_score,
    _tier_tail_end_target,
)
from ft8_appliance.statemachine.states import (
    DecodedMsg,
    MachineContext,
    State,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hw_ok() -> HardwareState:
    """Hardware-Mock der alle Guards passieren laesst."""
    return HardwareState(
        gps_fix_mode=3,
        time_offset_s=0.05,
        swr=1.2,
        alc_pct=0,
        battery_v=12.0,
        cpu_temp_c=45.0,
        audio_drift_samples=0,
        antenna_covers_band=True,
        chrony_synced=True,
    )


def _ctx(**overrides) -> MachineContext:
    ctx = MachineContext(
        callsign="DK9XR",
        my_grid="JN58",
        band="15m",
        tail_end_hunter_enabled=True,
    )
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


def _decode(
    call_from: str | None,
    *,
    call_to: str | None = None,
    grid: str | None = None,
    message: str | None = None,
    snr: int | None = -10,
    freq: int | None = 1500,
    band: str = "15m",
) -> DecodedMsg:
    if message is None:
        if call_to:
            message = f"{call_to} {call_from} 73"
        else:
            message = f"CQ {call_from} {grid or ''}".strip()
    return DecodedMsg(
        ts=datetime.now(UTC),
        call_from=call_from,
        call_to=call_to,
        grid=grid,
        message=message,
        snr_db=snr,
        dt_s=0.1,
        freq_offset_hz=freq,
        band=band,
    )


def _machine(ctx: MachineContext | None = None) -> StateMachine:
    return StateMachine(ctx=ctx or _ctx())


# ---------------------------------------------------------------------------
# Detection (on_decodes → tail_end_candidates)
# ---------------------------------------------------------------------------


def test_detection_rr73_makes_candidate():
    """X sendet 'Y X RR73' → X wird Candidate."""
    sm = _machine()
    sm.on_decodes(
        _hw_ok(),
        [_decode("DL3QR", call_to="EA4XYZ", message="EA4XYZ DL3QR RR73", snr=-8, freq=1234)],
    )
    assert "DL3QR" in sm.ctx.tail_end_candidates
    meta = sm.ctx.tail_end_candidates["DL3QR"]
    assert meta["snr_db"] == -8
    assert meta["freq_offset_hz"] == 1234
    assert meta["band"] == "15m"


def test_detection_73_makes_candidate():
    sm = _machine()
    sm.on_decodes(_hw_ok(), [_decode("DL3QR", call_to="EA4XYZ", message="EA4XYZ DL3QR 73")])
    assert "DL3QR" in sm.ctx.tail_end_candidates


def test_detection_rrr_makes_candidate():
    sm = _machine()
    sm.on_decodes(_hw_ok(), [_decode("DL3QR", call_to="EA4XYZ", message="EA4XYZ DL3QR RRR")])
    assert "DL3QR" in sm.ctx.tail_end_candidates


def test_detection_skips_closing_to_us():
    """Wenn das Closing an UNS gerichtet ist, ist X unser Partner — kein Tail-End."""
    sm = _machine()
    sm.on_decodes(
        _hw_ok(),
        [_decode("DL3QR", call_to="DK9XR", message="DK9XR DL3QR RR73")],
    )
    assert "DL3QR" not in sm.ctx.tail_end_candidates


def test_detection_disabled_when_toggle_off():
    """Toggle aus → keine candidates, keine recent_cq-Eintraege."""
    ctx = _ctx(tail_end_hunter_enabled=False)
    sm = _machine(ctx)
    sm.on_decodes(
        _hw_ok(),
        [
            _decode("DL3QR", call_to="EA4XYZ", message="EA4XYZ DL3QR RR73"),
            _decode("EA1XX"),  # CQ-Decode
        ],
    )
    assert sm.ctx.tail_end_candidates == {}
    assert sm.ctx.tail_end_recent_cq == {}


def test_detection_skips_freetext():
    """is_freetext-Decodes (Tx5/Tx6 wie '73 GL') werden nicht als Closing gewertet."""
    sm = _machine()
    d = _decode("DL3QR", call_to="EA4XYZ", message="EA4XYZ DL3QR 73")
    d.is_freetext = True
    sm.on_decodes(_hw_ok(), [d])
    assert "DL3QR" not in sm.ctx.tail_end_candidates


def test_detection_skips_if_recent_cq():
    """Wenn X kuerzlich (<5min) selbst CQ rief, kein Tail-End-Boost."""
    sm = _machine()
    # Erst CQ
    sm.on_decodes(_hw_ok(), [_decode("DL3QR", message="CQ DL3QR JN58")])
    assert "DL3QR" in sm.ctx.tail_end_recent_cq
    # Dann Closing im naechsten Slot — sollte NICHT als Candidate erscheinen
    sm.on_decodes(
        _hw_ok(),
        [_decode("DL3QR", call_to="EA4XYZ", message="EA4XYZ DL3QR RR73")],
    )
    assert "DL3QR" not in sm.ctx.tail_end_candidates


def test_detection_expiry_in_slot_tick():
    """Nach 30 s (=2 Slots) expire'd der Candidate via on_slot_tick."""
    sm = _machine()
    sm.on_decodes(_hw_ok(), [_decode("DL3QR", call_to="EA4XYZ", message="EA4XYZ DL3QR RR73")])
    assert "DL3QR" in sm.ctx.tail_end_candidates
    # Expiry kuenstlich in die Vergangenheit zerren
    sm.ctx.tail_end_candidates["DL3QR"]["expiry"] = (
        datetime.now(UTC).timestamp() - 1.0
    )
    sm.on_slot_tick(_hw_ok())
    assert "DL3QR" not in sm.ctx.tail_end_candidates


# ---------------------------------------------------------------------------
# Tier function (24h cooldown)
# ---------------------------------------------------------------------------


def test_tier_returns_1_for_active_candidate():
    ctx = _ctx(tail_end_candidates={"DL3QR": {"expiry": _time.time() + 30}})
    assert _tier_tail_end_target(_decode("DL3QR"), ctx) == 1


def test_tier_returns_0_for_unknown_call():
    ctx = _ctx(tail_end_candidates={"DL3QR": {"expiry": _time.time() + 30}})
    assert _tier_tail_end_target(_decode("EA4XYZ"), ctx) == 0


def test_tier_returns_0_during_24h_cooldown():
    """Wenn wir innerhalb der letzten 24h schon mal per Tail-End gepickt
    haben, Tier=0 — auch wenn Candidate technisch noch aktiv ist."""
    now = _time.time()
    ctx = _ctx(
        tail_end_candidates={"DL3QR": {"expiry": now + 30}},
        tail_end_last_pick={"DL3QR": now - 3600},  # vor 1h
    )
    assert _tier_tail_end_target(_decode("DL3QR"), ctx) == 0


def test_tier_returns_1_after_cooldown_expires():
    """24h+ her → Cooldown vorbei → wieder eligible."""
    now = _time.time()
    ctx = _ctx(
        tail_end_candidates={"DL3QR": {"expiry": now + 30}},
        tail_end_last_pick={"DL3QR": now - (TAIL_END_COOLDOWN_S + 60)},
    )
    assert _tier_tail_end_target(_decode("DL3QR"), ctx) == 1


# ---------------------------------------------------------------------------
# Picker (synthetic injection)
# ---------------------------------------------------------------------------


def test_picker_injects_synthetic_for_candidate():
    """Wenn Candidate NICHT in den echten CQs ist, picker injiziert
    synthetischen Decode und kann ihn picken."""
    ctx = _ctx(
        auto_answer=True,
        tail_end_candidates={
            "DL3QR": {
                "expiry": _time.time() + 30,
                "snr_db": -5,
                "freq_offset_hz": 1500,
                "band": "15m",
                "grid": "JO62",
            }
        },
        hunt_priority=["tail_end_target", "snr"],
    )
    sm = _machine(ctx)
    sm.state = State.IDLE
    # Nur ein echter Decode da — kein CQ von DL3QR. Aber er sollte
    # trotzdem als synthetic gepickt werden.
    sm.on_decodes(_hw_ok(), [_decode("EA1XX", snr=-15)])
    assert sm.state is State.QSO_RESPOND
    assert sm.qso is not None
    assert sm.qso.their_call == "DL3QR"


def test_picker_records_last_pick_when_candidate_wins():
    """Cooldown wird gesetzt sobald Tail-End-Candidate gepickt wird."""
    ctx = _ctx(
        auto_answer=True,
        tail_end_candidates={
            "DL3QR": {
                "expiry": _time.time() + 30,
                "snr_db": -5,
                "freq_offset_hz": 1500,
                "band": "15m",
                "grid": None,
            }
        },
        hunt_priority=["tail_end_target", "snr"],
    )
    sm = _machine(ctx)
    sm.state = State.IDLE
    sm.on_decodes(_hw_ok(), [_decode("EA1XX", snr=-25)])
    assert "DL3QR" in sm.ctx.tail_end_last_pick


def test_picker_no_synthetic_during_24h_cooldown():
    """Regression v0.11.1: nach Tail-End-Pick wird der Call fuer 24h
    NICHT mehr als synthetischer CQ injiziert. Bug 2026-05-27 (UN7GBX):
    Tier-Funktion lieferte 0, aber der synthetische Decode kam mit
    SNR -6 in den Pool und gewann ueber den SNR-Tie-Breaker."""
    now = _time.time()
    ctx = _ctx(
        tail_end_candidates={
            "UN7GBX": {
                "expiry": now + 30,
                "snr_db": -6,
                "freq_offset_hz": 1103,
                "band": "15m",
                "grid": None,
            }
        },
        tail_end_last_pick={"UN7GBX": now - 1800},  # vor 30 min
    )
    sm = _machine(ctx)
    synth = sm._build_synthetic_tail_end_decodes([])
    assert synth == [], "Im 24h-Cooldown darf kein synthetischer Decode entstehen"


def test_picker_synthetic_returns_after_24h():
    now = _time.time()
    ctx = _ctx(
        tail_end_candidates={
            "UN7GBX": {
                "expiry": now + 30,
                "snr_db": -6,
                "freq_offset_hz": 1103,
                "band": "15m",
                "grid": None,
            }
        },
        tail_end_last_pick={"UN7GBX": now - (TAIL_END_COOLDOWN_S + 60)},
    )
    sm = _machine(ctx)
    synth = sm._build_synthetic_tail_end_decodes([])
    assert len(synth) == 1
    assert synth[0].call_from == "UN7GBX"


def test_picker_no_synthetic_if_real_cq_present():
    """Wenn die Candidate-Station im selben Slot bereits echte CQ ruft,
    kein Duplikat — der echte Decode wird verarbeitet."""
    ctx = _ctx(
        auto_answer=True,
        tail_end_candidates={
            "DL3QR": {
                "expiry": _time.time() + 30,
                "snr_db": -5,
                "freq_offset_hz": 1500,
                "band": "15m",
                "grid": None,
            }
        },
        hunt_priority=["tail_end_target", "snr"],
    )
    sm = _machine(ctx)
    real_cq = _decode("DL3QR", snr=-3, freq=1700)
    synth = sm._build_synthetic_tail_end_decodes([real_cq])
    assert synth == []  # kein Duplikat


def test_tier_in_priority_beats_other_tiers():
    """Tier-Priority 'tail_end_target' an erster Stelle → Candidate
    schlaegt new_dxcc."""
    ctx = _ctx(
        tail_end_candidates={
            "DL3QR": {"expiry": _time.time() + 30}
        },
        new_dxcc_calls={"9J2FI"},
        hunt_priority=["tail_end_target", "new_dxcc", "snr"],
    )
    s_tail = _compute_tier_score(_decode("DL3QR", snr=-15), ctx)
    s_dxcc = _compute_tier_score(_decode("9J2FI", snr=-3), ctx)
    assert s_tail > s_dxcc


# ---------------------------------------------------------------------------
# OperatingConfig
# ---------------------------------------------------------------------------


def test_operating_config_tail_end_default_off():
    cfg = OperatingConfig()
    assert cfg.tail_end_hunter_enabled is False


def test_operating_config_tail_end_can_be_enabled():
    cfg = OperatingConfig(tail_end_hunter_enabled=True)
    assert cfg.tail_end_hunter_enabled is True


def test_default_hunt_priority_tail_end_downranked():
    """v0.65.1: tail_end_target runtergestuft (Telemetrie: nur 3% Completion) —
    steht jetzt NACH new_dxcc_psk und direkt vor dem snr-Tie-Breaker (vorletzte
    Position), gewinnt also nur noch als Quasi-Letztmittel."""
    cfg = OperatingConfig()
    assert "tail_end_target" in cfg.hunt_priority
    assert "new_dxcc_psk" in cfg.hunt_priority
    # jetzt NACH new_dxcc_psk (vorher davor)
    assert cfg.hunt_priority.index("tail_end_target") > cfg.hunt_priority.index("new_dxcc_psk")
    # vorletzte Position, direkt vor 'snr'
    assert cfg.hunt_priority[-2] == "tail_end_target"
    assert cfg.hunt_priority[-1] == "snr"


def test_hunt_tiers_registry_contains_tail_end():
    assert "tail_end_target" in HUNT_TIERS


# ---------------------------------------------------------------------------
# v0.12.0 — User-triggered Tail-End (manueller 🎯-Button in UI)
# ---------------------------------------------------------------------------


def _closing(call_from: str, call_to: str = "EA4XYZ", freq: int = 1500,
             snr: int = -10, band: str = "15m") -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC),
        call_from=call_from,
        call_to=call_to,
        grid=None,
        message=f"{call_to} {call_from} RR73",
        snr_db=snr,
        dt_s=0.1,
        freq_offset_hz=freq,
        band=band,
    )


def test_user_tail_end_transitions_to_qso_respond():
    """🎯-Klick → QSO_RESPOND mit call_from des Closings als Partner."""
    sm = _machine()
    sm.state = State.IDLE
    sm.on_user_tail_end(_hw_ok(), _closing("DL3QR", call_to="OK1AB", freq=1234))
    assert sm.state is State.QSO_RESPOND
    assert sm.qso is not None
    assert sm.qso.their_call == "DL3QR"
    assert sm.qso.freq_offset_hz == 1234


def test_user_tail_end_sets_24h_cooldown():
    """Nach manuellem 🎯 muss tail_end_last_pick gesetzt sein damit der
    Auto-Picker nicht 1 Slot spaeter denselben Call wieder pickt."""
    sm = _machine()
    sm.state = State.IDLE
    sm.on_user_tail_end(_hw_ok(), _closing("DL3QR"))
    assert "DL3QR" in sm.ctx.tail_end_last_pick


def test_user_tail_end_ignores_existing_24h_cooldown():
    """Manueller Override: auch wenn 24h-Cooldown laeuft, User darf
    trotzdem manuell anrufen wenn er es bewusst klickt."""
    sm = _machine()
    # Cooldown 30 min alt = noch aktiv
    sm.ctx.tail_end_last_pick["DL3QR"] = _time.time() - 1800
    sm.state = State.IDLE
    sm.on_user_tail_end(_hw_ok(), _closing("DL3QR"))
    assert sm.state is State.QSO_RESPOND
    assert sm.qso.their_call == "DL3QR"


def test_user_tail_end_refuses_closing_addressed_to_us():
    """Wenn das Closing an UNS ging, ist's unser eigener QSO-Partner —
    kein Tail-End-Pickup noetig."""
    sm = _machine()
    sm.state = State.IDLE
    sm.on_user_tail_end(_hw_ok(), _closing("DL3QR", call_to="DK9XR"))
    assert sm.state is State.IDLE


def test_user_tail_end_refuses_closing_from_us():
    """Defensive: wenn unsere eigene RR73 zurueckkommt, kein Self-Reply."""
    sm = _machine()
    sm.state = State.IDLE
    sm.on_user_tail_end(_hw_ok(), _closing("DK9XR", call_to="DL3QR"))
    assert sm.state is State.IDLE


def test_user_tail_end_no_call_from_is_noop():
    sm = _machine()
    sm.state = State.IDLE
    d = _closing("DL3QR")
    d.call_from = None
    sm.on_user_tail_end(_hw_ok(), d)
    assert sm.state is State.IDLE
