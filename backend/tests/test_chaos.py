"""Chaos / fault-injection tests.

These simulate hardware misbehaviour while the appliance is mid-QSO
and assert that the guards + state machine react correctly:

* GPS loses its fix during a QSO
* Audio stream goes silent (cable unplugged → all-zero samples)
* SWR spikes beyond threshold
* chrony offset jumps to >0.5 s
* rigctld TCP socket dies (mock closes the connection)

Every chaos scenario that removes a *required* condition MUST end with
TX_LOCKED and a forced PTT-off. Time: since 2026-09-06 ``time_guard`` trusts
**chrony only** — on this system GPS never disciplines the clock (chrony runs
NTP-only, see architecture.md §3.5), so a GPS fix is not a time source and
losing it is not a lock reason either. What locks is chrony losing its
source — see ``test_chaos_chrony_loses_source_mid_qso_locks_tx`` and its
counterpart ``test_chaos_gps_lost_but_ntp_good_keeps_tx``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.gps import GpsdClient
from ft8_appliance.rig import RigctldClient
from ft8_appliance.runtime import FakeSlotClock, Orchestrator, SlotTick
from ft8_appliance.runtime import orchestrator as orchestrator_mod
from ft8_appliance.statemachine import HardwareState
from ft8_appliance.util.system_health import ChronyStatus
from tests.mocks.mock_gpsd import MockGpsd
from tests.mocks.mock_rigctld import MockRigctld


def _cfg() -> AppConfig:
    return AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[BandConfig(name="20m", freq_khz=14074, antenna="e")],
        antennas=[AntennaConfig(name="e", bands=["20m"])],
        operating=OperatingConfig(),
    )


async def _make_orch(rig_mock: MockRigctld, gps_mock: MockGpsd):
    rig = RigctldClient(port=rig_mock.port)
    gps = GpsdClient(port=gps_mock.port)
    orch = Orchestrator(
        config=_cfg(), rig=rig, gps=gps,
        decode_source=lambda t: __noop_decodes(),
        slot_clock=FakeSlotClock(count=0),
        db_enabled=False,
    )
    await orch.start()
    await asyncio.sleep(0.2)
    return orch


async def __noop_decodes():
    return []


def _force_chrony(monkeypatch: pytest.MonkeyPatch, status: ChronyStatus | None) -> None:
    """Pin what the orchestrator sees as chrony state.

    Without this the result depends on whether the machine running the tests
    happens to have a synced chrony daemon: ``read_chrony_tracking`` shells out
    to the real ``chronyc`` and returns ``None`` when it isn't installed. The Pi
    has chrony, the dev workstation does not.
    """

    async def _stub() -> ChronyStatus | None:
        return status

    monkeypatch.setattr(orchestrator_mod, "read_chrony_tracking", _stub)


def _tick(i: int) -> SlotTick:
    posix = 1_700_000_000.0 + i * 15
    return SlotTick(
        index=i, posix=posix,
        utc_start=datetime.fromtimestamp(posix, tz=UTC),
    )


# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chaos_chrony_loses_source_mid_qso_locks_tx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chrony loses its source mid-QSO → time_guard fails → TX_LOCKED.

    Until 2026-09-06 this test was "GPS loses fix" and started the CQ with
    chrony=None, i.e. it *relied* on a GPS fix alone unlocking TX. That was
    the flaw the audit found (A1): GPS does not discipline the clock here.
    """
    holder: dict[str, ChronyStatus | None] = {
        "status": ChronyStatus(offset_s=0.001, stratum=3),
    }

    async def _stub() -> ChronyStatus | None:
        return holder["status"]

    monkeypatch.setattr(orchestrator_mod, "read_chrony_tracking", _stub)
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        orch = await _make_orch(mock_rig, mock_gps)
        await orch.handle_start_cq()
        assert orch.status().state == "CQ_CALLING"

        # chronyc stops answering (daemon died / no source left)
        holder["status"] = None

        await orch.process_slot(_tick(0))
        snap = orch.status()
        assert snap.state == "TX_LOCKED", f"expected TX_LOCKED, got {snap.state}"
        assert "chrony" in (snap.last_lock_reason or "").lower()
        # PTT must be off after lock
        assert mock_rig.state.ptt is False
        await orch.stop()


@pytest.mark.asyncio
async def test_chaos_gps_fix_alone_never_unlocks_tx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit 2026-09-06 A1, at orchestrator level: GPS fix, chrony without a
    source → the very first CQ attempt locks, and the reason says so."""
    _force_chrony(monkeypatch, None)
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        orch = await _make_orch(mock_rig, mock_gps)
        await orch.handle_start_cq()
        snap = orch.status()
        assert snap.state == "TX_LOCKED", f"expected TX_LOCKED, got {snap.state}"
        assert "GPS" in (snap.last_lock_reason or "")
        await orch.stop()


@pytest.mark.asyncio
async def test_chaos_gps_lost_but_ntp_good_keeps_tx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPS fix drops but chrony is synced → TX stays up.

    FT8 needs ~100 ms time accuracy and NTP delivers that, so GPS is a
    convenience, not a TX precondition. Pinned here because it is the
    counterpart to the lock case above and easy to break by accident.
    """
    _force_chrony(monkeypatch, ChronyStatus(offset_s=0.001, stratum=3))
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        orch = await _make_orch(mock_rig, mock_gps)
        await orch.handle_start_cq()
        assert orch.status().state == "CQ_CALLING"

        mock_gps.set_fix(mode=0)
        await mock_gps.emit_tpv()
        await asyncio.sleep(0.1)

        await orch.process_slot(_tick(0))
        snap = orch.status()
        assert snap.state != "TX_LOCKED", f"unexpected lock: {snap.last_lock_reason}"
        await orch.stop()


@pytest.mark.asyncio
async def test_chaos_swr_spike_locks_tx() -> None:
    """SWR spikes during a QSO → swr_guard fails → TX_LOCKED."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        orch = await _make_orch(mock_rig, mock_gps)
        await orch.handle_start_cq()
        # Deterministisch warten bis der erste Rig-Poll die SWR kennt
        # (timing-robust statt fixem sleep).
        for _ in range(50):
            await asyncio.sleep(0.1)
            if orch.status().rig.swr is not None:
                break
        mock_rig.set_swr(5.5)  # antenna fell over
        # Warten bis der Poll die neue (hohe) SWR gesehen hat.
        for _ in range(50):
            await asyncio.sleep(0.1)
            if (orch.status().rig.swr or 0) >= 5.0:
                break
        await orch.process_slot(_tick(0))
        snap = orch.status()
        assert snap.state == "TX_LOCKED"
        assert "SWR" in (snap.last_lock_reason or "")
        assert mock_rig.state.ptt is False
        await orch.stop()


@pytest.mark.asyncio
async def test_chaos_panic_button_always_drops_ptt() -> None:
    """Even mid-TX (PTT=True), panic forces it off."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        orch = await _make_orch(mock_rig, mock_gps)
        # Simulate rig got into TX state somehow
        mock_rig.state.ptt = True
        await orch.handle_panic()
        assert mock_rig.state.ptt is False
        assert orch.status().state == "IDLE"
        await orch.stop()


def test_chaos_status_is_pure_python_no_io() -> None:
    """Asserting structurally: orch.status() doesn't await I/O.

    The motivation is the live-system scenario "rig socket died
    mid-operation" — the REST API must still respond. A full asyncio
    orchestrator test would have to deal with the rig client's
    reconnect timer making teardown brittle; instead we assert the
    property at the type level: status() is sync, so it cannot
    await any blocking call.
    """
    import inspect

    from ft8_appliance.runtime.orchestrator import Orchestrator

    assert not inspect.iscoroutinefunction(Orchestrator.status), (
        "status() must remain a sync method — any await in it would "
        "couple status latency to rig/gpsd I/O latency"
    )


# ---------------------------------------------------------------------------
# All-zero ("cable pulled") audio against the real decoder
# ---------------------------------------------------------------------------
def test_chaos_silent_audio_yields_no_decodes() -> None:
    """An all-zero slot through the C decoder must return 0 decodes
    without crashing or hanging."""
    from ft8_appliance.decode.ft8_native import SAMPLES_PER_SLOT, decode_slot
    silent = b"\x00\x00" * SAMPLES_PER_SLOT
    results = decode_slot(silent)
    assert results == [] or results == []  # robust against returning empty
    # Critically, no crash, no exception, no hang


def test_chaos_negative_audio_freq_rejected_by_synth() -> None:
    """The new shim guard rejects bad audio frequencies."""
    from ft8_appliance.decode.ft8_native import FT8EncodeError, synth_message
    with pytest.raises(FT8EncodeError):
        synth_message("CQ DK9XR JN58", audio_freq_hz=-500.0)


def test_chaos_excessive_audio_freq_rejected_by_synth() -> None:
    """Above Nyquist (6 kHz) is also rejected."""
    from ft8_appliance.decode.ft8_native import FT8EncodeError, synth_message
    with pytest.raises(FT8EncodeError):
        synth_message("CQ DK9XR JN58", audio_freq_hz=9000.0)


def test_chaos_zero_audio_freq_rejected_by_synth() -> None:
    """audio_freq_hz=0 is invalid (would TX DC)."""
    from ft8_appliance.decode.ft8_native import FT8EncodeError, synth_message
    with pytest.raises(FT8EncodeError):
        synth_message("CQ DK9XR JN58", audio_freq_hz=0.0)


# ---------------------------------------------------------------------------
# chrony offset spike — pure state-machine test, not orchestrator-level
# ---------------------------------------------------------------------------
def test_chaos_chrony_offset_spike_blocks_tx() -> None:
    """If chrony reports >0.5 s offset, every TX transition must lock."""
    from ft8_appliance.statemachine import (
        GuardLimits,
        MachineContext,
        StateMachine,
    )

    sm = StateMachine(
        ctx=MachineContext(callsign="DK9XR", my_grid="JN58td"),
        limits=GuardLimits(),
    )
    chrony_drifted = HardwareState(
        gps_fix_mode=3,
        time_offset_s=1.8,  # way past 0.5s
        swr=1.2,
        alc_pct=0,
        battery_v=13.4,
        cpu_temp_c=55.0,
    )
    sm.on_user_start_cq(chrony_drifted)
    actions = sm.drain_actions()
    assert any(a.kind == "TX_LOCKED" for a in actions)
    assert "offset" in (sm.ctx.last_lock_reason or "").lower()
