"""Audit 2026-09-06 A4: der AP-Fallback wurde von nichts ausgeloest.

``ft8-ap-fallback.service`` sagte "Started by ft8-controller when no
upstream WiFi has been reachable" — im Controller gab es diesen Aufruf
nie. Sebastian stand am 06.09. mit dem Pi bei seinem Vater: kein
bekanntes WLAN, kein AP, nur das LAN-Kabel half. Dazu kam, dass Debians
``hostapd@``-Template die Instanz an ein Netzgeraet namens ``ft8-ap``
band und den AP 5 ms nach dem Start wieder stoppte.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.runtime.orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parents[2]


def _stub(*, upstream: bool, ap_active: bool = False, delay: int = 60) -> SimpleNamespace:
    stub = SimpleNamespace(
        config=SimpleNamespace(network=SimpleNamespace(fallback_delay_s=delay), demo_mode=False),
        _ap_fallback_offline_since=None,
        _has_upstream_connection=AsyncMock(return_value=upstream),
        ap_fallback_is_active=AsyncMock(return_value=ap_active),
        set_ap_fallback=AsyncMock(),
    )
    stub._ap_fallback_tick = lambda: Orchestrator._ap_fallback_tick(stub)
    return stub


# ================================================================== Watchdog


@pytest.mark.asyncio
async def test_with_upstream_nothing_happens() -> None:
    stub = _stub(upstream=True)
    await stub._ap_fallback_tick()
    stub.set_ap_fallback.assert_not_called()
    assert stub._ap_fallback_offline_since is None


@pytest.mark.asyncio
async def test_first_offline_tick_only_starts_the_clock() -> None:
    stub = _stub(upstream=False)
    await stub._ap_fallback_tick()
    stub.set_ap_fallback.assert_not_called()
    assert stub._ap_fallback_offline_since is not None


@pytest.mark.asyncio
async def test_ap_starts_after_fallback_delay() -> None:
    stub = _stub(upstream=False, delay=60)
    stub._ap_fallback_offline_since = time.monotonic() - 61.0
    await stub._ap_fallback_tick()
    stub.set_ap_fallback.assert_awaited_once_with(True)
    assert stub._ap_fallback_offline_since is None


@pytest.mark.asyncio
async def test_ap_does_not_start_before_the_delay() -> None:
    stub = _stub(upstream=False, delay=60)
    stub._ap_fallback_offline_since = time.monotonic() - 30.0
    await stub._ap_fallback_tick()
    stub.set_ap_fallback.assert_not_called()


@pytest.mark.asyncio
async def test_upstream_returning_resets_the_clock() -> None:
    stub = _stub(upstream=False)
    stub._ap_fallback_offline_since = time.monotonic() - 30.0
    stub._has_upstream_connection.return_value = True
    await stub._ap_fallback_tick()
    assert stub._ap_fallback_offline_since is None


@pytest.mark.asyncio
async def test_running_ap_is_never_restarted() -> None:
    """Im AP-Modus ist wlan0 unmanaged, also 'kein Upstream' — das darf
    nicht alle 60 s einen neuen Start ausloesen."""
    stub = _stub(upstream=False, ap_active=True)
    stub._ap_fallback_offline_since = time.monotonic() - 600.0
    await stub._ap_fallback_tick()
    stub.set_ap_fallback.assert_not_called()
    assert stub._ap_fallback_offline_since is None


def test_nmcli_parsing_counts_only_ethernet_and_wifi() -> None:
    import asyncio

    from ft8_appliance.util import network as net

    async def run(lines: str):
        async def fake_run(cmd, **kw):
            return 0, lines, ""
        orig = net._run
        net._run = fake_run  # type: ignore[assignment]
        try:
            return await Orchestrator._has_upstream_connection(SimpleNamespace())
        finally:
            net._run = orig  # type: ignore[assignment]

    # So sah es am 06.09. aus: Kabel dran, Tailscale "connected (externally)",
    # wlan0 disconnected.
    cable = "eth0:ethernet:connected:Wired connection 1\ntailscale0:tun:connected (externally):tailscale0\nlo:loopback:connected (externally):lo\nwlan0:wifi:disconnected:\n"
    assert asyncio.run(run(cable)) is True
    # Nur Tailscale + lo "connected" — das ist KEIN Upstream.
    only_tun = "tailscale0:tun:connected (externally):tailscale0\nlo:loopback:connected (externally):lo\nwlan0:wifi:disconnected:\neth0:ethernet:unavailable:\n"
    assert asyncio.run(run(only_tun)) is False
    # AP-Modus: wlan0 unmanaged, kein Kabel.
    ap = "wlan0:wifi:unmanaged:\neth0:ethernet:unavailable:\nlo:loopback:connected (externally):lo\n"
    assert asyncio.run(run(ap)) is False


@pytest.mark.asyncio
async def test_active_check_asks_hostapd_not_the_oneshot_unit() -> None:
    """Die oneshot-Unit bleibt 'active', wenn der AP am Unit-Manager vorbei
    gestoppt wird — dann duerfte der Watchdog nie wieder starten."""
    from ft8_appliance.util import network as net

    seen: list[list[str]] = []

    async def fake_run(cmd, **kw):
        seen.append(cmd)
        return 0, "inactive\n", ""

    orig = net._run
    net._run = fake_run  # type: ignore[assignment]
    try:
        assert await Orchestrator.ap_fallback_is_active(SimpleNamespace()) is False
    finally:
        net._run = orig  # type: ignore[assignment]
    assert seen and "ft8-hostapd.service" in seen[0]


def test_stop_script_only_flushes_an_unmanaged_wlan0() -> None:
    stop = (ROOT / "deploy/scripts/stop-ap-fallback.sh").read_text()
    assert ':unmanaged"' in stop and "ip addr flush" in stop


def test_watchdog_is_spawned_in_start() -> None:
    import inspect

    src = inspect.getsource(Orchestrator.start)
    assert 'self._spawn(self._ap_fallback_loop(), name="ap-fallback")' in src


# ================================================================== API


@pytest.mark.asyncio
async def test_start_endpoint_calls_the_orchestrator() -> None:
    from ft8_appliance.web.routes.network import start_ap_fallback, stop_ap_fallback

    orch = SimpleNamespace(set_ap_fallback=AsyncMock(), ap_fallback_is_active=AsyncMock(return_value=True))
    r = await start_ap_fallback(orch=orch)
    orch.set_ap_fallback.assert_awaited_once_with(True)
    assert r.ok and r.active is True
    orch.ap_fallback_is_active.return_value = False
    r = await stop_ap_fallback(orch=orch)
    orch.set_ap_fallback.assert_awaited_with(False)
    assert r.active is False


# ================================================================== Deploy


def test_ap_scripts_use_our_own_hostapd_unit() -> None:
    """Debians hostapd@-Template bindet die Instanz an ein Netzgeraet mit dem
    Instanznamen — 'ft8-ap' ist keins, der AP starb 5 ms nach dem Start."""
    start = (ROOT / "deploy/scripts/start-ap-fallback.sh").read_text()
    stop = (ROOT / "deploy/scripts/stop-ap-fallback.sh").read_text()
    # Die Template-Instanz darf nirgends mehr angesprochen werden (Kommentare
    # duerfen das Template erwaehnen, um zu erklaeren, warum nicht).
    assert "hostapd@ft8-ap" not in start and "hostapd@ft8-ap" not in stop
    assert "ft8-hostapd.service" in start and "ft8-hostapd.service" in stop
    unit = (ROOT / "deploy/systemd/ft8-hostapd.service").read_text()
    directives = [ln.strip() for ln in unit.splitlines() if ln.strip() and not ln.startswith("#")]
    assert not any(d.startswith("BindsTo=") for d in directives)
    assert "ExecStart=/usr/sbin/hostapd /etc/hostapd/ft8-ap.conf" in directives


def test_start_script_waits_for_networkmanager_to_release_wlan0() -> None:
    start = (ROOT / "deploy/scripts/start-ap-fallback.sh").read_text()
    assert ':unmanaged"' in start  # Poll-Schleife statt blindem Weiterlaufen
    assert "is-active --quiet ft8-hostapd" in start  # oneshot scheitert laut


def test_sudoers_allow_the_controller_to_toggle_the_ap() -> None:
    for name in ("deploy/sudoers.d/ft8-self-update.in", "deploy/sudoers.d/ft8-self-update"):
        s = (ROOT / name).read_text()
        for verb in ("start", "stop"):
            assert f"/bin/systemctl {verb} ft8-ap-fallback.service" in s, (name, verb)
        assert "ft8-hostapd.service /etc/systemd/system/ft8-hostapd.service" in s, name


def test_install_and_self_update_ship_the_unit() -> None:
    assert "ft8-hostapd.service" in (ROOT / "deploy/install.sh").read_text()
    su = (ROOT / "scripts/self-update.sh").read_text()
    assert su.count("ft8-hostapd.service") >= 2  # beide Sync-Zweige
