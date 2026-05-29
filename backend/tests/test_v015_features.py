"""Tests v0.15.0 — Soft-Blacklist (Bail-Reason-aware) + Slot-Parity-Predictor."""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime

import pytest

from ft8_appliance.statemachine.machine import (
    HUNT_TIERS,
    _tier_not_bad_reputation,
    _tier_not_his_tx_slot,
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
# Soft-Blacklist
# ---------------------------------------------------------------------------


def test_reputation_tier_clean_call_returns_1():
    ctx = _ctx(soft_blacklist=set())
    assert _tier_not_bad_reputation(_d("DL5ABC"), ctx) == 1


def test_reputation_tier_blacklisted_returns_0():
    ctx = _ctx(soft_blacklist={"BAD1XYZ"})
    assert _tier_not_bad_reputation(_d("BAD1XYZ"), ctx) == 0


def test_reputation_tier_case_insensitive():
    ctx = _ctx(soft_blacklist={"BAD1XYZ"})
    assert _tier_not_bad_reputation(_d("bad1xyz"), ctx) == 0


def test_reputation_tier_no_call_from_returns_1():
    """Defensive: kein call_from = kein Filter."""
    ctx = _ctx(soft_blacklist={"BAD1XYZ"})
    d = _d(None)
    d.call_from = None
    assert _tier_not_bad_reputation(d, ctx) == 1


def test_reputation_tier_matches_base_call_regardless_of_suffix():
    """v0.27.0 — Reputation laeuft auf dem Basis-Call. Eine als BAD1XYZ
    geblacklistete Station wird auch als BAD1XYZ/P, /MM, /AM erkannt
    (selber Mensch, selbes Verhalten)."""
    ctx = _ctx(soft_blacklist={"BAD1XYZ"})
    assert _tier_not_bad_reputation(_d("BAD1XYZ/P"), ctx) == 0
    assert _tier_not_bad_reputation(_d("BAD1XYZ/MM"), ctx) == 0
    assert _tier_not_bad_reputation(_d("bad1xyz/am"), ctx) == 0
    # Andere Station bleibt unberuehrt
    assert _tier_not_bad_reputation(_d("DL5ABC/P"), ctx) == 1


# ---------------------------------------------------------------------------
# Slot-Parity
# ---------------------------------------------------------------------------


def test_slot_parity_tier_no_info_returns_1():
    """Wenn wir keine Slot-Parity fuer den Op kennen → kein Filter."""
    ctx = _ctx(op_slot_parity={}, current_slot_parity="even")
    assert _tier_not_his_tx_slot(_d("UNKNOWN"), ctx) == 1


def test_slot_parity_tier_opposite_slot_returns_1():
    """Op sendet even, wir sind in odd → er hoert uns (RX)."""
    ctx = _ctx(op_slot_parity={"DL5ABC": "even"}, current_slot_parity="odd")
    assert _tier_not_his_tx_slot(_d("DL5ABC"), ctx) == 1


def test_slot_parity_tier_same_slot_returns_0():
    """Op sendet even, wir sind auch in even → er sendet jetzt, picken
    macht keinen Sinn."""
    ctx = _ctx(op_slot_parity={"DL5ABC": "even"}, current_slot_parity="even")
    assert _tier_not_his_tx_slot(_d("DL5ABC"), ctx) == 0


def test_slot_parity_tier_no_current_slot_returns_1():
    """Wenn ctx.current_slot_parity nicht gesetzt → kein Filter (Bootphase)."""
    ctx = _ctx(op_slot_parity={"DL5ABC": "even"}, current_slot_parity="")
    assert _tier_not_his_tx_slot(_d("DL5ABC"), ctx) == 1


def test_slot_parity_tier_no_call_from_returns_1():
    ctx = _ctx(op_slot_parity={"DL5ABC": "even"}, current_slot_parity="even")
    d = _d(None)
    d.call_from = None
    assert _tier_not_his_tx_slot(d, ctx) == 1


# ---------------------------------------------------------------------------
# HUNT_TIERS Registry + Default-Order
# ---------------------------------------------------------------------------


def test_hunt_tiers_registry_has_v015_entries():
    assert "not_bad_reputation" in HUNT_TIERS
    assert "not_his_tx_slot" in HUNT_TIERS


def test_default_order_v015_filters_first():
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig()
    assert cfg.hunt_priority[0] == "not_bad_reputation"
    assert cfg.hunt_priority[1] == "not_his_tx_slot"


def test_migration_adds_v015_tiers():
    from ft8_appliance.config.models import OperatingConfig
    old = ["marine_psk", "snr"]
    cfg = OperatingConfig(hunt_priority=old)
    assert "not_bad_reputation" in cfg.hunt_priority
    assert "not_his_tx_slot" in cfg.hunt_priority
    assert cfg.hunt_priority[-1] == "snr"


def test_migration_includes_v015_tiers():
    """v0.15.0 hat min. die filter-tiers drin."""
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig(hunt_priority=[])
    assert "not_bad_reputation" in cfg.hunt_priority
    assert "not_his_tx_slot" in cfg.hunt_priority
    assert len(cfg.hunt_priority) >= 16


# ---------------------------------------------------------------------------
# QSO_BAIL Action emission
# ---------------------------------------------------------------------------


def test_bail_emits_qso_bail_action():
    """State-Machine emittiert QSO_BAIL action mit call + reason wenn
    _bail_qso_with_cooldown aufgerufen wird."""
    from ft8_appliance.statemachine.machine import StateMachine
    from ft8_appliance.statemachine.states import State

    sm = StateMachine(ctx=_ctx())
    sm._bail_qso_with_cooldown("DL5ABC", "max_resends")
    actions = sm.drain_actions()
    bail = [a for a in actions if a.kind == "QSO_BAIL"]
    assert len(bail) == 1
    assert bail[0].payload["call"] == "DL5ABC"
    assert bail[0].payload["reason"] == "max_resends"


def test_bail_with_empty_call_no_action():
    """Defensive: leerer call_from emittiert keinen QSO_BAIL."""
    from ft8_appliance.statemachine.machine import StateMachine

    sm = StateMachine(ctx=_ctx())
    sm._bail_qso_with_cooldown("", "max_resends")
    actions = sm.drain_actions()
    bail = [a for a in actions if a.kind == "QSO_BAIL"]
    assert bail == []
