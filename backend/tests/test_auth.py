"""API-Token-Auth (v0.37.0, Audit SEC-C1).

Prueft: ohne Token → 401; Master-Token (Header + ?token=) → durch;
Action-Token nur fuer ACTION_PATHS; fail-open wenn kein Token konfiguriert.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ft8_appliance.config import AppConfig, set_config_for_tests
from ft8_appliance.web import create_app

from .test_web import FakeOrchestrator

MASTER = "master-token-xyz"
ACTION = "action-token-abc"


@pytest.fixture
def client_authed() -> TestClient:
    set_config_for_tests(AppConfig.model_validate({
        "operators": [{"callsign": "DK9XR", "license_class": "A"}],
        "active_callsign": "DK9XR",
        "api_token": MASTER,
        "ntfy_action_token": ACTION,
    }))
    app = create_app()
    app.state.orchestrator = FakeOrchestrator()
    # TestClient-Default-Host ist "testclient" (nicht localhost) → Auth greift.
    return TestClient(app)


def test_api_requires_token(client_authed: TestClient) -> None:
    assert client_authed.get("/api/status").status_code == 401


def test_master_token_header_passes(client_authed: TestClient) -> None:
    r = client_authed.get("/api/status", headers={"Authorization": f"Bearer {MASTER}"})
    assert r.status_code != 401


def test_master_token_query_passes(client_authed: TestClient) -> None:
    # ?token= fuer SSE (EventSource kann keine Header setzen)
    r = client_authed.get(f"/api/status?token={MASTER}")
    assert r.status_code != 401


def test_wrong_token_rejected(client_authed: TestClient) -> None:
    r = client_authed.get("/api/status", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_action_token_wird_nicht_mehr_akzeptiert(client_authed: TestClient) -> None:
    """Der enge ntfy-Aktions-Token ist seit 2026-09-13 ausser Dienst.

    Er steckte in den Aktionsknoepfen der Push-Meldungen und lag damit im
    Klartext auf einem oeffentlichen ntfy-Topic, dessen Name sich aus dem
    Rufzeichen ableitet (``ft8-dk9xr``) — und das Rufzeichen steht in einem
    veroeffentlichten Blogartikel. Dass ihn niemand nutzen konnte, lag
    allein daran, dass die Knopf-Adresse auf einen von aussen nicht
    aufloesbaren Hostnamen zeigte. Benutzt wurden die Knoepfe nie.

    Der Test haelt fest, dass er auch die Steuerpfade nicht mehr oeffnet —
    sonst kaeme die Hintertuer bei einer spaeteren Aenderung still zurueck.
    """
    for pfad in ("/api/control/stop", "/api/control/cq",
                 "/api/control/auto-answer", "/api/control/shutdown"):
        r = client_authed.post(pfad, headers={"Authorization": f"Bearer {ACTION}"})
        assert r.status_code == 401, f"{pfad} akzeptiert den alten Aktions-Token noch"
    assert client_authed.get(
        "/api/config", headers={"Authorization": f"Bearer {ACTION}"}
    ).status_code == 401


def test_static_spa_is_public(client_authed: TestClient) -> None:
    # "/" (SPA) ist kein /api → ohne Token erreichbar (200 oder 404, nie 401)
    assert client_authed.get("/").status_code != 401


def test_fail_open_without_token() -> None:
    set_config_for_tests(AppConfig.model_validate({
        "operators": [{"callsign": "DK9XR", "license_class": "A"}],
        "active_callsign": "DK9XR",
    }))  # kein api_token
    app = create_app()
    app.state.orchestrator = FakeOrchestrator()
    c = TestClient(app)
    assert c.get("/api/status").status_code != 401


def test_set_password_rejects_short(client_authed: TestClient) -> None:
    # zu kurzes Passwort wird vor jeglichem Orchestrator-Zugriff abgelehnt
    r = client_authed.post("/api/auth/token",
                           headers={"Authorization": f"Bearer {MASTER}"},
                           json={"token": "short"})
    assert r.status_code == 400


def test_set_password_requires_auth(client_authed: TestClient) -> None:
    r = client_authed.post("/api/auth/token", json={"token": "longenough123"})
    assert r.status_code == 401


LESEN = "lese-token-spiegel"


@pytest.fixture
def client_mit_lesetoken() -> TestClient:
    set_config_for_tests(AppConfig.model_validate({
        "operators": [{"callsign": "DK9XR", "license_class": "A"}],
        "active_callsign": "DK9XR",
        "api_token": MASTER,
        "api_read_token": LESEN,
    }))
    app = create_app()
    app.state.orchestrator = FakeOrchestrator()
    # Serverfehler als Antwort statt als Ausnahme: Hier zaehlt nur, ob die
    # Anfrage an der Auth vorbeikommt, nicht ob die Route ohne Datenbank laeuft.
    return TestClient(app, raise_server_exceptions=False)


def test_lesetoken_oeffnet_die_abrufe_des_spiegels(client_mit_lesetoken: TestClient) -> None:
    """Der Spiegel fuer dk9xr.de lag bis 28.09. mit dem Master-Token auf dem
    Webserver. Er braucht nur diese Abrufe — und bekommt nur diese."""
    h = {"Authorization": f"Bearer {LESEN}"}
    for pfad in ("/api/status", "/api/stats", "/api/map", "/api/psk/who-heard-me",
                 "/api/psk/tagesprofil?tage=7", "/api/qsl?limit=5", "/api/qsl/7/bild"):
        assert client_mit_lesetoken.get(pfad, headers=h).status_code != 401, pfad


def test_lesetoken_oeffnet_nichts_sonst(client_mit_lesetoken: TestClient) -> None:
    h = {"Authorization": f"Bearer {LESEN}"}
    for pfad in ("/api/config", "/api/auth/token", "/api/operators", "/api/log"):
        assert client_mit_lesetoken.get(pfad, headers=h).status_code == 401, pfad
    for pfad in ("/api/control/shutdown", "/api/control/stop", "/api/auth/token",
                 "/api/status"):
        assert client_mit_lesetoken.post(pfad, headers=h).status_code == 401, pfad
    # Kein Umweg ueber ein Suffix des erlaubten Musters
    assert client_mit_lesetoken.get("/api/qsl/7/bild/../../config", headers=h).status_code == 401


def test_lesetoken_steht_nicht_in_der_konfiguration(client_mit_lesetoken: TestClient) -> None:
    r = client_mit_lesetoken.get("/api/config", headers={"Authorization": f"Bearer {MASTER}"})
    assert r.status_code == 200
    assert LESEN not in r.text
