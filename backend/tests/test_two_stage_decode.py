"""Zweistufiger Decoder (2026-09-06).

Gemessen am Pi 4B: im extreme-Modus lag der Sendestart 2,8 s nach der
Slot-Grenze, weil der ganze Decoder VOR der TX-Entscheidung laeuft. Der
Standard-Pass allein braucht 0,25 s und liefert ~94 % der Decodes. Also:
Stufe 1 (standard) entscheidet ueber TX, Stufe 2 (der Rest des Modus)
laeuft nebenher und reicht nach, was sie zusaetzlich findet.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.audio.slot_sync import SlotBuffer
from ft8_appliance.decode import pipeline as _pipe
from ft8_appliance.decode.ft8_native import SAMPLES_PER_SLOT, ShimDecode
from ft8_appliance.decode.pipeline import DecodePipeline
from ft8_appliance.runtime.slot_clock import SlotTick

SLOT0 = 1_700_000_000.0


def _tick(i: int) -> SlotTick:
    posix = SLOT0 + 15.0 * (i + 1)
    return SlotTick(index=i, posix=posix, utc_start=_dt.datetime.fromtimestamp(posix, tz=_dt.UTC))


def _shim(msg: str, score: int = 30) -> ShimDecode:
    return ShimDecode(message=msg, snr_db_est=-10, dt_s=0.1, freq_hz=1500.0, score=score)


def _pipeline(mode: str, sink) -> DecodePipeline:
    buf = SlotBuffer()
    buf.feed(b"\x00\x00" * SAMPLES_PER_SLOT, posix_start=SLOT0)
    pl = DecodePipeline(slot_buffer=buf, band_hint="15m")
    pl.decoder_mode = mode
    pl.extract_delay_s = 0.0
    pl.late_pass_sink = sink
    return pl


async def _wait_late(pl: DecodePipeline) -> None:
    if pl._late_task is not None:
        await pl._late_task


# ================================================================== Pipeline


@pytest.mark.asyncio
async def test_fast_pass_returns_immediately_and_late_pass_delivers_the_rest(monkeypatch) -> None:
    fast = [_shim("CQ DL1AAA JN58")]
    full = [_shim("CQ DL1AAA JN58"), _shim("CQ W1WEAK FN31", score=8)]
    monkeypatch.setattr(_pipe, "decode_slot", lambda pcm: fast)
    monkeypatch.setattr(_pipe, "decode_slot_v2", lambda pcm, mode="standard": full)
    late: list = []

    async def sink(msgs, tick):
        late.append((tick.index, [m.message for m in msgs], [m.late for m in msgs]))

    pl = _pipeline("extreme", sink)
    out = await pl(_tick(0))
    assert [m.message for m in out] == ["CQ DL1AAA JN58"]  # Stufe 1 sofort
    assert out[0].late is False
    await _wait_late(pl)
    assert late == [(0, ["CQ W1WEAK FN31"], [True])]  # nur das Neue, markiert
    assert pl.metrics.late_pass_last_count == 1
    assert pl.metrics.late_decodes_total == 1


@pytest.mark.asyncio
async def test_standard_mode_has_no_second_stage(monkeypatch) -> None:
    monkeypatch.setattr(_pipe, "decode_slot", lambda pcm: [_shim("CQ DL1AAA JN58")])
    called = []
    monkeypatch.setattr(_pipe, "decode_slot_v2", lambda pcm, mode="standard": called.append(mode) or [])
    pl = _pipeline("standard", AsyncMock())
    await pl(_tick(0))
    assert pl._late_task is None and called == []


@pytest.mark.asyncio
async def test_two_stage_can_be_switched_off(monkeypatch) -> None:
    """decoder_late_pass=false: alter Weg, der ganze Modus vor der TX-Entscheidung."""
    full = [_shim("CQ DL1AAA JN58"), _shim("CQ W1WEAK FN31")]
    monkeypatch.setattr(_pipe, "decode_slot", lambda pcm: [])
    monkeypatch.setattr(_pipe, "decode_slot_v2", lambda pcm, mode="standard": full)
    pl = _pipeline("extreme", AsyncMock())
    pl.two_stage = False
    out = await pl(_tick(0))
    assert len(out) == 2 and pl._late_task is None


@pytest.mark.asyncio
async def test_a_running_late_pass_skips_the_next_and_downgrades_after_three(monkeypatch) -> None:
    monkeypatch.setattr(_pipe, "decode_slot", lambda pcm: [])
    gate = asyncio.Event()

    def slow_v2(pcm, mode="standard"):
        # blockiert den Late-Thread, bis der Test ihn freigibt
        import time
        while not gate.is_set():
            time.sleep(0.01)
        return []

    monkeypatch.setattr(_pipe, "decode_slot_v2", slow_v2)
    pl = _pipeline("extreme", AsyncMock())
    await pl(_tick(0))          # startet Stufe 2, die haengt
    for i in range(1, 4):
        await pl(_tick(i))      # Stufe 2 laeuft noch -> uebersprungen
    assert pl.metrics.late_pass_skipped == 3
    assert pl.decoder_mode == "multi"  # eine Stufe runter, nicht gleich standard
    gate.set()
    await _wait_late(pl)


def test_downgrade_order() -> None:
    pl = _pipeline("extreme", AsyncMock())
    for expected in ("multi", "deep", "standard", "standard"):
        pl._downgrade_mode("test")
        assert pl.decoder_mode == expected


@pytest.mark.asyncio
async def test_late_pass_duration_feeds_the_adaptive_ldpc_window(monkeypatch) -> None:
    """Sonst hielte die LDPC-Adaption den Slot fuer fast leer (Stufe 1: 0,25 s)
    und drehte die Iterationen hoch — obwohl Stufe 2 schon 3 s frisst."""
    monkeypatch.setattr(_pipe, "decode_slot", lambda pcm: [])
    monkeypatch.setattr(_pipe, "decode_slot_v2", lambda pcm, mode="standard": [])
    pl = _pipeline("extreme", AsyncMock())
    await pl(_tick(0))
    await _wait_late(pl)
    pl.metrics.note_late_pass(0, 3.0)
    assert pl.metrics.recent_durations_s[-1] == 3.0


# ================================================================== Orchestrator


def _orch():
    from ft8_appliance.config import (
        AntennaConfig,
        AppConfig,
        BandConfig,
        OperatingConfig,
        OperatorConfig,
    )
    from ft8_appliance.rig.rigctld_client import RigSnapshot
    from ft8_appliance.runtime import FakeSlotClock, Orchestrator

    cfg = AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[BandConfig(name="15m", freq_khz=21074, antenna="w")],
        antennas=[AntennaConfig(name="w", bands=["15m"])],
        operating=OperatingConfig(),
    )
    rig = AsyncMock(); rig.snapshot = AsyncMock(return_value=RigSnapshot(freq_hz=21_074_000)); rig.close = AsyncMock()
    gps = AsyncMock(); gps.snapshot = SimpleNamespace(mode=3, lat=0, lon=0, ts=None, lock_for_min=None, satellites_used=None); gps.close = AsyncMock()

    async def nd(tick):
        return []

    o = Orchestrator(config=cfg, rig=rig, gps=gps, decode_source=nd, slot_clock=FakeSlotClock(count=0))
    o._rx_audio_dbfs_peak = None; o._rx_audio_dbfs_peak_ts = 0.0
    return o


def _msg(text: str, frm: str, to: str | None, late: bool = True):
    from ft8_appliance.statemachine import DecodedMsg
    return DecodedMsg(ts=_dt.datetime.now(_dt.UTC), call_from=frm, call_to=to, grid="JN58",
                      message=text, snr_db=-12, dt_s=0.1, freq_offset_hz=1500, band="15m", late=late)


@pytest.mark.asyncio
async def test_late_decodes_reach_sse_state_machine_and_slot_snapshot() -> None:
    o = _orch()
    q = o.subscribe_decodes()
    o._last_decodes = [_msg("CQ DL1AAA JN58", "DL1AAA", None, late=False)]
    late = [_msg("CQ W1WEAK FN31", "W1WEAK", None)]
    await o._ingest_late_decodes(late, _tick(3))
    assert q.get_nowait().message == "CQ W1WEAK FN31"
    assert [d.message for d in o._last_decodes] == ["CQ DL1AAA JN58", "CQ W1WEAK FN31"]
    assert [d.message for d in o.state_machine.last_decodes] == ["CQ DL1AAA JN58", "CQ W1WEAK FN31"]


@pytest.mark.asyncio
async def test_late_direct_call_arms_a_reply_but_does_not_key_mid_slot() -> None:
    """Jemand ruft uns, nur Stufe 2 hat es gesehen: State-Machine geht in
    QSO_RESPOND, aber der Burst wird nicht mitten im Slot getastet (B4) —
    er kommt beim naechsten CQ/Repeat des Partners an der Grenze."""
    from ft8_appliance.statemachine.guards import HardwareState
    from ft8_appliance.statemachine.states import State

    o = _orch()
    o._hardware_state = HardwareState()  # bis zum ersten Slot steht der Orchestrator auf "rot"
    o.state_machine.set_auto_answer(True)
    o._slot_phase_s = lambda: 6.0  # type: ignore[method-assign]
    o.playback = SimpleNamespace(play=lambda pcm: None)
    o.rig.set_ptt = AsyncMock()
    await o._ingest_late_decodes([_msg("DK9XR W1WEAK FN31", "W1WEAK", "DK9XR")], _tick(3))
    assert o.state_machine.state in (State.QSO_RESPOND, State.QSO_REPORT)
    o.rig.set_ptt.assert_not_called()
    assert o.status().tx_start_offset_s == pytest.approx(6.0)


def test_sink_is_wired_in_start_source() -> None:
    import inspect

    from ft8_appliance.runtime.orchestrator import Orchestrator

    src = inspect.getsource(Orchestrator.start)
    assert "late_pass_sink = self._ingest_late_decodes" in src


@pytest.mark.asyncio
async def test_psk_upload_happens_once_per_decode() -> None:
    """Vorher zweimal pro Decode (zwei Bloecke im Slot-Pfad); der Client
    dedupte vor dem Flush, darum fiel es nie auf."""
    o = _orch()
    o.integrations.psk_reporter = SimpleNamespace(upload_decode=AsyncMock())
    o._last_rig.freq_hz = 21_074_000
    await o._publish_decodes([_msg("CQ DL1AAA JN58", "DL1AAA", None, late=False)])
    assert o.integrations.psk_reporter.upload_decode.await_count == 1
