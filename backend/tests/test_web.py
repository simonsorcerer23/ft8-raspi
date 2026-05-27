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
        pile_up_calls: set = set()
        active_continent_hours: set = set()
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

    async def handle_shutdown(self) -> None:
        self.actions.append("shutdown")

    async def handle_reboot(self) -> None:
        self.actions.append("reboot")

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
    assert client.post("/api/control/shutdown").json()["detail"] == "shutdown"
    assert client.post("/api/control/reboot").json()["detail"] == "reboot"
    assert fake_orch.actions == ["cq", "stop", "panic", "reset", "shutdown", "reboot"]


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


# ------------------------------------------------------------------ v0.20.2 read-only debug endpoints
def test_pile_up_endpoint_empty(client: TestClient) -> None:
    """Leeres Set → leere Liste, count=0."""
    r = client.get("/api/pile-up")
    assert r.status_code == 200
    data = r.json()
    assert data["calls"] == []
    assert data["count"] == 0


def test_pile_up_endpoint_with_calls(client: TestClient, fake_orch) -> None:
    """Calls aus ctx werden sortiert geliefert."""
    fake_orch.state_machine.ctx.pile_up_calls = {"ZL9HR", "P5RYL", "BS7H"}
    r = client.get("/api/pile-up")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 3
    assert data["calls"] == ["BS7H", "P5RYL", "ZL9HR"]  # sortiert


def test_active_hours_endpoint_empty(client: TestClient) -> None:
    r = client.get("/api/active-hours")
    assert r.status_code == 200
    data = r.json()
    assert data["by_continent"] == {}
    assert data["total_active_buckets"] == 0
    assert 0 <= data["current_utc_hour"] < 24


def test_active_hours_endpoint_grouped_by_continent(client: TestClient, fake_orch) -> None:
    """Tuples aus ctx werden nach Continent gruppiert + sortiert."""
    fake_orch.state_machine.ctx.active_continent_hours = {
        ("EU", 8), ("EU", 19), ("EU", 14),
        ("AS", 6), ("AS", 22),
    }
    r = client.get("/api/active-hours")
    assert r.status_code == 200
    data = r.json()
    assert data["total_active_buckets"] == 5
    assert data["by_continent"]["EU"] == [8, 14, 19]
    assert data["by_continent"]["AS"] == [6, 22]


@pytest.fixture
def db_initialized():
    """Initialisiert eine In-Memory-DB + Tables fuer Endpoint-Tests
    die session_scope() nutzen."""
    import asyncio
    from ft8_appliance.db.session import init_engine, create_all
    init_engine(":memory:")
    asyncio.run(create_all())
    yield


def test_freq_reputation_endpoint_empty(
    client: TestClient, db_initialized
) -> None:
    """Endpoint antwortet 200 auch wenn DB leer ist."""
    r = client.get("/api/freq-reputation")
    assert r.status_code == 200
    data = r.json()
    assert "entries" in data
    assert isinstance(data["entries"], list)
    assert data["entries"] == []


def test_freq_reputation_endpoint_band_filter(
    client: TestClient, db_initialized
) -> None:
    """Band-Filter wird akzeptiert (kein 422)."""
    r = client.get("/api/freq-reputation?band=15m&min_attempts=5")
    assert r.status_code == 200


def test_freq_reputation_endpoint_sorts_by_success_rate(
    client: TestClient, db_initialized
) -> None:
    """Bins werden nach success_rate absteigend sortiert geliefert."""
    import asyncio
    from datetime import UTC, datetime
    from ft8_appliance.db import session_scope
    from ft8_appliance.db.models import FreqReputation

    async def seed():
        async with session_scope() as s:
            s.add(FreqReputation(
                band="15m", audio_bin_hz=1500,
                attempts=10, successes=5, last_used_at=datetime.now(UTC),
            ))
            s.add(FreqReputation(
                band="15m", audio_bin_hz=2000,
                attempts=10, successes=8, last_used_at=datetime.now(UTC),
            ))
            s.add(FreqReputation(
                band="20m", audio_bin_hz=1500,
                attempts=10, successes=2, last_used_at=datetime.now(UTC),
            ))
    asyncio.run(seed())

    r = client.get("/api/freq-reputation")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 3
    # bestes Bin (success_rate 0.8) zuerst
    assert entries[0]["audio_bin_hz"] == 2000
    assert entries[0]["band"] == "15m"
    assert entries[0]["success_rate"] == 0.8
    assert entries[-1]["success_rate"] == 0.2
