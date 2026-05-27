"""Tests v0.18.0 — TX-Audio-Freq Smart-Hop + Frequency-Reputation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import (
    DecodedMsg, MachineContext, QsoContext, State,
)


def _hw_ok() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )


# ---------------------------------------------------------------------------
# Freq-Hop vor Bail
# ---------------------------------------------------------------------------


def test_hop_audio_freq_low_side_goes_up():
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    assert sm._hop_audio_freq(800) == 1000


def test_hop_audio_freq_high_side_goes_down():
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    assert sm._hop_audio_freq(2000) == 1800


def test_hop_audio_freq_clamped_low():
    """Sehr niedrige Freq → wird ins safe-range geclampt."""
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    result = sm._hop_audio_freq(200)
    assert result >= 350  # min + 50


def test_hop_audio_freq_clamped_high():
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    result = sm._hop_audio_freq(2400)
    assert result <= 2350  # max - 50


def test_freq_hop_before_bail_on_max_resends():
    """Bei max_resends + freq_hopped_once=False → Hop statt Bail."""
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    sm.state = State.QSO_RESPOND
    sm.qso = QsoContext(
        their_call="DL5ABC", freq_offset_hz=1500,
        cq_resends=2,  # bereits am Limit
    )
    sm.qso_max_cq_resends = 2
    # Partner ruft erneut CQ
    decodes = [DecodedMsg(
        ts=datetime.now(UTC), call_from="DL5ABC", call_to=None, grid=None,
        message="CQ DL5ABC", snr_db=-10, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )]
    sm.on_decodes(_hw_ok(), decodes)
    # Sollte gehoppt haben, nicht gebailt
    assert sm.state is State.QSO_RESPOND
    assert sm.qso is not None
    assert sm.qso.freq_hopped_once is True
    assert sm.qso.freq_offset_hz != 1500


def test_freq_hop_bail_after_second_max_resends():
    """Wenn schon gehoppt UND max_resends erneut erreicht → Bail."""
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    sm.state = State.QSO_RESPOND
    sm.qso = QsoContext(
        their_call="DL5ABC", freq_offset_hz=1300,
        cq_resends=2,
        freq_hopped_once=True,  # schon einmal gehoppt
    )
    sm.qso_max_cq_resends = 2
    decodes = [DecodedMsg(
        ts=datetime.now(UTC), call_from="DL5ABC", call_to=None, grid=None,
        message="CQ DL5ABC", snr_db=-10, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )]
    sm.on_decodes(_hw_ok(), decodes)
    # Sollte gebailt sein
    assert sm.state is State.IDLE


# ---------------------------------------------------------------------------
# Freq-Reputation im Smart-CQ-Picker
# ---------------------------------------------------------------------------


def test_freq_reputation_biases_picker():
    """Bei gleichem Belegungs-Histogramm waehlt der Picker den Bin mit
    besserer Reputation (höhere success/attempts-Ratio)."""
    sm = StateMachine(ctx=MachineContext(
        callsign="DK9XR", my_grid="JN58", band="15m",
        freq_reputation={
            ("15m", 1500): (10, 8),   # 80% success
            ("15m", 1700): (10, 1),   # 10% success
        },
    ))
    # Last decodes leer → Histogramm-Filter ergibt fuer alle Kandidaten 0
    sm.last_decodes = []
    # Erste 5 Calls: ohne Decodes faellt smart-picker auf Rotation zurueck.
    # Dafuer brauchen wir Decodes — auch wenn sie unsichtbar sind.
    sm.last_decodes = [DecodedMsg(
        ts=datetime.now(UTC), call_from="X", call_to=None, grid=None,
        message="CQ X", snr_db=-10, dt_s=0.1,
        freq_offset_hz=2200, band="15m",
    )]
    # Erste 3 picks aus Top-3 — auf jeden Fall sollte 1500 dabei sein
    # (Wilson/Laplace-Smoothing: 1500 = 9/12 = 0.75, 1700 = 2/12 = 0.17).
    picks = set()
    for _ in range(3):
        picks.add(sm._next_cq_freq_hz())
    # 1500 sollte unter den Top-3 sein wegen besserer Reputation
    assert 1500 in picks


def test_freq_reputation_unknown_bin_neutral():
    """Wenn ein Bin keine Reputation hat, gilt Laplace-Smoothing (50/50).
    Picker crashed nicht."""
    sm = StateMachine(ctx=MachineContext(
        callsign="DK9XR", my_grid="JN58", band="15m",
        freq_reputation={},
    ))
    sm.last_decodes = [DecodedMsg(
        ts=datetime.now(UTC), call_from="X", call_to=None, grid=None,
        message="CQ X", snr_db=-10, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )]
    # Sollte einen sinnvollen Wert returnen, nicht crashen
    result = sm._next_cq_freq_hz()
    assert 300 <= result <= 2400
