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


def test_pileup_detection_rare_dx_auto_flagged():
    """rarity_score >= 70 → automatisch Pile-Up-Verdacht."""
    # Wir testen _detect_pile_ups als statische Methode mit Mock-Self.
    from ft8_appliance.runtime.orchestrator import Orchestrator
    # Synthetischen Decode: ZL9HR mit rarity 95
    decodes = [DecodedMsg(
        ts=datetime.now(UTC), call_from="ZL9HR", call_to=None, grid=None,
        message="CQ ZL9HR", snr_db=-5, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )]
    # _detect_pile_ups ist instance-method, aber benutzt kein self.
    # Brauchen instance — bauen die nicht, sondern testen die Logik
    # direkt mit nem dummy.
    result = Orchestrator._detect_pile_ups(
        None, decodes, {"ZL9HR": 95},
    )
    assert "ZL9HR" in result


def test_pileup_detection_density():
    """Wenn 4+ unique callers auf ±50 Hz der DX-Freq → Pile-Up."""
    from ft8_appliance.runtime.orchestrator import Orchestrator
    decodes = [
        # DX-CQ auf 1500 Hz
        DecodedMsg(
            ts=datetime.now(UTC), call_from="VK0XX", call_to=None, grid=None,
            message="CQ VK0XX", snr_db=-3, dt_s=0.1,
            freq_offset_hz=1500, band="15m",
        ),
    ]
    # 4 andere Stationen senden auf ±50 Hz (Pile-Up-Caller)
    for i, c in enumerate(["DL1AB", "F5XYZ", "OK1AB", "EA2QQ"]):
        decodes.append(DecodedMsg(
            ts=datetime.now(UTC), call_from=c, call_to="VK0XX", grid=None,
            message=f"VK0XX {c} -10", snr_db=-12, dt_s=0.1,
            freq_offset_hz=1500 + (i - 2) * 25, band="15m",
        ))
    result = Orchestrator._detect_pile_ups(
        None, decodes, {"VK0XX": 50},  # rarity unter 70 → braucht density
    )
    assert "VK0XX" in result


def test_pileup_detection_normal_cq_not_flagged():
    """Normaler CQ ohne Pile-Up + rarity 0 → kein Flag."""
    from ft8_appliance.runtime.orchestrator import Orchestrator
    decodes = [DecodedMsg(
        ts=datetime.now(UTC), call_from="DL5ABC", call_to=None, grid=None,
        message="CQ DL5ABC", snr_db=-10, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )]
    result = Orchestrator._detect_pile_ups(None, decodes, {})
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


def test_migration_len_is_20():
    """Current default: 20 known Tiers."""
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig(hunt_priority=[])
    assert len(cfg.hunt_priority) == 20
