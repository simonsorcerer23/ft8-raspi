"""Tests v0.22.0 — DX-Operating-Location.

Drei Feature-Bloecke werden hier getestet:
1. integrations.cept — Country-Lookup, GPS-Detection, CEPT-Compliance
2. State-Machine tx_callsign + TX-Helper-Output mit Prefix
3. AppConfig can_tx_on + effective_max_power_w mit CEPT-Awareness
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ft8_appliance.config.models import (
    AntennaConfig, AppConfig, BandConfig, OperatorConfig, OperatingConfig, RigConfig,
)
from ft8_appliance.integrations.cept import (
    COUNTRIES,
    cept_compliance,
    cept_power_cap,
    country,
    detect_from_gps,
)
from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import MachineContext


# ---------------------------------------------------------------------------
# 1. integrations.cept
# ---------------------------------------------------------------------------


def test_country_lookup_known():
    info = country("9A")
    assert info is not None
    assert info.name == "Kroatien"
    assert info.prefix == "9A"


def test_country_lookup_unknown_returns_none():
    assert country("XX") is None
    assert country(None) is None
    assert country("") is None


def test_country_lookup_case_insensitive():
    assert country("9a") is not None
    assert country("9a").code == "9A"


def test_detect_from_gps_kroatien():
    # Split (Kroatien) — etwa 43.5°N 16.4°E
    assert detect_from_gps(43.5, 16.4) == "9A"


def test_detect_from_gps_deutschland():
    # München
    assert detect_from_gps(48.13, 11.58) == "DL"


def test_detect_from_gps_griechenland():
    # Athen
    assert detect_from_gps(37.98, 23.73) == "SV"


def test_detect_from_gps_no_fix_returns_none():
    assert detect_from_gps(None, None) is None
    assert detect_from_gps(None, 10.0) is None
    assert detect_from_gps(10.0, None) is None


def test_detect_from_gps_atlantic_no_match():
    # Mitten im Atlantik — keine Country-Box matcht
    assert detect_from_gps(40.0, -40.0) is None


def test_cept_compliance_home_country_always_ok():
    allowed, reason = cept_compliance("DL", "DL", "E")
    assert allowed is True
    assert reason is None


def test_cept_compliance_class_a_in_foreign_country():
    allowed, reason = cept_compliance("9A", "DL", "A")
    assert allowed is True
    assert reason is None


def test_cept_compliance_class_e_in_cept1_only_country_blocked():
    """Klasse E (CEPT-Novice) ist in CEPT-1-only-Laendern GESPERRT.
    Frankreich setzt T/R 61-01 um, aber NICHT ECC/REC (05)06."""
    allowed, reason = cept_compliance("F", "DL", "E")
    assert allowed is False
    assert reason is not None
    assert "Klasse E" in reason
    assert "Frankreich" in reason


def test_cept_compliance_class_e_in_novice_country_allowed():
    """Klasse E IST in CEPT-Novice-Laendern erlaubt (ECC/REC 05-06).
    Kroatien, Oesterreich, Schweiz etc. setzen die Novice-Empfehlung um."""
    for code in ("9A", "OE", "HB9", "PA", "OK", "OZ", "HA", "S5"):
        allowed, reason = cept_compliance(code, "DL", "E")
        assert allowed is True, f"Klasse E sollte in {code} erlaubt sein"
        assert reason is None


def test_cept_compliance_class_e_cept1_only_set():
    """Stichprobe der CEPT-1-only-Laender — Klasse E ueberall gesperrt."""
    for code in ("F", "I", "EA", "SV", "G", "SM", "LA", "EI", "TA"):
        allowed, _ = cept_compliance(code, "DL", "E")
        assert allowed is False, f"Klasse E darf NICHT in {code}"


def test_cept_compliance_unknown_country_blocked():
    allowed, reason = cept_compliance("XX", "DL", "A")
    assert allowed is False
    assert "XX" in (reason or "")


def test_cept_compliance_none_country_ok():
    """current_operating_country=None bedeutet Heimat — kein Block."""
    allowed, reason = cept_compliance(None, "DL", "E")
    assert allowed is True


def test_cept_power_cap_foreign():
    """Frankreich hat 500W-Cap vs DL 750W."""
    assert cept_power_cap("F", "DL") == 500


def test_cept_power_cap_home_none():
    assert cept_power_cap("DL", "DL") is None
    assert cept_power_cap(None, "DL") is None


def test_cept_power_cap_unknown_country_none():
    assert cept_power_cap("XX", "DL") is None


# ---------------------------------------------------------------------------
# 2. MachineContext.tx_callsign + State-Machine TX-Helpers
# ---------------------------------------------------------------------------


def _ctx(**overrides) -> MachineContext:
    c = MachineContext(callsign="DK9XR", my_grid="JN58", band="15m")
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def test_tx_callsign_no_dx():
    """Default: home_country=DL, current=None → tx_callsign = home call."""
    ctx = _ctx(home_country="DL", current_operating_country=None)
    assert ctx.tx_callsign == "DK9XR"


def test_tx_callsign_dx_prefix():
    """Auslandsbetrieb: tx_callsign = <country>/<call>."""
    ctx = _ctx(home_country="DL", current_operating_country="9A")
    assert ctx.tx_callsign == "9A/DK9XR"


def test_tx_callsign_current_eq_home_no_prefix():
    """Wenn current_country == home_country: kein Prefix."""
    ctx = _ctx(home_country="DL", current_operating_country="DL")
    assert ctx.tx_callsign == "DK9XR"


def test_emit_cq_uses_tx_callsign():
    """CQ-Message mit Prefix wenn DX aktiv."""
    ctx = _ctx(home_country="DL", current_operating_country="9A")
    sm = StateMachine(ctx=ctx)
    sm._emit_cq()
    actions = sm.drain_actions()
    cq = next(a for a in actions if a.kind == "TX_MESSAGE")
    assert cq.payload["message"].startswith("CQ 9A/DK9XR")


def test_emit_cq_no_prefix_when_home():
    ctx = _ctx(home_country="DL", current_operating_country=None)
    sm = StateMachine(ctx=ctx)
    sm._emit_cq()
    cq = next(a for a in sm.drain_actions() if a.kind == "TX_MESSAGE")
    assert cq.payload["message"].startswith("CQ DK9XR")
    assert "/" not in cq.payload["message"].split()[1]


def test_log_qso_carries_station_and_operator_calls():
    """LOG_QSO-Action enthaelt station_callsign (mit Prefix) + operator
    (Heimat)."""
    from ft8_appliance.statemachine.states import QsoContext
    ctx = _ctx(home_country="DL", current_operating_country="F")
    sm = StateMachine(ctx=ctx)
    sm.qso = QsoContext(
        their_call="EA4XYZ", their_grid="IM98",
        band="15m", freq_offset_hz=1500,
        their_snr=-10, our_snr_received=-15,
    )
    sm._emit_log_qso()
    log_action = next(a for a in sm.drain_actions() if a.kind == "LOG_QSO")
    assert log_action.payload["station_callsign"] == "F/DK9XR"
    assert log_action.payload["operator"] == "DK9XR"


# ---------------------------------------------------------------------------
# 3. AppConfig CEPT-Awareness — can_tx_on + effective_max_power_w
# ---------------------------------------------------------------------------


def _app_config(license_class: str = "A",
                home_country: str = "DL",
                current_op_country: str | None = None) -> AppConfig:
    return AppConfig(
        operators=[OperatorConfig(
            callsign="DK9XR",
            default_locator="JN58",
            license_class=license_class,
            home_country=home_country,
            current_operating_country=current_op_country,
        )],
        bands=[
            BandConfig(name="15m", freq_khz=21074),
            BandConfig(name="20m", freq_khz=14074),
            BandConfig(name="40m", freq_khz=7074),
        ],
        antennas=[AntennaConfig(name="EFHW", bands=["15m", "20m", "40m"])],
        rig=RigConfig(model="ic7300", max_power_w=100),
        operating=OperatingConfig(),
    )


def test_can_tx_on_home_class_a_all_bands():
    cfg = _app_config(license_class="A")
    assert cfg.can_tx_on("15m") is True
    assert cfg.can_tx_on("20m") is True
    assert cfg.can_tx_on("40m") is True


def test_can_tx_on_class_e_only_allowed_bands_at_home():
    cfg = _app_config(license_class="E")
    # Klasse E darf in DL: 80, 15, 10, 2, 70cm — NICHT 20m / 40m
    assert cfg.can_tx_on("15m") is True
    assert cfg.can_tx_on("20m") is False
    assert cfg.can_tx_on("40m") is False


def test_can_tx_on_class_a_in_foreign_country():
    """Klasse A im Ausland (Frankreich) — Bands die in Frankreich erlaubt
    sind UND die wir an einer Antenne haben."""
    cfg = _app_config(license_class="A", current_op_country="F")
    # 15m sollte gehen — Frankreich erlaubt Klasse A auf 15m
    assert cfg.can_tx_on("15m") is True


def test_can_tx_on_class_e_in_cept1_only_country_blocked():
    """Klasse E in CEPT-1-only-Land (Frankreich) → HART BLOCKED."""
    cfg = _app_config(license_class="E", current_op_country="F")
    assert cfg.can_tx_on("15m") is False


def test_can_tx_on_class_e_in_novice_country_allowed():
    """Klasse E in CEPT-Novice-Land (Kroatien) → erlaubt (auf den
    Baendern die Klasse E national hat + Antenne deckt)."""
    cfg = _app_config(license_class="E", current_op_country="9A")
    assert cfg.can_tx_on("15m") is True   # 15m: Klasse E national erlaubt
    assert cfg.can_tx_on("20m") is False  # 20m: Klasse E national gesperrt


def test_cept_lock_reason_when_blocked():
    cfg = _app_config(license_class="E", current_op_country="F")
    reason = cfg.cept_lock_reason()
    assert reason is not None
    assert "Klasse E" in reason


def test_cept_lock_reason_none_when_novice_country():
    """Klasse E in CEPT-Novice-Land → kein Lock-Reason."""
    cfg = _app_config(license_class="E", current_op_country="9A")
    assert cfg.cept_lock_reason() is None


def test_cept_lock_reason_none_when_class_a():
    cfg = _app_config(license_class="A", current_op_country="9A")
    assert cfg.cept_lock_reason() is None


def test_effective_max_power_w_uses_cept_cap():
    """Frankreich hat 500W-Cap, Rig hat 100W → MIN = 100W (Rig gewinnt)."""
    cfg = _app_config(license_class="A", current_op_country="F")
    # license_cap (Klasse A 15m = 750W) ∩ rig (100W) ∩ cept (500W) = 100W
    assert cfg.effective_max_power_w("15m") == 100


def test_effective_max_power_w_at_home_no_cept_cap():
    """Daheim: kein CEPT-Cap, nur license + rig."""
    cfg = _app_config(license_class="A")
    # license 750W ∩ rig 100W = 100W
    assert cfg.effective_max_power_w("15m") == 100


def test_unknown_country_blocked():
    """Country-Code der nicht in cept.py-DB ist → blocked."""
    cfg = _app_config(license_class="A", current_op_country="XX")
    assert cfg.can_tx_on("15m") is False
