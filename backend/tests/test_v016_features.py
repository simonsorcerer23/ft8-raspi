"""Tests v0.16.0 — Hour-of-Day-Predictor + Tail-End-PreStage."""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime

import pytest

from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import (
    HUNT_TIERS,
    StateMachine,
    _tier_active_hour,
)
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext


def _hw_ok() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )


def _ctx(**overrides) -> MachineContext:
    c = MachineContext(callsign="DK9XR", my_grid="JN58", band="15m",
                       tail_end_hunter_enabled=True)
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _decode(call_from: str | None, *, call_to: str | None = None,
            message: str | None = None, snr: int = -10, freq: int = 1500,
            band: str = "15m") -> DecodedMsg:
    if message is None:
        if call_to:
            message = f"{call_to} {call_from} RR73"
        else:
            message = f"CQ {call_from}"
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call_from, call_to=call_to, grid=None,
        message=message, snr_db=snr, dt_s=0.1, freq_offset_hz=freq, band=band,
    )


# ---------------------------------------------------------------------------
# Hour-of-Day Tier
# ---------------------------------------------------------------------------


def test_active_hour_tier_no_data_zero():
    """Ohne active_continent_hours-Daten → 0."""
    ctx = _ctx(call_to_continent={"DL5ABC": "EU"})
    assert _tier_active_hour(_decode("DL5ABC"), ctx) == 0


def test_active_hour_tier_no_continent_zero():
    """Wenn wir den Continent des Calls nicht kennen → 0."""
    hour = datetime.now(UTC).hour
    ctx = _ctx(active_continent_hours={("EU", hour)})
    assert _tier_active_hour(_decode("UNKNOWN"), ctx) == 0


def test_active_hour_tier_matches_current_hour():
    """(Continent, jetzige Stunde) in active set → 1."""
    hour = datetime.now(UTC).hour
    ctx = _ctx(
        call_to_continent={"DL5ABC": "EU"},
        active_continent_hours={("EU", hour)},
    )
    assert _tier_active_hour(_decode("DL5ABC"), ctx) == 1


def test_active_hour_tier_other_continent_zero():
    """EU-Stunde im Set, aber Call ist OC → 0."""
    hour = datetime.now(UTC).hour
    ctx = _ctx(
        call_to_continent={"VK3XYZ": "OC"},
        active_continent_hours={("EU", hour)},
    )
    assert _tier_active_hour(_decode("VK3XYZ"), ctx) == 0


def test_active_hour_tier_in_registry():
    assert "active_hour" in HUNT_TIERS


# ---------------------------------------------------------------------------
# Tail-End PreStage (R-Report-Detection)
# ---------------------------------------------------------------------------


def test_pre_stage_on_r_report():
    """X sendet 'Y X R-12' an Y → X wird pre-staged."""
    sm = StateMachine(ctx=_ctx())
    r_report = _decode("DL3QR", call_to="OK1AB",
                       message="OK1AB DL3QR R-12", snr=-8, freq=1234)
    sm.on_decodes(_hw_ok(), [r_report])
    assert "DL3QR" in sm.ctx.pre_staged_tail_ends
    meta = sm.ctx.pre_staged_tail_ends["DL3QR"]
    assert meta["snr_db"] == -8
    assert meta["freq_offset_hz"] == 1234


def test_pre_stage_ignores_r_report_to_us():
    """R-Report an UNS → KEIN Pre-Stage (unser Partner, nicht Fremder)."""
    sm = StateMachine(ctx=_ctx())
    r_report = _decode("DL3QR", call_to="DK9XR",
                       message="DK9XR DL3QR R-12")
    sm.on_decodes(_hw_ok(), [r_report])
    assert "DL3QR" not in sm.ctx.pre_staged_tail_ends


def test_pre_stage_overrides_recent_cq_filter():
    """Wenn Op kurz vorher CQ rief (5min-Filter) ABER wir ihn pre-staged
    haben (R-Report gesehen), uebersteuert Pre-Stage und Candidate
    wird gebildet."""
    sm = StateMachine(ctx=_ctx())
    # 1. Slot: er ruft CQ
    sm.on_decodes(_hw_ok(), [_decode("DL3QR", message="CQ DL3QR JO62")])
    assert "DL3QR" in sm.ctx.tail_end_recent_cq
    # 2. Slot: er sendet R-Report an OK1AB (pre-stage)
    sm.on_decodes(_hw_ok(),
                  [_decode("DL3QR", call_to="OK1AB",
                           message="OK1AB DL3QR R-12")])
    assert "DL3QR" in sm.ctx.pre_staged_tail_ends
    # 3. Slot: er sendet RR73 an OK1AB. OHNE Pre-Stage haette der 5-min-
    # Filter ihn rausgefiltert. MIT Pre-Stage wird er Candidate.
    sm.on_decodes(_hw_ok(),
                  [_decode("DL3QR", call_to="OK1AB",
                           message="OK1AB DL3QR RR73")])
    assert "DL3QR" in sm.ctx.tail_end_candidates


def test_pre_stage_expires_with_prune():
    """pre_staged_tail_ends wird via _prune_tail_end_state expiriert."""
    sm = StateMachine(ctx=_ctx())
    sm.on_decodes(_hw_ok(),
                  [_decode("DL3QR", call_to="OK1AB",
                           message="OK1AB DL3QR R-12")])
    assert "DL3QR" in sm.ctx.pre_staged_tail_ends
    # Expiry kuenstlich nach vorne
    sm.ctx.pre_staged_tail_ends["DL3QR"]["expiry"] = (
        datetime.now(UTC).timestamp() - 1.0
    )
    sm.on_slot_tick(_hw_ok())
    assert "DL3QR" not in sm.ctx.pre_staged_tail_ends


# ---------------------------------------------------------------------------
# Registry + Default
# ---------------------------------------------------------------------------


def test_default_hunt_priority_includes_active_hour():
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig()
    assert "active_hour" in cfg.hunt_priority
    # active_hour kommt nach band_open
    assert cfg.hunt_priority.index("band_open") < cfg.hunt_priority.index("active_hour")


def test_migration_includes_v016_tiers():
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig(hunt_priority=[])
    assert "active_hour" in cfg.hunt_priority
    assert len(cfg.hunt_priority) >= 17
