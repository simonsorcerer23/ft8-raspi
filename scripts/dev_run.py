"""Run the full appliance stack on the dev workstation — without a Pi.

Spins up:
  * MockRigctld on a random localhost port
  * MockGpsd on a random localhost port
  * an Orchestrator wired to the mocks
  * a DecodeSource that fakes a couple of stations on the band
  * the FastAPI app on http://127.0.0.1:8000
  * a *seeded* in-memory SQLite — pre-populated QSO log + heard cache so
    the Log/Map/ADIF tabs show realistic content right away.

Use to:
  - Click through the frontend (built by `npm run build` into
    backend/.../static, served by FastAPI on :8000)
  - Manually verify control endpoints
  - Watch the log stream tell the story of what the orchestrator does

Stop with Ctrl-C.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn

# Make sure ``tests/`` is importable so we can pull in the mocks
ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))

from ft8_appliance.config import (  # noqa: E402
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
    set_config_for_tests,
)
from ft8_appliance.db import create_all, init_engine, session_scope  # noqa: E402
from ft8_appliance.db.models import Heard, Qso  # noqa: E402
from ft8_appliance.gps import GpsdClient  # noqa: E402
from ft8_appliance.rig import RigctldClient  # noqa: E402
from ft8_appliance.runtime import Orchestrator, SlotClock, SlotTick  # noqa: E402
from ft8_appliance.statemachine import DecodedMsg  # noqa: E402
from ft8_appliance.util.band_simulator import FT8BandSimulator  # noqa: E402
from ft8_appliance.web import create_app  # noqa: E402
from tests.mocks.mock_gpsd import MockGpsd  # noqa: E402
from tests.mocks.mock_rigctld import MockRigctld  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
log = logging.getLogger("dev_run")


# A "decode source" that fakes a couple of stations talking on the band.
# Every 6 slots it pretends W1AW just answered our CQ, then sends a report,
# then RR73 — so we can verify the full QSO path lights up the UI.
class FakeOtherStations:
    def __init__(self) -> None:
        self.tick = 0

    async def __call__(self, slot: SlotTick) -> list[DecodedMsg]:
        self.tick += 1
        now = datetime.now(UTC)
        decodes: list[DecodedMsg] = []
        # Always pretend a couple of random stations are CQ-ing
        decodes.append(_d("JA1XYZ", None, "CQ JA1XYZ PM95", grid="PM95",
                          snr=-8, dt=0.2, freq=1230, now=now))
        decodes.append(_d("VK6ABC", None, "CQ VK6ABC OF87", grid="OF87",
                          snr=-15, dt=0.5, freq=2310, now=now))

        # Scripted little QSO with W1AW every 6 slots
        phase = self.tick % 6
        if phase == 1:
            decodes.append(_d("W1AW", "DK9XR", "DK9XR W1AW FN31",
                              grid="FN31", snr=-7, dt=0.1, freq=1750, now=now))
        elif phase == 2:
            decodes.append(_d("W1AW", "DK9XR", "DK9XR W1AW -12",
                              snr=-12, dt=0.1, freq=1750, now=now))
        elif phase == 3:
            decodes.append(_d("W1AW", "DK9XR", "DK9XR W1AW RR73",
                              snr=-10, dt=0.1, freq=1750, now=now))
        return decodes


def _d(call_from, call_to, message, *, grid=None, snr=-10, dt=0.2, freq=1500, now=None):
    return DecodedMsg(
        ts=now or datetime.now(UTC),
        call_from=call_from, call_to=call_to, grid=grid,
        message=message, snr_db=snr, dt_s=dt,
        freq_offset_hz=freq, band="20m",
    )


# ---------------------------------------------------------------------------
# Demo data — gives the UI something interesting to render right after boot
# ---------------------------------------------------------------------------
DEMO_QSOS = [
    # (call, band, freq_hz, grid, rst_sent, rst_rcvd, hours_ago, power_w)
    ("W1AW",   "20m", 14_076_350,  "FN31",  -7,  -12, 0.25,  10),
    ("JA1XYZ", "20m", 14_075_120,  "PM95",  -10, -15, 0.75,  10),
    ("VK6ABC", "20m", 14_076_010,  "OF87",  -18, -16, 1.5,   25),
    ("EA8DX",  "20m", 14_074_900,  "IL18",  -5,  -8,  2.5,   10),
    ("K2LE",   "40m",  7_076_200,  "FN42",  -14, -11, 5.0,   10),
    ("OH8X",   "40m",  7_075_800,  "KP54",  -8,  -10, 6.5,   10),
    ("3Y0J",   "20m", 14_076_500,  "GD52",  -22, -23, 20.0,  25),  # rare DX
    ("DL3MAX", "80m",  3_575_800,  "JO31",  +2,  -1,  26.0,  5),
    ("F5RZJ",  "20m", 14_074_300,  "JN13",  -9,  -7,  48.0,  10),
    ("G0XYZ",  "40m",  7_076_700,  "IO91",  -11, -14, 50.0,  10),
    ("PA3ABC", "20m", 14_075_500,  "JO22",  -6,  -5,  72.0,  10),
    ("ON4QX",  "20m", 14_074_200,  "JO20",  -4,  -3,  72.5,  10),
]

# Stations the rig has heard recently but not worked (yet)
DEMO_HEARD_ONLY = [
    # (call, grid, snr, count, minutes_ago)
    ("UA9CDV", "MO04", -12, 4,   2),
    ("ZS6CCY", "KG44", -16, 2,   5),
    ("LU1AEE", "GF05", -14, 1,  10),
    ("CE3SAD", "FF46", -19, 1,  12),
    ("BV2KI",  "PL05", -20, 1,  15),
    ("ZL2IFB", "RE78", -18, 2,  20),
    ("9V1XX",  "OJ11", -22, 1,  30),
]


async def seed_demo_data() -> None:
    """Pre-populate the in-memory DB with a believable QSO log + heard list
    so the Log, Map and ADIF tabs aren't empty on first boot."""
    now = datetime.now(UTC)
    # Two operator locations for the trip-map demo: home (JN58td ≈ Nuremberg)
    # for recent QSOs, and an "Italy vacation" position for QSOs >24h old.
    HOME = (49.4639, 11.0997)
    ITALY = (44.4949, 11.3426)  # Bologna-ish
    async with session_scope() as s:
        for call, band, freq, grid, rst_s, rst_r, hours_ago, pwr in DEMO_QSOS:
            start = now - timedelta(hours=hours_ago)
            end = start + timedelta(seconds=90)
            lat, lon = HOME if hours_ago < 24 else ITALY
            s.add(Qso(
                call=call, band=band, freq_hz=freq, mode="FT8",
                rst_sent=rst_s, rst_rcvd=rst_r, grid_rcvd=grid,
                qso_start=start, qso_end=end,
                my_grid="JN58td", my_power_w=pwr,
                swr_avg=1.3 + (hash(call) % 50) / 100,
                my_lat=lat, my_lon=lon,
            ))
        # Heard but not worked
        for call, grid, snr, count, mins_ago in DEMO_HEARD_ONLY:
            s.add(Heard(
                call=call, grid=grid,
                last_seen=now - timedelta(minutes=mins_ago),
                count=count, best_snr=snr,
            ))
        # Also seed heard rows for the worked ones (worked = also heard)
        for call, _band, _freq, grid, _rs, _rr, hours_ago, _pwr in DEMO_QSOS[:8]:
            s.add(Heard(
                call=call, grid=grid,
                last_seen=now - timedelta(hours=hours_ago),
                count=1, best_snr=-10,
            ))


async def main() -> None:
    # Set up mocks first so the clients have something to connect to
    rig_mock = MockRigctld()
    gps_mock = MockGpsd()
    await rig_mock.__aenter__()
    await gps_mock.__aenter__()
    log.info("MockRigctld up on port %d", rig_mock.port)
    log.info("MockGpsd up on port %d", gps_mock.port)

    # In-memory DB so the orchestrator's QSO writes don't hit disk
    init_engine(":memory:")
    await create_all()
    await seed_demo_data()

    cfg = AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[
            BandConfig(name="20m", freq_khz=14074, antenna="endfed_2040"),
            BandConfig(name="40m", freq_khz=7074,  antenna="endfed_2040"),
            BandConfig(name="80m", freq_khz=3573,  antenna="doublet_8040"),
        ],
        antennas=[
            AntennaConfig(name="endfed_2040", bands=["20m", "40m"]),
            AntennaConfig(name="doublet_8040", bands=["80m", "40m"]),
        ],
        operating=OperatingConfig(),
    )
    set_config_for_tests(cfg)  # so /api/config GET works

    rig = RigctldClient(host="127.0.0.1", port=rig_mock.port)
    gps = GpsdClient(host="127.0.0.1", port=gps_mock.port)

    simulator = FT8BandSimulator(
        my_call=cfg.operator.callsign,
        my_grid=cfg.operator.default_locator or "JN58td",
    )
    orch = Orchestrator(
        config=cfg, rig=rig, gps=gps,
        decode_source=simulator,
        slot_clock=SlotClock(),
        db_enabled=True,
    )
    # Hook: let the simulator see our TX so its stations can react.
    _orig_tx = orch._do_tx_message  # noqa: SLF001
    async def _hooked_tx(payload):
        simulator.notify_our_tx(payload.get("message", ""),
                                payload.get("freq_offset_hz", 1500))
        await _orig_tx(payload)
    orch._do_tx_message = _hooked_tx  # noqa: SLF001
    orch._action_handlers["TX_MESSAGE"] = _hooked_tx  # noqa: SLF001
    await orch.start()
    log.info("Orchestrator running. Callsign=%s, %d sim stations on band",
             cfg.operator.callsign, len(simulator.population))

    app = create_app(orchestrator=orch)

    cfg_uv = uvicorn.Config(
        app, host="127.0.0.1", port=8000, log_level="info", access_log=False
    )
    server = uvicorn.Server(cfg_uv)
    log.info("--------------------------------------------------------")
    log.info("  Browser: http://127.0.0.1:8000/api/docs")
    log.info("  Status:  curl http://127.0.0.1:8000/api/status | jq")
    log.info("  Health:  curl http://127.0.0.1:8000/api/healthcheck | jq")
    log.info("  SSE:     curl -N http://127.0.0.1:8000/sse/decodes")
    log.info("  Frontend dev: cd frontend && npm run dev  (then :5173)")
    log.info("--------------------------------------------------------")
    try:
        await server.serve()
    finally:
        await orch.stop()
        await gps_mock.__aexit__(None, None, None)
        await rig_mock.__aexit__(None, None, None)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
