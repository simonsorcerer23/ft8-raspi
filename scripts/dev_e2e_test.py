"""Headless end-to-end self-test of the controller — no Pi, no browser.

Boots a full Orchestrator against mock hardware, scripts an incoming
QSO, asserts the appliance walks the state machine to LOG_QSO and writes
the QSO row to the in-memory DB.

Exit code 0 = green, 1 = something broke. Useful for CI or sanity checks
after refactors. Runs in ~2 seconds.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from ft8_appliance.config import (  # noqa: E402
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.db import create_all, init_engine, session_scope  # noqa: E402
from ft8_appliance.db.models import Qso  # noqa: E402
from ft8_appliance.gps import GpsdClient  # noqa: E402
from ft8_appliance.rig import RigctldClient  # noqa: E402
from ft8_appliance.runtime import FakeSlotClock, Orchestrator, SlotTick  # noqa: E402
from ft8_appliance.statemachine import DecodedMsg  # noqa: E402
from tests.mocks.mock_gpsd import MockGpsd  # noqa: E402
from tests.mocks.mock_rigctld import MockRigctld  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")


def _d(call_from, call_to, message, **kw):
    return DecodedMsg(
        ts=datetime.now(UTC),
        call_from=call_from, call_to=call_to,
        grid=kw.get("grid"),
        message=message,
        snr_db=kw.get("snr", -10),
        dt_s=kw.get("dt", 0.2),
        freq_offset_hz=kw.get("freq", 1500),
        band=kw.get("band", "20m"),
    )


def ok(msg: str) -> None:
    print(f"\033[32m✓\033[0m {msg}")


def fail(msg: str) -> None:
    print(f"\033[31m✗\033[0m {msg}")
    sys.exit(1)


class Script:
    def __init__(self, batches: list[list[DecodedMsg]]) -> None:
        self.batches = batches
        self.i = 0

    async def __call__(self, _tick: SlotTick) -> list[DecodedMsg]:
        out = self.batches[self.i] if self.i < len(self.batches) else []
        self.i += 1
        return out


async def main() -> None:
    init_engine(":memory:")
    await create_all()

    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(port=mock_rig.port)
        gps = GpsdClient(port=mock_gps.port)
        cfg = AppConfig(
            operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
            bands=[BandConfig(name="20m", freq_khz=14074, antenna="e")],
            antennas=[AntennaConfig(name="e", bands=["20m"])],
            operating=OperatingConfig(),
        )

        caller_freq = 2350
        scripts = [
            [],
            [_d("W1AW", "DK9XR", "DK9XR W1AW FN31", grid="FN31", snr=-7, freq=caller_freq)],
            [_d("W1AW", "DK9XR", "DK9XR W1AW -12", snr=-12, freq=caller_freq)],
            [_d("W1AW", "DK9XR", "DK9XR W1AW RR73", freq=caller_freq)],
        ]

        orch = Orchestrator(
            config=cfg, rig=rig, gps=gps,
            decode_source=Script(scripts),
            slot_clock=FakeSlotClock(count=0),
            db_enabled=True,
        )
        await orch.start()
        await asyncio.sleep(0.2)

        # Sanity: gpsd snapshot populated
        if orch.gps.snapshot.mode != 3:
            fail(f"gpsd snapshot not ready (mode={orch.gps.snapshot.mode})")
        ok("gpsd mock TPV received, mode=3")

        # Sanity: rig snapshot populated
        snap = orch.status()
        if snap.rig.freq_hz != 14_074_000:
            fail(f"rig freq wrong: {snap.rig.freq_hz}")
        ok(f"rig snapshot freq={snap.rig.freq_hz} mode={snap.rig.mode}")

        # 1) start CQ
        await orch.handle_start_cq()
        if orch.status().state != "CQ_CALLING":
            fail(f"after start_cq, state={orch.status().state}, expected CQ_CALLING")
        ok("CQ_CALLING entered")

        # 2) drive 4 slots — the QSO sequence
        for i, _ in enumerate(scripts):
            await orch.process_slot(SlotTick(
                index=i, posix=1_700_000_000.0 + i * 15,
                utc_start=datetime.fromtimestamp(1_700_000_000.0 + i * 15, tz=UTC),
            ))

        snap = orch.status()
        if snap.state != "IDLE":
            fail(f"after full QSO, state={snap.state}, expected IDLE")
        ok("QSO completed back to IDLE")

        # 3) Geminis Freq-Bug: every in-QSO TX must have used the caller's freq
        in_qso_tx = [
            a for a in orch._action_log  # noqa: SLF001
            if a.kind == "TX_MESSAGE" and a.payload.get("kind") != "cq"
        ]
        if not in_qso_tx:
            fail("no in-QSO TX_MESSAGE actions emitted")
        bad = [a for a in in_qso_tx if a.payload.get("freq_offset_hz") != caller_freq]
        if bad:
            fail(f"{len(bad)}/{len(in_qso_tx)} in-QSO TX on wrong freq: "
                 f"{[a.payload['freq_offset_hz'] for a in bad]}")
        ok(f"all {len(in_qso_tx)} in-QSO TXs on caller freq {caller_freq} Hz")

        # 4) QSO row in DB?
        async with session_scope() as s:
            rows = list((await s.execute(select(Qso))).scalars())
        if not rows:
            fail("no QSO row written to DB")
        qso = rows[0]
        if qso.call != "W1AW":
            fail(f"QSO row wrong call: {qso.call}")
        if qso.grid_rcvd != "FN31":
            fail(f"QSO row wrong grid: {qso.grid_rcvd}")
        if qso.rst_rcvd != -12:
            fail(f"QSO row wrong rst_rcvd: {qso.rst_rcvd}")
        # On-air freq = rig dial (14_074_000) + audio offset (2350) — the
        # audit-found bug would have left this at the dial only.
        expected_freq = 14_074_000 + caller_freq
        if qso.freq_hz != expected_freq:
            fail(f"QSO row freq_hz={qso.freq_hz}, expected {expected_freq} "
                 f"(dial + audio offset)")
        ok(f"QSO row in DB: {qso.call} {qso.band} freq={qso.freq_hz}Hz "
           f"grid={qso.grid_rcvd} sent={qso.rst_sent} rcvd={qso.rst_rcvd}")

        # 5) Panic stops PTT on the rig
        mock_rig.state.ptt = True
        await orch.handle_panic()
        if mock_rig.state.ptt:
            fail("panic did not turn off PTT on rig")
        ok("panic dropped PTT on rig")

        await orch.stop()

    print()
    print("\033[1;32mAll checks green.\033[0m")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
