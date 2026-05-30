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


def test_action_token_only_for_control_paths(client_authed: TestClient) -> None:
    # Action-Token darf einen Control-Toggle (POST /api/control/stop)
    a = client_authed.post("/api/control/stop", headers={"Authorization": f"Bearer {ACTION}"})
    assert a.status_code != 401
    # aber NICHT die Config lesen
    c = client_authed.get("/api/config", headers={"Authorization": f"Bearer {ACTION}"})
    assert c.status_code == 401
    # und NICHT shutdown (nicht in ACTION_PATHS)
    s = client_authed.post("/api/control/shutdown", headers={"Authorization": f"Bearer {ACTION}"})
    assert s.status_code == 401


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
