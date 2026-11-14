"""Tests v0.19.0 — Pile-Up-Avoidance + DXpedition-Schedule."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ft8_appliance.statemachine.machine import (
    HUNT_TIERS,
    _tier_not_in_pileup,
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
# Pile-Up Tier
# ---------------------------------------------------------------------------


def test_pileup_tier_clean_call_returns_1():
    ctx = _ctx(pile_up_calls=set())
    assert _tier_not_in_pileup(_d("DL5ABC"), ctx) == 1


def test_pileup_tier_in_pileup_returns_0():
    ctx = _ctx(pile_up_calls={"ZL9HR"})
    assert _tier_not_in_pileup(_d("ZL9HR"), ctx) == 0


def test_pileup_tier_case_insensitive():
    ctx = _ctx(pile_up_calls={"ZL9HR"})
    assert _tier_not_in_pileup(_d("zl9hr"), ctx) == 0


def test_pileup_tier_exempts_new_dxcc():
    """v0.44.0 — ein NEUES DXCC ist den Pile-Up-Kampf wert: trotz Pile-Up
    liefert der Tier 1 (kein Meiden), damit die Headless-Box den Sonderling
    jagt statt ihn wegen Pile-Up zu uebergehen."""
    ctx = _ctx(pile_up_calls={"ZL9HR"}, new_dxcc_calls={"ZL9HR"})
    assert _tier_not_in_pileup(_d("ZL9HR"), ctx) == 1
    # Gegenprobe: Pile-Up-Call der KEIN neues DXCC ist → weiter gemieden.
    ctx2 = _ctx(pile_up_calls={"DL5ABC"}, new_dxcc_calls=set())
    assert _tier_not_in_pileup(_d("DL5ABC"), ctx2) == 0


def test_pileup_tier_no_call_from_returns_1():
    ctx = _ctx(pile_up_calls={"ZL9HR"})
    d = _d(None)
    d.call_from = None
    assert _tier_not_in_pileup(d, ctx) == 1


def test_pileup_tier_in_registry():
    assert "not_in_pileup" in HUNT_TIERS


# ---------------------------------------------------------------------------
# Detection-Heuristik (im Orchestrator) — testen via direkter Methode
# ---------------------------------------------------------------------------


def _selbst(davor=None):
    """Was _detect_pile_ups vom Orchestrator braucht: den vorigen Slot und das eigene Rufzeichen."""
    from types import SimpleNamespace
    return SimpleNamespace(
        _slot_history=[davor] if davor is not None else [],
        config=SimpleNamespace(operator=SimpleNamespace(callsign="DK9XR")),
    )


def _cq(call: str) -> DecodedMsg:
    return DecodedMsg(ts=datetime.now(UTC), call_from=call, call_to=None, grid=None,
                      message=f"CQ {call}", snr_db=-5, dt_s=0.1, freq_offset_hz=1500, band="15m")


def _anruf(von: str, an: str, hz: int) -> DecodedMsg:
    return DecodedMsg(ts=datetime.now(UTC), call_from=von, call_to=an, grid=None,
                      message=f"{an} {von} -10", snr_db=-12, dt_s=0.1,
                      freq_offset_hz=hz, band="15m")


def test_pileup_detection_rare_dx_auto_flagged():
    """rarity_score >= 70 → automatisch Pile-Up-Verdacht."""
    from ft8_appliance.runtime.orchestrator import Orchestrator
    result = Orchestrator._detect_pile_ups(_selbst(), [_cq("ZL9HR")], {"ZL9HR": 95})
    assert "ZL9HR" in result


def test_pileup_detection_zaehlt_die_anrufer_im_slot_davor():
    """Vier oder mehr Stationen haben ihn im vorigen Slot angerufen → Pile-Up.

    Audit 14.11.2026: Gezaehlt wurde bis dahin im selben Slot wie das CQ, in
    ±50 Hz um seine Frequenz. Dort senden die Anrufer nie (Gegenparitaet), und
    sie rufen auf beliebigen Frequenzen."""
    from ft8_appliance.runtime.orchestrator import Orchestrator
    davor = [_anruf(c, "VK0XX", 400 + 500 * i)
             for i, c in enumerate(["DL1AB", "F5XYZ", "OK1AB", "EA2QQ"])]
    result = Orchestrator._detect_pile_ups(_selbst(davor), [_cq("VK0XX")], {"VK0XX": 50})
    assert "VK0XX" in result
    # Derselbe Slot zaehlt nicht mehr, und der eigene Anruf auch nicht.
    selber_slot = [_cq("VK0XX")] + davor
    assert "VK0XX" not in Orchestrator._detect_pile_ups(_selbst([]), selber_slot, {"VK0XX": 50})
    mit_uns = davor[:3] + [_anruf("DK9XR", "VK0XX", 1200)]
    assert "VK0XX" not in Orchestrator._detect_pile_ups(_selbst(mit_uns), [_cq("VK0XX")], {})


def test_pileup_detection_normal_cq_not_flagged():
    """Normaler CQ ohne Pile-Up + rarity 0 → kein Flag."""
    from ft8_appliance.runtime.orchestrator import Orchestrator
    result = Orchestrator._detect_pile_ups(_selbst([]), [_cq("DL5ABC")], {})
    assert "DL5ABC" not in result


# ---------------------------------------------------------------------------
# OperatingConfig: known tiers + default order
# ---------------------------------------------------------------------------


def test_default_hunt_priority_has_not_in_pileup_at_position_3():
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig()
    assert cfg.hunt_priority[0] == "not_bad_reputation"
    assert cfg.hunt_priority[1] == "not_his_tx_slot"
    assert cfg.hunt_priority[2] == "not_in_pileup"


def test_migration_adds_not_in_pileup():
    from ft8_appliance.config.models import OperatingConfig
    old = ["marine_psk", "snr"]
    cfg = OperatingConfig(hunt_priority=old)
    assert "not_in_pileup" in cfg.hunt_priority


def test_migration_len_is_21():
    """Current default: 20 known Tiers."""
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig(hunt_priority=[])
    assert len(cfg.hunt_priority) == 22  # 2026-09-06: + lonely_cq
