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
    # Stufe 2 und jt9 laufen parallel — wer zuerst fertig ist, bekommt den
    # gemeinsamen Fund gutgeschrieben; die Summe ist entscheidend.
    assert pl.metrics.jt9_total + pl.metrics.late_decodes_total == 2
    assert pl.metrics.jt9_total >= 1


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


def test_build_cmd_ft4_and_ap_options(tmp_path) -> None:
    from pathlib import Path
    cmd = _jt9.build_cmd("/usr/bin/jt9", Path("/dev/shm/x.wav"), Path("/dev/shm"), depth=2, mode="FT4")
    assert cmd[1] == "-5" and "-d" in cmd and cmd[-1] == "/dev/shm/x.wav" and "-c" not in cmd
    cmd = _jt9.build_cmd("/usr/bin/jt9", Path("/dev/shm/x.wav"), Path("/dev/shm"), depth=3, mode="FT8",
                         my_call="DK9XR", my_grid="JN58td", his_call="W1AW", his_grid="FN31", ap_flags=3)
    assert cmd[1] == "-8"
    assert cmd[cmd.index("-c") + 1] == "DK9XR" and cmd[cmd.index("-G") + 1] == "JN58"
    assert cmd[cmd.index("-x") + 1] == "W1AW" and cmd[cmd.index("-g") + 1] == "FN31" and cmd[cmd.index("-X") + 1] == "3"


@pytest.mark.asyncio
async def test_depth_boost_uses_depth_3_and_blocks_after_overrun(monkeypatch) -> None:
    seen_depths = []
    monkeypatch.setattr(_pipe, "decode_slot", lambda pcm: [])
    monkeypatch.setattr(_pipe, "decode_slot_v2", lambda pcm, mode="standard": [])
    monkeypatch.setattr(_jt9, "available", lambda: True)
    monkeypatch.setattr(_jt9, "run_jt9", lambda pcm, **kw: seen_depths.append(kw["depth"]) or [])
    buf = SlotBuffer(); buf.feed(b"\x00\x00" * SAMPLES_PER_SLOT, posix_start=SLOT0)
    pl = DecodePipeline(slot_buffer=buf, band_hint="20m")
    pl.decoder_mode = "extreme"; pl.extract_delay_s = 0.0; pl.late_pass_sink = AsyncMock()
    pl.jt9_depth = 2; pl.jt9_depth_boost = True
    await pl(_tick(0)); await pl._jt9_task
    assert seen_depths == [3]
    pl._jt9_boost_blocked_until = 10**12          # Sperre aktiv -> zurueck auf 2
    await pl(_tick(1)); await pl._jt9_task
    assert seen_depths == [3, 2]


def test_parser_reads_ft4_lines_and_ap_suffix() -> None:
    out = "000000 -10 -0.2 1500 +  DK9XR JA1XYZ PM95                       \n000000  -3  0.1  987 ~  DK9XR W1AW R-05     a2\n<DecodeFinished>   0   2        0\n"
    d = _jt9.parse_jt9_output(out)
    assert [x.message for x in d] == ["DK9XR JA1XYZ PM95", "DK9XR W1AW R-05"]


def test_parser_drops_uncertain_ap_decodes() -> None:
    out = "000000 -22  0.3 1500 ~  DL8TG SP2EWQ -10     a2\n000000 -23  0.3 1500 ~  DL8TG SP2EWQ -12     ? a2\n"
    assert [x.message for x in _jt9.parse_jt9_output(out)] == ["DL8TG SP2EWQ -10"]
    assert len(_jt9.parse_jt9_output(out, drop_uncertain=False)) == 2


def test_run_jt9_uses_workdir_as_cwd(monkeypatch, tmp_path) -> None:
    """jt9 legt decoded.txt im aktuellen Verzeichnis ab. Ohne cwd landet die
    Datei im Repo-Checkout und blockiert das naechste git checkout des
    Self-Update (live 2026-09-08: Pi blieb auf v0.81.0)."""
    seen = {}

    class _Res:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(_jt9, "jt9_path", lambda: "/usr/bin/jt9")
    monkeypatch.setattr(_jt9, "work_dir", lambda: tmp_path)
    monkeypatch.setattr(_jt9.subprocess, "run", lambda cmd, **kw: seen.update(kw, cmd=cmd) or _Res())
    _jt9.run_jt9(b"\x00\x00" * 100, depth=2)
    assert seen["cwd"] == str(tmp_path)
