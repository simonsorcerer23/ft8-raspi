"""Operator-Zugangsdaten nachtraeglich pflegen + Default-Bandplan.

Hintergrund (Sebastian 2026-07-30): QRZ-/ClubLog-Credentials liessen sich
nur beim *Anlegen* eines Profils setzen. Wer sie nachtragen wollte, kam
nicht weiter — DELETE scheitert an der QSO-Historie und POST an 409.
Dazu kam ein stiller Cross-Account-Bug: wechselte man auf ein Profil ohne
eigene QRZ-Credentials, blieben die des Vorgaengers global stehen und
dessen Logbuch haette die fremden QSOs bekommen.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ft8_appliance.config import AppConfig
from ft8_appliance.runtime.orchestrator import Orchestrator
from ft8_appliance.web import create_app


def _cfg(**over: object) -> AppConfig:
    raw: dict[str, object] = {
        "operators": [
            {
                "callsign": "DK9XR",
                "license_class": "A",
                "qrz_user": "DK9XR",
                "qrz_password": "pw-dad",
                "qrz_logbook_api_key": "KEY-DAD",
            },
            {"callsign": "DO3XR", "license_class": "E"},
        ],
        "active_callsign": "DK9XR",
        "antennas": [{"name": "spitzwegstrasse", "bands": ["15m"]}],
    }
    raw.update(over)
    return AppConfig.model_validate(raw)


class _FakeOrch:
    """Nur was die operators-Routen anfassen."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.persisted = 0
        self.integrations_reloaded = 0

    async def persist_config(self) -> None:
        self.persisted += 1

    def reload_active_operator_integrations(self) -> None:
        self.integrations_reloaded += 1


@pytest.fixture
def orch() -> _FakeOrch:
    return _FakeOrch(_cfg())


@pytest.fixture
def client(orch: _FakeOrch) -> TestClient:
    app = create_app()
    app.state.orchestrator = orch
    return TestClient(app)


# ------------------------------------------------------- PATCH /operators
def test_patch_adds_qrz_credentials_to_operator_without_any(
    client: TestClient, orch: _FakeOrch
) -> None:
    r = client.patch("/api/operators/DO3XR", json={
        "qrz_user": "DO3XR",
        "qrz_password": "pw-sohn",
        "qrz_logbook_api_key": "KEY-SOHN",
    })
    assert r.status_code == 200, r.text
    assert r.json()["has_qrz_credentials"] is True
    do3 = [o for o in orch.config.operators if o.callsign == "DO3XR"][0]
    assert do3.qrz_user == "DO3XR"
    assert do3.qrz_logbook_api_key == "KEY-SOHN"
    assert orch.persisted == 1


def test_patch_adds_clublog_credentials(client: TestClient, orch: _FakeOrch) -> None:
    r = client.patch("/api/operators/DO3XR", json={
        "clublog_email": "do3xr@example.org",
        "clublog_app_password": "app-pw",
        "clublog_api_key": "cl-key",
    })
    assert r.status_code == 200, r.text
    assert r.json()["has_clublog_credentials"] is True
    do3 = [o for o in orch.config.operators if o.callsign == "DO3XR"][0]
    assert do3.clublog_app_password == "app-pw"


def test_patch_empty_string_clears_a_credential(
    client: TestClient, orch: _FakeOrch
) -> None:
    """Leerer String = Feld loeschen — sonst liesse sich ein fremder
    Zugang nicht entfernen ohne das ganze Profil zu loeschen."""
    r = client.patch("/api/operators/DK9XR", json={
        "qrz_user": "", "qrz_password": "", "qrz_logbook_api_key": "",
    })
    assert r.status_code == 200, r.text
    assert r.json()["has_qrz_credentials"] is False
    dk = [o for o in orch.config.operators if o.callsign == "DK9XR"][0]
    assert dk.qrz_user is None
    assert dk.qrz_password is None


def test_patch_leaves_unsent_fields_untouched(
    client: TestClient, orch: _FakeOrch
) -> None:
    """Nur das Passwort aendern darf den API-Key nicht mit wegraeumen."""
    r = client.patch("/api/operators/DK9XR", json={"qrz_password": "neu"})
    assert r.status_code == 200, r.text
    dk = [o for o in orch.config.operators if o.callsign == "DK9XR"][0]
    assert dk.qrz_password == "neu"
    assert dk.qrz_user == "DK9XR"
    assert dk.qrz_logbook_api_key == "KEY-DAD"


def test_patch_active_operator_reloads_integrations(
    client: TestClient, orch: _FakeOrch
) -> None:
    """Aktiver Operator → QRZ-Client sofort neu, nicht erst nach Reboot."""
    client.patch("/api/operators/DK9XR", json={"qrz_password": "neu"})
    assert orch.integrations_reloaded == 1


def test_patch_inactive_operator_does_not_touch_integrations(
    client: TestClient, orch: _FakeOrch
) -> None:
    client.patch("/api/operators/DO3XR", json={"qrz_password": "neu"})
    assert orch.integrations_reloaded == 0


def test_patch_can_change_license_class_and_power(
    client: TestClient, orch: _FakeOrch
) -> None:
    r = client.patch("/api/operators/DO3XR", json={
        "license_class": "N", "default_power_w": 10,
    })
    assert r.status_code == 200, r.text
    assert r.json()["license_class"] == "N"


def test_patch_unknown_operator_is_404(client: TestClient) -> None:
    r = client.patch("/api/operators/XX1YYY", json={"qrz_user": "x"})
    assert r.status_code == 404


def test_patch_without_fields_is_400(client: TestClient) -> None:
    r = client.patch("/api/operators/DO3XR", json={})
    assert r.status_code == 400


def test_patch_rejects_invalid_locator(client: TestClient) -> None:
    r = client.patch("/api/operators/DO3XR", json={"default_locator": "nope!"})
    assert r.status_code == 400


def test_patch_cannot_rename_callsign(client: TestClient) -> None:
    """callsign ist nicht Teil des Requests — an ihm haengt Qso.user_callsign."""
    r = client.patch("/api/operators/DO3XR", json={"callsign": "DL1ABC"})
    assert r.status_code == 422


# ------------------------------------------- Cross-Account-Leak (Regression)
def _bare_orchestrator(cfg: AppConfig) -> Orchestrator:
    """Orchestrator ohne __init__/Hardware — der Credential-Sync liest
    ausschliesslich self.config, mehr braucht es hier nicht."""
    orch = Orchestrator.__new__(Orchestrator)
    orch.config = cfg
    return orch


def test_switch_to_operator_without_qrz_clears_global_credentials() -> None:
    """Wechsel auf ein Profil ohne eigene QRZ-Credentials darf die des
    Vorgaengers NICHT stehen lassen — sonst laden DO3XRs QSOs in DK9XRs
    Logbuch hoch."""
    cfg = _cfg()
    orch = _bare_orchestrator(cfg)
    dk9 = [o for o in cfg.operators if o.callsign == "DK9XR"][0]
    do3 = [o for o in cfg.operators if o.callsign == "DO3XR"][0]

    orch._sync_global_integrations_from_operator(dk9)
    assert cfg.integrations.qrz.user == "DK9XR"
    assert cfg.integrations.qrz.logbook_api_key == "KEY-DAD"

    orch._sync_global_integrations_from_operator(do3)
    assert cfg.integrations.qrz.user is None
    assert cfg.integrations.qrz.password is None
    assert cfg.integrations.qrz.logbook_api_key is None
    assert cfg.integrations.ntfy.topic == "ft8-do3xr"


def test_single_operator_keeps_globally_configured_qrz() -> None:
    """Bei genau einem Profil ist die globale Online-Dienste-Sektion die
    einzige Pflegestelle — der Sync darf sie nicht leerraeumen."""
    cfg = AppConfig.model_validate({
        "operators": [{"callsign": "DK9XR"}],
        "active_callsign": "DK9XR",
        "integrations": {"qrz": {
            "enabled": True, "user": "DK9XR", "password": "pw",
            "logbook_api_key": "KEY",
        }},
    })
    orch = _bare_orchestrator(cfg)
    # Die Migration spiegelt globale Credentials ins Profil; entscheidend
    # ist, dass der Sync sie danach nicht wieder wegwirft.
    orch._sync_global_integrations_from_operator(cfg.operators[0])
    assert cfg.integrations.qrz.user == "DK9XR"
    assert cfg.integrations.qrz.logbook_api_key == "KEY"


# --------------------------------------------------------- Default-Bandplan
def test_fresh_config_has_the_full_band_plan() -> None:
    """Baender sind physikalisch fix — ein frisch aufgesetzter Pi soll sie
    komplett haben, statt dass sie von Hand nachgetragen werden muessen."""
    cfg = AppConfig.model_validate({
        "operators": [{"callsign": "DK9XR"}], "active_callsign": "DK9XR",
    })
    names = [b.name for b in cfg.bands]
    assert names == ["160m", "80m", "60m", "40m", "30m", "20m", "17m",
                     "15m", "12m", "10m", "6m", "2m", "70cm"]


def test_every_ft8_dial_matches_the_bandplan_module() -> None:
    """Jeder FT8-Dial muss auf dem Segmentanfang aus util/bandplan.py
    liegen. Zweite, unabhaengige Quelle im Repo — ein Tippfehler in der
    Default-Tabelle waere sonst eine Fehlabstimmung beim Senden."""
    from ft8_appliance.util.bandplan import _FT8_SEGMENTS

    cfg = AppConfig.model_validate({
        "operators": [{"callsign": "DK9XR"}], "active_callsign": "DK9XR",
    })
    for band in cfg.bands:
        segs = [s for s in _FT8_SEGMENTS if s.band == band.name]
        assert segs, f"kein FT8-Segment fuer {band.name}"
        assert band.freq_khz * 1_000 == segs[0].lo_hz, band.name


def test_70cm_has_no_guessed_ft4_dial() -> None:
    """Fuer 70cm gibt es keinen etablierten FT4-Dial. Lieber der FT8-
    Fallback als ein geratener Wert, der das Rig fehl abstimmt."""
    cfg = AppConfig.model_validate({
        "operators": [{"callsign": "DK9XR"}], "active_callsign": "DK9XR",
    })
    band = [b for b in cfg.bands if b.name == "70cm"][0]
    assert band.freq_khz_ft4 is None
    assert band.freq_for_mode("FT4") == band.freq_khz


def test_explicit_bands_still_win_over_the_default() -> None:
    """Wer seine Baender in der YAML pflegt, bekommt genau die."""
    cfg = AppConfig.model_validate({
        "operators": [{"callsign": "DK9XR"}], "active_callsign": "DK9XR",
        "bands": [{"name": "20m", "freq_khz": 14074}],
    })
    assert [b.name for b in cfg.bands] == ["20m"]
