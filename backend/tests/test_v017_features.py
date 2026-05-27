"""Tests v0.17.0 — Buddy-Seen-Tier + Adaptive QSO-Cooldown + Watchlist-Hint."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ft8_appliance.statemachine.machine import (
    HUNT_TIERS,
    _tier_buddy_seen,
)
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext


def _ctx(**overrides) -> MachineContext:
    c = MachineContext(callsign="DK9XR", my_grid="JN58", band="15m")
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _d(call_from: str | None) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call_from, call_to=None, grid=None,
        message=f"CQ {call_from}", snr_db=-10, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )


# ---------------------------------------------------------------------------
# Buddy-Seen Tier
# ---------------------------------------------------------------------------


def test_buddy_seen_not_worked_returns_0():
    """Call nicht in worked-Set → kein Buddy."""
    ctx = _ctx(worked=set(), worked_call_band=set())
    assert _tier_buddy_seen(_d("DL5ABC"), ctx) == 0


def test_buddy_seen_worked_on_other_band_returns_1():
    """Call in worked, aber NICHT auf diesem Band → Buddy."""
    ctx = _ctx(
        band="15m",
        worked={"DL5ABC"},
        worked_call_band={("DL5ABC", "20m")},  # auf 20m gearbeitet, nicht 15m
    )
    assert _tier_buddy_seen(_d("DL5ABC"), ctx) == 1


def test_buddy_seen_worked_on_this_band_returns_0():
    """Bereits auf DIESEM Band → kein Boost (kein neuer Band-Punkt)."""
    ctx = _ctx(
        band="15m",
        worked={"DL5ABC"},
        worked_call_band={("DL5ABC", "15m")},
    )
    assert _tier_buddy_seen(_d("DL5ABC"), ctx) == 0


def test_buddy_seen_case_insensitive():
    ctx = _ctx(
        band="15m",
        worked={"DL5ABC"},
        worked_call_band={("DL5ABC", "20m")},
    )
    assert _tier_buddy_seen(_d("dl5abc"), ctx) == 1


def test_buddy_seen_no_call_from_returns_0():
    ctx = _ctx(worked={"DL5ABC"}, worked_call_band=set())
    d = _d(None)
    d.call_from = None
    assert _tier_buddy_seen(d, ctx) == 0


def test_buddy_seen_in_registry():
    assert "buddy_seen" in HUNT_TIERS


def test_default_hunt_priority_includes_buddy_seen():
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig()
    assert "buddy_seen" in cfg.hunt_priority
    # buddy_seen kommt nach active_hour
    assert cfg.hunt_priority.index("active_hour") < cfg.hunt_priority.index("buddy_seen")


def test_migration_includes_buddy_seen():
    from ft8_appliance.config.models import OperatingConfig
    old = ["marine_psk", "snr"]
    cfg = OperatingConfig(hunt_priority=old)
    assert "buddy_seen" in cfg.hunt_priority


def test_migration_len_is_18():
    """v0.17.0: 18 known Tiers (+ buddy_seen)."""
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig(hunt_priority=[])
    assert len(cfg.hunt_priority) == 18
