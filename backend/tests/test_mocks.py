"""Smoke tests for the hardware mocks themselves.

These don't exercise the controller — they just prove the mocks behave
sanely, so test failures elsewhere have a clean baseline.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.mocks.mock_audio import (
    SAMPLES_PER_SLOT,
    SLOT_SECONDS,
    MockAudio,
)
from tests.mocks.mock_gpsd import MockGpsd
from tests.mocks.mock_rigctld import MockRigctld


# ------------------------------------------------------------------ rigctld
@pytest.mark.asyncio
async def test_mock_rigctld_basic_roundtrip() -> None:
    async with MockRigctld() as rig:
        reader, writer = await asyncio.open_connection("127.0.0.1", rig.port)

        async def send(cmd: str) -> str:
            writer.write((cmd + "\n").encode())
            await writer.drain()
            line = await reader.readline()
            return line.decode().rstrip("\n")

        # frequency get / set / get
        assert await send("f") == "14074000"
        assert await send("F 14076000") == "RPRT 0"
        assert await send("f") == "14076000"

        # PTT
        assert await send("t") == "0"
        assert await send("T 1") == "RPRT 0"
        assert await send("t") == "1"

        # SWR readable
        rig.set_swr(2.5)
        assert float(await send("l SWR")) == pytest.approx(2.5)

        # unknown command
        assert await send("nope") == "RPRT -11"

        writer.close()
        await writer.wait_closed()


# ------------------------------------------------------------------ gpsd
@pytest.mark.asyncio
async def test_mock_gpsd_emits_fix_on_watch() -> None:
    async with MockGpsd() as gpsd:
        reader, writer = await asyncio.open_connection("127.0.0.1", gpsd.port)
        # connect-time banner
        version = json.loads(await reader.readline())
        assert version["class"] == "VERSION"
        devices = json.loads(await reader.readline())
        assert devices["class"] == "DEVICES"

        writer.write(b'?WATCH={"enable":true,"json":true};\n')
        await writer.drain()

        # WATCH ack, then TPV + SKY
        watch_ack = json.loads(await reader.readline())
        assert watch_ack["class"] == "WATCH"

        tpv = json.loads(await reader.readline())
        assert tpv["class"] == "TPV"
        assert tpv["mode"] == 3
        assert "lat" in tpv and "lon" in tpv

        sky = json.loads(await reader.readline())
        assert sky["class"] == "SKY"
        assert sum(1 for s in sky["satellites"] if s["used"]) == 8

        writer.close()
        await writer.wait_closed()


# ------------------------------------------------------------------ audio
def test_mock_audio_silent_slot_shape() -> None:
    slot = MockAudio.make_silent_slot()
    assert len(slot.pcm_s16le) == SAMPLES_PER_SLOT * 2  # 16-bit samples
    assert slot.pcm_s16le == b"\x00\x00" * SAMPLES_PER_SLOT


def test_mock_audio_tone_slot_is_nonzero() -> None:
    slot = MockAudio.make_tone_slot(freq_hz=1500.0)
    assert len(slot.pcm_s16le) == SAMPLES_PER_SLOT * 2
    # at least one non-zero sample
    assert any(b != 0 for b in slot.pcm_s16le[:1000])


def test_mock_audio_iterates_slots() -> None:
    """A WAV-less mock should at least yield one zero-padded slot."""
    audio = MockAudio()
    slots = list(audio.slots(start_ts=1_700_000_000.0))
    assert len(slots) == 1
    assert slots[0].slot_start_utc == 1_700_000_000.0
    assert len(slots[0].pcm_s16le) == SAMPLES_PER_SLOT * 2


@pytest.mark.asyncio
async def test_mock_audio_async_iter_fast_forward() -> None:
    audio = MockAudio()
    collected = []
    async for slot in audio.slots_async(start_ts=0.0, real_time=False):
        collected.append(slot)
    assert len(collected) == 1
    assert collected[0].slot_start_utc == 0.0


def test_slot_constants() -> None:
    assert SAMPLES_PER_SLOT == 180_000
    assert SLOT_SECONDS == 15
