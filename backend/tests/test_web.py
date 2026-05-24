"""HTTP-level tests for the FastAPI app.

Uses FastAPI's TestClient so no port-binding / event-loop dance needed.
A fake orchestrator is attached so the status/control endpoints have
something concrete to talk to without the full async machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from ft8_appliance.gps import GpsSnapshot
from ft8_appliance.rig import RigSnapshot
from ft8_appliance.runtime import OrchestratorStatus
from ft8_appliance.statemachine import HardwareState
from ft8_appliance.web import create_app


class _FakeGps:
    snapshot = GpsSnapshot(mode=3, lat=49.5, lon=11.0, sats_seen=10, sats_used=8)


class _FakeStateMachine:
    class _State:
        name = "IDLE"
    class _Ctx:
        last_lock_reason = None
        cq_count = 0
    state = _State()
    ctx = _Ctx()


@dataclass
class FakeOrchestrator:
    """Minimal stand-in implementing only what the routes call."""

    state: str = "IDLE"
    cq_count: int = 0
    actions: list[str] = field(default_factory=list)
    # attrs the healthcheck route inspects
    gps: _FakeGps = field(default_factory=_FakeGps)
    _last_rig: RigSnapshot = field(
        default_factory=lambda: RigSnapshot(freq_hz=14_074_000, mode="USB", ptt=False)
    )
    state_machine: _FakeStateMachine = field(default_factory=_FakeStateMachine)

    def status(self) -> OrchestratorStatus:
        return OrchestratorStatus(
            callsign="DK9XR",
            state=self.state,
            last_lock_reason=None,
            cq_count=self.cq_count,
            current_qso_call=None,
            last_slot_index=0,
            last_decodes=0,
            auto_answer=False,
            tx_power_w=10,
            active_antenna="endfed_2040",
            worked_count=0,
            blacklist_count=0,
            rig=RigSnapshot(freq_hz=14_074_000, mode="USB", ptt=False),
            gps=GpsSnapshot(mode=3, lat=49.5, lon=11.0, sats_seen=10, sats_used=8),
        )

    def is_worked_before(self, _call):
        return False

    def is_blacklisted(self, _call):
        return False

    async def handle_skip_qso(self):
        self.actions.append("skip")
        self.state = "IDLE"

    async def handle_blacklist_add(self, call, reason=None):
        self.actions.append(f"bl_add:{call}")

    async def handle_blacklist_remove(self, call):
        self.actions.append(f"bl_del:{call}")

    async def handle_tx_power(self, watts):
        self.actions.append(f"power:{watts}")

    async def handle_set_antenna(self, name):
        self.actions.append(f"ant:{name}")

    def hardware_state(self) -> HardwareState:
        return HardwareState()

    async def handle_start_cq(self) -> None:
        self.actions.append("cq")
        self.state = "CQ_CALLING"
        self.cq_count += 1

    async def handle_stop(self) -> None:
        self.actions.append("stop")
        self.state = "IDLE"

    async def handle_panic(self) -> None:
        self.actions.append("panic")
        self.state = "IDLE"

    async def handle_reset_lock(self) -> None:
        self.actions.append("reset")

    async def handle_reply_to(self, decoded) -> None:  # type: ignore[no-untyped-def]
        self.actions.append(f"reply:{decoded.call_from}")


@pytest.fixture
def fake_orch() -> FakeOrchestrator:
    return FakeOrchestrator()


@pytest.fixture
def client(fake_orch: FakeOrchestrator) -> TestClient:
    app = create_app()
    app.state.orchestrator = fake_orch
    return TestClient(app)


# ------------------------------------------------------------------ basics
def test_root_responds(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "FT8" in r.text


def test_favicon_204(client: TestClient) -> None:
    assert client.get("/favicon.ico").status_code == 204


def test_openapi_visible(client: TestClient) -> None:
    r = client.get("/api/docs")
    assert r.status_code == 200


# ------------------------------------------------------------------ status / control / health
def test_status_endpoint_shape(client: TestClient) -> None:
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "IDLE"
    assert data["callsign"] == "DK9XR"
    assert data["rig"]["freq_hz"] == 14_074_000
    assert data["gps"]["mode"] == 3


def test_status_503_without_orchestrator() -> None:
    """If no orchestrator is attached, /api/status should return 503."""
    app = create_app()  # no state.orchestrator set
    client = TestClient(app)
    assert client.get("/api/status").status_code == 503


def test_healthcheck_shape(client: TestClient) -> None:
    r = client.get("/api/healthcheck")
    assert r.status_code == 200
    data = r.json()
    assert data["overall"] in {"green", "yellow", "red"}
    assert "system" in data["sections"]
    assert data["sections"]["system"]["status"] == "ok"


def test_control_endpoints(client: TestClient, fake_orch) -> None:
    assert client.post("/api/control/cq").json()["state"] == "CQ_CALLING"
    assert "cq" in fake_orch.actions
    assert client.post("/api/control/stop").json()["state"] == "IDLE"
    assert client.post("/api/control/panic").json()["ok"] is True
    assert client.post("/api/control/reset-lock").json()["ok"] is True
    assert fake_orch.actions == ["cq", "stop", "panic", "reset"]


def test_control_reply_endpoint(client: TestClient, fake_orch) -> None:
    r = client.post(
        "/api/control/reply",
        json={"call_from": "W1AW", "message": "CQ W1AW FN31", "grid": "FN31"},
    )
    assert r.status_code == 200
    assert fake_orch.actions == ["reply:W1AW"]


# ------------------------------------------------------------------ captive portal
@pytest.mark.parametrize(
    "path",
    [
        "/generate_204",
        "/gen_204",
    ],
)
def test_captive_generate_204_paths(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 204
    assert r.content == b""


def test_captive_apple_hotspot(client: TestClient) -> None:
    r = client.get("/hotspot-detect.html")
    assert r.status_code == 200
    assert "Success" in r.text


def test_captive_windows_ncsi(client: TestClient) -> None:
    r = client.get("/ncsi.txt")
    assert r.status_code == 200
    assert r.text == "Microsoft NCSI"


def test_captive_host_based_match(client: TestClient) -> None:
    """Probe with captive Host header but a path our app doesn't know
    should still get a sane response (204 fallback by middleware)."""
    r = client.get(
        "/something/we/dont/serve",
        headers={"Host": "connectivitycheck.gstatic.com"},
    )
    assert r.status_code == 204
