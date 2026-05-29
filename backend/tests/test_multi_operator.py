"""Multi-Operator-Profile (Sebastian 2026-05-23).

Tests fuer:
1. Backward-Compat: alte single-operator YAML wird automatisch in
   operators=[op] + active_callsign umgewandelt
2. AppConfig.operator-Property liefert aktiven Operator
3. AppConfig.operator-Setter aktualisiert die Liste
4. Globale QRZ-Credentials werden in OperatorConfig migriert
5. Duplicate-Callsign-Validierung
6. Unbekannter active_callsign loest ValueError aus
"""

from __future__ import annotations

import yaml

import pytest
from pydantic import ValidationError

from ft8_appliance.config import (
    AppConfig,
    OperatorConfig,
    BandConfig,
    AntennaConfig,
    OperatingConfig,
)


# ---------------------------------------------------------------------------
def test_legacy_single_operator_migrates_to_list() -> None:
    """Alte YAMLs mit nur ``operator: {...}`` werden transparent
    in das Multi-User-Schema migriert."""
    raw = yaml.safe_load("""
operator:
  callsign: dk9xr
  default_locator: JN58td
  default_power_w: 50
  license_class: A
bands:
  - name: 20m
    freq_khz: 14074
antennas:
  - name: ant20m
    bands: [20m]
""")
    cfg = AppConfig.model_validate(raw)
    assert len(cfg.operators) == 1
    assert cfg.operators[0].callsign == "DK9XR"
    assert cfg.active_callsign == "DK9XR"
    # Backward-compat property liefert den aktiven
    assert cfg.operator.callsign == "DK9XR"
    assert cfg.operator.default_locator == "JN58td"


def test_global_qrz_credentials_are_migrated_to_operator() -> None:
    """Wenn die alte Config qrz-Credentials in integrations.qrz hatte
    aber der Operator selbst keine, werden sie in den Operator gespiegelt."""
    raw = yaml.safe_load("""
operator:
  callsign: DK9XR
bands:
  - name: 20m
    freq_khz: 14074
antennas:
  - name: ant20m
    bands: [20m]
integrations:
  qrz:
    enabled: true
    user: DK9XR
    password: secret123
    logbook_api_key: ABCD-1234
""")
    cfg = AppConfig.model_validate(raw)
    op = cfg.operators[0]
    assert op.qrz_user == "DK9XR"
    assert op.qrz_password == "secret123"
    assert op.qrz_logbook_api_key == "ABCD-1234"


def test_new_schema_with_multiple_operators_works() -> None:
    """Neue YAML-Form: explizite ``operators``-Liste."""
    raw = yaml.safe_load("""
operators:
  - callsign: DK9XR
    default_locator: JN58td
    license_class: A
  - callsign: DL2XYZ
    default_locator: JO31
    license_class: E
active_callsign: DL2XYZ
bands:
  - name: 20m
    freq_khz: 14074
antennas:
  - name: ant20m
    bands: [20m]
""")
    cfg = AppConfig.model_validate(raw)
    assert len(cfg.operators) == 2
    assert cfg.active_callsign == "DL2XYZ"
    assert cfg.operator.callsign == "DL2XYZ"
    assert cfg.operator.license_class == "E"


def test_duplicate_callsign_raises() -> None:
    """Pflicht-Validierung: gleicher Callsign zweimal verboten."""
    with pytest.raises(ValidationError, match="duplicate callsign"):
        AppConfig.model_validate({
            "operators": [
                {"callsign": "DK9XR"},
                {"callsign": "DK9XR", "default_locator": "JN58td"},
            ],
            "active_callsign": "DK9XR",
            "bands": [{"name": "20m", "freq_khz": 14074}],
            "antennas": [{"name": "ant", "bands": ["20m"]}],
        })


def test_active_callsign_not_in_operators_raises() -> None:
    """active_callsign muss in operators existieren."""
    with pytest.raises(ValidationError, match="not in operators"):
        AppConfig.model_validate({
            "operators": [{"callsign": "DK9XR"}],
            "active_callsign": "DL9XYZ",
            "bands": [{"name": "20m", "freq_khz": 14074}],
            "antennas": [{"name": "ant", "bands": ["20m"]}],
        })


def test_active_callsign_auto_set_when_missing() -> None:
    """Wenn active_callsign fehlt aber operators vorhanden, wird der
    erste automatisch gewaehlt."""
    cfg = AppConfig.model_validate({
        "operators": [
            {"callsign": "DK9XR"},
            {"callsign": "DL2XYZ"},
        ],
        "bands": [{"name": "20m", "freq_khz": 14074}],
        "antennas": [{"name": "ant", "bands": ["20m"]}],
    })
    assert cfg.active_callsign == "DK9XR"


def test_operator_property_setter_keeps_list_consistent() -> None:
    """Backward-Compat fuer Tests/Code der cfg.operator = ... macht:
    der Setter ersetzt den aktiven in der operators-Liste."""
    cfg = AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[BandConfig(name="20m", freq_khz=14074)],
        antennas=[AntennaConfig(name="ant", bands=["20m"])],
        operating=OperatingConfig(),
    )
    assert cfg.operator.callsign == "DK9XR"
    # Aktiven Operator ersetzen
    cfg.operator = OperatorConfig(callsign="DL2XYZ", default_locator="JO31")
    assert cfg.operator.callsign == "DL2XYZ"
    assert cfg.active_callsign == "DL2XYZ"
    assert len(cfg.operators) == 1  # Replaced, nicht hinzugefuegt


def test_per_operator_qrz_credentials_isolated() -> None:
    """Verschiedene Operatoren koennen verschiedene QRZ-Accounts haben."""
    cfg = AppConfig.model_validate({
        "operators": [
            {
                "callsign": "DK9XR",
                "qrz_user": "DK9XR",
                "qrz_logbook_api_key": "AAAA",
            },
            {
                "callsign": "DL2XYZ",
                "qrz_user": "DL2XYZ",
                "qrz_logbook_api_key": "BBBB",
            },
        ],
        "active_callsign": "DK9XR",
        "bands": [{"name": "20m", "freq_khz": 14074}],
        "antennas": [{"name": "ant", "bands": ["20m"]}],
    })
    assert cfg.operators[0].qrz_logbook_api_key == "AAAA"
    assert cfg.operators[1].qrz_logbook_api_key == "BBBB"
    assert cfg.operator.qrz_logbook_api_key == "AAAA"  # active


def test_no_operators_raises_when_accessed() -> None:
    """Operatorless config (zb beim Wizard-Bootstrap) erlaubt aber
    der Zugriff auf .operator wirft."""
    # Wizard-Mode: kein Operator gesetzt → ist erlaubt
    cfg = AppConfig.model_validate({
        "operators": [],
        "bands": [{"name": "20m", "freq_khz": 14074}],
        "antennas": [{"name": "ant", "bands": ["20m"]}],
    })
    with pytest.raises(ValueError, match="keine Operators"):
        _ = cfg.operator


# ---------------------------------------------------------------------------
def test_qrz_key_for_routes_by_on_air_call() -> None:
    """v0.28.0 — qrz_logbooks-Map routet den Logbook-Key am On-Air-Call;
    fehlt ein Eintrag, faellt es auf den Heimat-Key zurueck."""
    op = OperatorConfig(
        callsign="DO3XR",
        qrz_logbook_api_key="HOME",
        qrz_logbooks={"do3xr/am": "AM", "9A/DO3XR": "CRO"},
    )
    assert op.qrz_key_for("DO3XR") == "HOME"
    assert op.qrz_key_for("DO3XR/AM") == "AM"      # case-insensitiv
    assert op.qrz_key_for("9A/DO3XR") == "CRO"
    assert op.qrz_key_for("VK/DO3XR") == "HOME"    # unbekannt → Heimat
    assert op.qrz_key_for(None) == "HOME"
    # Keys werden uppercased gespeichert
    assert set(op.qrz_logbooks) == {"DO3XR/AM", "9A/DO3XR"}


def test_base_call_groups_suffix_and_prefix_variants() -> None:
    """DO3XR, DO3XR/AM und 9A/DO3XR sind dieselbe Person (gleicher
    base_call), DK9XR nicht."""
    from ft8_appliance.util.callsign import base_call
    assert base_call("DO3XR/AM") == "DO3XR"
    assert base_call("9A/DO3XR") == "DO3XR"
    assert base_call("DO3XR/MM") == "DO3XR"
    assert base_call("DK9XR") != base_call("DO3XR")
