"""Stufe 3: jt9 (2026-09-08)."""

from __future__ import annotations

import datetime as _dt
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.audio.slot_sync import SlotBuffer
from ft8_appliance.decode import jt9 as _jt9
from ft8_appliance.decode import pipeline as _pipe
from ft8_appliance.decode.ft8_native import SAMPLES_PER_SLOT, ShimDecode
from ft8_appliance.decode.pipeline import DecodePipeline
from ft8_appliance.runtime.slot_clock import SlotTick

SLOT0 = 1_700_000_000.0


def _tick(i: int) -> SlotTick:
    posix = SLOT0 + 15.0 * (i + 1)
    return SlotTick(index=i, posix=posix, utc_start=_dt.datetime.fromtimestamp(posix, tz=_dt.UTC))


def _shim(msg: str) -> ShimDecode:
    return ShimDecode(message=msg, snr_db_est=-10, dt_s=0.1, freq_hz=1500.0, score=20)


def test_parser_reads_wsjtx_lines() -> None:
    out = "000000 -12  0.3 1234 ~  CQ DL1AAA JN58\n000000  -3  0.1  987 ~  DK9XR <W1AW> R-05   \nBlock size = 1920\n"
    d = _jt9.parse_jt9_output(out)
    assert [(x.message, x.snr_db_est, x.dt_s, x.freq_hz) for x in d] == [
        ("CQ DL1AAA JN58", -12, 0.3, 1234.0), ("DK9XR <W1AW> R-05", -3, 0.1, 987.0)]


@pytest.mark.asyncio
async def test_jt9_stage_delivers_only_new_messages(monkeypatch) -> None:
    fast = [_shim("CQ DL1AAA JN58")]
    monkeypatch.setattr(_pipe, "decode_slot", lambda pcm: fast)
    monkeypatch.setattr(_pipe, "decode_slot_v2", lambda pcm, mode="standard": fast + [_shim("CQ W1AW FN31")])
    monkeypatch.setattr(_jt9, "available", lambda: True)
    monkeypatch.setattr(_jt9, "run_jt9", lambda pcm, **kw: [_shim("CQ DL1AAA JN58"), _shim("CQ W1AW FN31"), _shim("CQ JA1XYZ PM95")])
    late: list = []

    async def sink(msgs, tick):
        late.append(sorted(m.message for m in msgs))

    buf = SlotBuffer(); buf.feed(b"\x00\x00" * SAMPLES_PER_SLOT, posix_start=SLOT0)
    pl = DecodePipeline(slot_buffer=buf, band_hint="20m")
    pl.decoder_mode = "extreme"; pl.extract_delay_s = 0.0; pl.late_pass_sink = sink
    out = await pl(_tick(0))
    assert [m.message for m in out] == ["CQ DL1AAA JN58"]
    await pl._late_task
    await pl._jt9_task
    delivered = sorted(m for batch in late for m in batch)
    assert delivered == ["CQ JA1XYZ PM95", "CQ W1AW FN31"]   # jeder nur einmal, egal wer ihn fand
    assert pl.metrics.jt9_total == 1 and pl.metrics.late_decodes_total == 1


@pytest.mark.asyncio
async def test_jt9_stage_off_without_binary_or_for_ft4(monkeypatch) -> None:
    monkeypatch.setattr(_pipe, "decode_slot", lambda pcm: [])
    monkeypatch.setattr(_pipe, "decode_slot_v2", lambda pcm, mode="standard": [])
    monkeypatch.setattr(_jt9, "available", lambda: False)
    buf = SlotBuffer(); buf.feed(b"\x00\x00" * SAMPLES_PER_SLOT, posix_start=SLOT0)
    pl = DecodePipeline(slot_buffer=buf, band_hint="20m")
    pl.decoder_mode = "extreme"; pl.extract_delay_s = 0.0; pl.late_pass_sink = AsyncMock()
    await pl(_tick(0))
    assert pl._jt9_task is None


@pytest.mark.skipif(not _jt9.available(), reason="jt9 nicht installiert")
def test_real_jt9_decodes_a_reference_wav() -> None:
    import pathlib
    import wave

    import numpy as np
    wav = pathlib.Path(__file__).resolve().parents[2] / "vendor" / "ft8_lib" / "test" / "wav" / "websdr_test7.wav"
    with wave.open(str(wav)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    buf = np.zeros(SAMPLES_PER_SLOT, np.int16); buf[: len(x)] = x[:SAMPLES_PER_SLOT]
    d = _jt9.run_jt9(buf.tobytes(), depth=1, timeout_s=60)
    assert any("SP2EWQ" in r.message for r in d)
