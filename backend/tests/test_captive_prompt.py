"""Captive-Portal-Prompt im AP-Fallback (2026-09-06).

Sebastian stand mit dem Handy vor dem Pi: SSID da, Passwort ging, aber
kein Portal — Androids Probe bekam 204 ("Internet ok"), und Chrome geht
HTTPS-first, also faengt auch kein getippter Name. Ohne die IP war er
aufgeschmissen. Jetzt: Redirect fuer die ersten Probes nach dem Erst-
kontakt eines AP-Clients, danach wieder 204 (Android bleibt im WLAN).
"""

from __future__ import annotations

import pytest

from ft8_appliance.web.routes import captive


@pytest.fixture(autouse=True)
def _clean_state():
    captive._first_seen.clear()
    yield
    captive._first_seen.clear()


def test_first_probes_from_an_ap_client_get_the_prompt() -> None:
    assert captive.captive_prompt_due("192.168.66.77", now=1000.0) is True
    assert captive.captive_prompt_due("192.168.66.77", now=1000.0 + 120) is True


def test_after_the_window_the_same_client_gets_204_again() -> None:
    assert captive.captive_prompt_due("192.168.66.77", now=1000.0) is True
    assert captive.captive_prompt_due("192.168.66.77", now=1000.0 + captive.CAPTIVE_PROMPT_S + 1) is False


def test_a_client_returning_hours_later_is_a_new_contact() -> None:
    assert captive.captive_prompt_due("192.168.66.77", now=1000.0) is True
    assert captive.captive_prompt_due("192.168.66.77", now=1000.0 + 600) is False
    assert captive.captive_prompt_due("192.168.66.77", now=1000.0 + 7200) is True


def test_lan_and_tailscale_clients_never_get_the_prompt() -> None:
    """Das 204 fuer Nicht-AP-Clients bleibt — der dokumentierte Grund
    (Android wirft das WLAN sonst ab) gilt dort weiter."""
    for ip in ("192.168.0.153", "100.96.43.23", "127.0.0.1", "testclient", None):
        assert captive.captive_prompt_due(ip, now=5.0) is False


def test_foreign_host_detection() -> None:
    assert captive._is_foreign_host("connectivitycheck.gstatic.com") is True
    assert captive._is_foreign_host("irgendwas.de:80") is True
    assert captive._is_foreign_host("192.168.66.1") is False
    assert captive._is_foreign_host("ft8.local") is False
    assert captive._is_foreign_host("") is False


def test_probe_paths_and_hosts_still_recognised() -> None:
    assert captive.is_captive_probe("connectivitycheck.gstatic.com", "/generate_204")
    assert captive.is_captive_probe("example.com", "/generate_204")
    assert not captive.is_captive_probe("192.168.66.1", "/api/status")


def test_probe_from_a_non_ap_client_is_still_204() -> None:
    """Testclient kommt als 'testclient' — kein AP-Client — also 204 wie bisher."""
    from fastapi.testclient import TestClient

    from ft8_appliance.web.app import create_app

    with TestClient(create_app()) as c:
        r = c.get("/generate_204", headers={"Host": "connectivitycheck.gstatic.com"})
        assert r.status_code == 204


def test_probe_from_an_ap_client_redirects_to_the_ui() -> None:
    from fastapi.testclient import TestClient

    from ft8_appliance.web.app import create_app

    with TestClient(create_app(), client=("192.168.66.77", 40000)) as c:
        r = c.get("/generate_204", headers={"Host": "connectivitycheck.gstatic.com"},
                  follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == captive.CAPTIVE_UI_URL
        # Tippt der Nutzer irgendeine http-Adresse: ebenfalls zur UI.
        r = c.get("/irgendwas", headers={"Host": "beispiel.de"}, follow_redirects=False)
        assert r.status_code == 302
        # Die UI selbst (Host = unsere IP) bleibt unangetastet.
        r = c.get("/", headers={"Host": "192.168.66.1"}, follow_redirects=False)
        assert r.status_code in (200, 404)
