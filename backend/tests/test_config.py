"""Tests for the configuration layer."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ft8_appliance.config import AppConfig, load_config


MINIMAL_YAML = """
operator:
  callsign: DK9XR
"""


FULL_YAML = """
operator:
  callsign: dk9xr
  default_locator: JN58td
  default_power_w: 10

bands:
  - name: "20m"
    freq_khz: 14074
    antenna: endfed_2040
  - name: "40m"
    freq_khz: 7074
    antenna: endfed_2040
  - name: "80m"
    freq_khz: 3573
    antenna: doublet_80_40_20

antennas:
  - name: endfed_2040
    bands: ["20m", "40m"]
  - name: doublet_80_40_20
    bands: ["80m", "40m", "20m"]

operating:
  auto_cq_interval_s: 30
  max_ptt_s: 18
  swr_max: 2.0

network:
  wifi_priority:
    - { ssid: "Heimnetz", psk: "secret" }
    - { ssid: "Dad-Android" }
  ap_fallback:
    ssid: "ft8-hochgericht"
    psk: "changeme123"

integrations:
  qrz:
    enabled: true
    user: dk9xr
    password: redacted
  psk_reporter:
    enabled: true
    upload_decodes: true

ui:
  language: de
  theme: auto
"""


def test_minimal_config_validates() -> None:
    cfg = AppConfig.model_validate(yaml.safe_load(MINIMAL_YAML))
    assert cfg.operator.callsign == "DK9XR"
    assert cfg.operator.default_locator is None
    assert cfg.operator.default_power_w == 10  # default


def test_full_config_validates() -> None:
    cfg = AppConfig.model_validate(yaml.safe_load(FULL_YAML))
    assert cfg.operator.callsign == "DK9XR"  # uppercased
    assert cfg.operator.default_locator == "JN58td"
    assert len(cfg.bands) == 3
    assert cfg.integrations.qrz.enabled is True
    assert cfg.integrations.hamqth.enabled is True  # default kept
    assert cfg.ui.language == "de"


def test_callsign_validation_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="invalid callsign"):
        AppConfig.model_validate({"operator": {"callsign": "not-a-call"}})


def test_grid_validation_rejects_bad_format() -> None:
    with pytest.raises(ValueError, match="invalid Maidenhead"):
        AppConfig.model_validate(
            {"operator": {"callsign": "DK9XR", "default_locator": "ZZ99"}}
        )


def test_antenna_lookup_helpers() -> None:
    cfg = AppConfig.model_validate(yaml.safe_load(FULL_YAML))
    ant = cfg.antenna_for("20m")
    assert ant is not None and ant.name == "endfed_2040"
    assert cfg.can_tx_on("20m") is True
    assert cfg.can_tx_on("160m") is False  # not configured


def test_load_config_from_disk(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(FULL_YAML)
    cfg = load_config(cfg_path)
    assert cfg.operator.callsign == "DK9XR"


def test_unknown_field_rejected_strict() -> None:
    # extra="forbid" on every model — typo'd keys must fail loudly
    with pytest.raises(ValueError):
        AppConfig.model_validate(
            {"operator": {"callsign": "DK9XR", "typo_field": "boom"}}
        )


# ---------------------------------------------------------------------------
# Incident 2026-05-30: Config-Save plaettete die Operator-Liste + Credentials,
# weil der Frontend-YAML nur den Legacy-`operator:`-Stub schickt. Der Guard
# preserve_operators() muss Operatoren/Creds autoritativ aus der laufenden
# Config bewahren und nur Basisfelder des aktiven Operators uebernehmen.
from ft8_appliance.web.routes.config import preserve_operators  # noqa: E402


def _two_op_config() -> AppConfig:
    return AppConfig.model_validate({
        "operators": [
            {"callsign": "DK9XR", "license_class": "A", "default_locator": "JN58",
             "qrz_user": "dk9xr", "qrz_logbook_api_key": "KEY_DK",
             "qrz_logbooks": {"DK9XR/AM": "AMKEY_DK"}},
            {"callsign": "DO3XR", "license_class": "E", "default_locator": "JN58",
             "qrz_user": "do3xr", "qrz_logbook_api_key": "KEY_DO",
             "clublog_email": "x@y.z", "clublog_app_password": "pw",
             "clublog_api_key": "CL", "qrz_logbooks": {"DO3XR/AM": "AMKEY_DO"}},
        ],
        "active_callsign": "DO3XR",
    })


def test_config_save_preserves_operators_and_creds() -> None:
    current = _two_op_config()
    # Frontend-Post: nur Legacy-Stub des aktiven Operators, KEINE creds/operators
    raw = {
        "operator": {"callsign": "DO3XR", "default_locator": "JN58td",
                     "default_power_w": 25, "license_class": "E"},
        "operating": {"qso_cooldown_min": 360},
    }
    cfg = AppConfig.model_validate(preserve_operators(raw, current))
    by = {o.callsign: o for o in cfg.operators}
    assert set(by) == {"DK9XR", "DO3XR"}, "beide Operatoren muessen ueberleben"
    assert by["DK9XR"].qrz_logbook_api_key == "KEY_DK"
    assert by["DK9XR"].qrz_logbooks == {"DK9XR/AM": "AMKEY_DK"}
    assert by["DO3XR"].clublog_api_key == "CL"
    assert by["DO3XR"].qrz_logbooks == {"DO3XR/AM": "AMKEY_DO"}
    # Basisfeld-Edit am aktiven Operator greift
    assert by["DO3XR"].default_locator == "JN58td"
    assert by["DO3XR"].default_power_w == 25
    assert cfg.active_callsign == "DO3XR"
    assert cfg.operating.qso_cooldown_min == 360


def test_config_save_ignores_injected_operators() -> None:
    """Selbst eine boesartig mitgeschickte operators-Liste wird ignoriert."""
    current = _two_op_config()
    raw = {"operators": [{"callsign": "HACKER", "license_class": "A"}],
           "active_callsign": "HACKER"}
    cfg = AppConfig.model_validate(preserve_operators(raw, current))
    assert {o.callsign for o in cfg.operators} == {"DK9XR", "DO3XR"}
    assert cfg.active_callsign == "DO3XR"
