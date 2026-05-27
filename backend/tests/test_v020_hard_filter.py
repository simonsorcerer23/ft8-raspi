"""Tests v0.20.4 — Hard-Filter im Picker fuer die drei Filter-Tiers.

Bug (Sebastian-Live-Audit 2026-05-27): EA5KB war in ctx.soft_blacklist,
das Tier `not_bad_reputation` lieferte 0, aber der Picker pickte ihn
trotzdem 2x in 20 min weil er der einzige CQ im Slot war (Tier=0 alleine
reicht nicht — bei 1 Kandidat gewinnt er via SNR-Tie-Breaker).

Fix: Hard-Filter im _pick_hunt_target VOR der Tier-Auswahl — analog
blacklist/skip_worked/recent_until. Gilt fuer:
  - soft_blacklist
  - pile_up_calls
  - op_slot_parity (Call meidet seinen eigenen TX-Slot)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext


def _ctx(**overrides) -> MachineContext:
    c = MachineContext(callsign="DK9XR", my_grid="JN58", band="15m")
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _cq(call: str, snr: int = -10, freq: int = 1500) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to=None, grid=None,
        message=f"CQ {call}", snr_db=snr, dt_s=0.1,
        freq_offset_hz=freq, band="15m",
    )


# ---------------------------------------------------------------------------
# Soft-Blacklist Hard-Filter
# ---------------------------------------------------------------------------


def test_soft_blacklist_filters_out_lone_call():
    """Einzige CQ ist soft-blacklisted → Picker liefert None statt ihn
    via SNR-Tie-Breaker zu picken."""
    ctx = _ctx(soft_blacklist={"EA5KB"})
    sm = StateMachine(ctx=ctx)
    assert sm._pick_hunt_target([_cq("EA5KB", snr=-5)]) is None


def test_soft_blacklist_filters_out_among_others():
    """Soft-blacklisted Call wird auch dann ignoriert wenn andere
    CQs schwaecher sind."""
    ctx = _ctx(soft_blacklist={"EA5KB"})
    sm = StateMachine(ctx=ctx)
    decodes = [_cq("EA5KB", snr=-3), _cq("DL5ABC", snr=-20)]
    winner = sm._pick_hunt_target(decodes)
    assert winner is not None
    assert winner.call_from == "DL5ABC"


def test_soft_blacklist_empty_no_filter():
    """Leeres Set → keine Filterung."""
    ctx = _ctx(soft_blacklist=set())
    sm = StateMachine(ctx=ctx)
    winner = sm._pick_hunt_target([_cq("EA5KB", snr=-5)])
    assert winner is not None
    assert winner.call_from == "EA5KB"


def test_soft_blacklist_case_insensitive():
    """ctx.soft_blacklist enthaelt uppercase Calls, Decode lowercase →
    trotzdem gefiltert."""
    ctx = _ctx(soft_blacklist={"EA5KB"})
    sm = StateMachine(ctx=ctx)
    # Lowercase call_from — wird vom Picker uppercased verglichen
    assert sm._pick_hunt_target([_cq("ea5kb", snr=-5)]) is None


# ---------------------------------------------------------------------------
# Pile-Up Hard-Filter
# ---------------------------------------------------------------------------


def test_pile_up_filters_out_lone_call():
    ctx = _ctx(pile_up_calls={"KH8C"})
    sm = StateMachine(ctx=ctx)
    assert sm._pick_hunt_target([_cq("KH8C", snr=-3)]) is None


def test_pile_up_filters_one_of_many():
    ctx = _ctx(pile_up_calls={"KH8C"})
    sm = StateMachine(ctx=ctx)
    winner = sm._pick_hunt_target(
        [_cq("KH8C", snr=-3), _cq("DL5ABC", snr=-15)]
    )
    assert winner is not None
    assert winner.call_from == "DL5ABC"


# ---------------------------------------------------------------------------
# Slot-Parity Hard-Filter
# ---------------------------------------------------------------------------


def test_slot_parity_filters_call_in_own_tx_slot():
    """DL5ABC sendet even, aktueller Slot ist even → er hoert uns nicht."""
    ctx = _ctx(
        op_slot_parity={"DL5ABC": "even"},
        current_slot_parity="even",
    )
    sm = StateMachine(ctx=ctx)
    assert sm._pick_hunt_target([_cq("DL5ABC")]) is None


def test_slot_parity_allows_other_parity():
    """DL5ABC sendet even, aktueller Slot ist odd → erreichbar."""
    ctx = _ctx(
        op_slot_parity={"DL5ABC": "even"},
        current_slot_parity="odd",
    )
    sm = StateMachine(ctx=ctx)
    winner = sm._pick_hunt_target([_cq("DL5ABC")])
    assert winner is not None
    assert winner.call_from == "DL5ABC"


def test_slot_parity_unknown_op_not_filtered():
    """Ohne bekannte Parity fuer den Op → keine Filterung."""
    ctx = _ctx(
        op_slot_parity={},
        current_slot_parity="even",
    )
    sm = StateMachine(ctx=ctx)
    winner = sm._pick_hunt_target([_cq("DL5ABC")])
    assert winner is not None


def test_slot_parity_no_current_parity_no_filter():
    """current_slot_parity leer → keine Filterung (defensive)."""
    ctx = _ctx(
        op_slot_parity={"DL5ABC": "even"},
        current_slot_parity="",
    )
    sm = StateMachine(ctx=ctx)
    winner = sm._pick_hunt_target([_cq("DL5ABC")])
    assert winner is not None


# ---------------------------------------------------------------------------
# Combined — alle drei Filter gleichzeitig
# ---------------------------------------------------------------------------


def test_all_three_filters_combined():
    """Drei CQs — jeweils einer ist soft-bl/pile-up/own-slot.
    Der vierte (cleane) muss gewinnen."""
    ctx = _ctx(
        soft_blacklist={"BAD1"},
        pile_up_calls={"DX1"},
        op_slot_parity={"OWN1": "even"},
        current_slot_parity="even",
    )
    sm = StateMachine(ctx=ctx)
    decodes = [
        _cq("BAD1", snr=-2),    # soft-blacklist
        _cq("DX1", snr=-2),     # pile-up
        _cq("OWN1", snr=-2),    # own TX slot
        _cq("CLEAN", snr=-20),  # weakest but clean
    ]
    winner = sm._pick_hunt_target(decodes)
    assert winner is not None
    assert winner.call_from == "CLEAN"


def test_all_three_filtered_returns_none():
    ctx = _ctx(
        soft_blacklist={"BAD1"},
        pile_up_calls={"DX1"},
        op_slot_parity={"OWN1": "even"},
        current_slot_parity="even",
    )
    sm = StateMachine(ctx=ctx)
    decodes = [
        _cq("BAD1"), _cq("DX1"), _cq("OWN1"),
    ]
    assert sm._pick_hunt_target(decodes) is None
