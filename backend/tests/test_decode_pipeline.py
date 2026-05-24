"""End-to-end decode tests: synthesise an FT8 signal with ``gen_ft8``,
feed it through SlotBuffer → DecodePipeline, assert we recover the
message.

This is the load-bearing proof that the *full* path works on real audio
samples, not just the encode side.
"""

from __future__ import annotations

import struct
import subprocess
import wave
from pathlib import Path

import pytest

from ft8_appliance.audio.slot_sync import SAMPLES_PER_SLOT, SlotBuffer
from ft8_appliance.decode.ft8_native import decode_slot
from ft8_appliance.decode.pipeline import DecodePipeline, parse_message
from ft8_appliance.runtime.slot_clock import SlotTick

REPO_ROOT = Path(__file__).resolve().parents[2]
FT8_LIB_DIR = REPO_ROOT / "vendor" / "ft8_lib"
GEN_FT8 = FT8_LIB_DIR / "gen_ft8"


# ---------------------------------------------------------------------------
def _gen_wav(message: str, freq_hz: int = 1500, tmp_path: Path | None = None) -> Path:
    """Use the vendored ``gen_ft8`` binary to synthesise a slot WAV."""
    if tmp_path is None:
        tmp_path = Path("/tmp")
    out = tmp_path / f"gen_{abs(hash(message))}.wav"
    if not GEN_FT8.exists():
        pytest.skip(f"gen_ft8 binary not built at {GEN_FT8}")
    subprocess.run(
        [str(GEN_FT8), message, str(out), str(freq_hz)],
        check=True,
        capture_output=True,
    )
    return out


def _wav_to_pcm_s16le(wav_path: Path) -> bytes:
    """Read a 12 kHz mono S16LE WAV into raw bytes (frames only)."""
    with wave.open(str(wav_path), "rb") as w:
        assert w.getframerate() == 12_000, f"unexpected rate {w.getframerate()}"
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        return w.readframes(w.getnframes())


# ---------------------------------------------------------------------------
# Parser tests (no audio involved)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, exp_from, exp_to, exp_grid, exp_report, exp_cq",
    [
        ("CQ DK9XR JN58",         "DK9XR", None,    "JN58", None, True),
        ("CQ DX W1AW FN31",       "W1AW",  None,    "FN31", None, True),
        ("DK9XR W1AW FN31",       "W1AW",  "DK9XR", "FN31", None, False),
        ("DK9XR W1AW -12",        "W1AW",  "DK9XR", None,   "-12", False),
        ("DK9XR W1AW R-08",       "W1AW",  "DK9XR", None,   "R-08", False),
        ("DK9XR W1AW RR73",       "W1AW",  "DK9XR", None,   None, False),
        ("DK9XR W1AW 73",         "W1AW",  "DK9XR", None,   None, False),
    ],
)
def test_parse_message_shapes(text, exp_from, exp_to, exp_grid, exp_report, exp_cq):
    p = parse_message(text)
    assert p.call_from == exp_from
    assert p.call_to == exp_to
    assert p.grid == exp_grid
    assert p.report == exp_report
    assert p.is_cq == exp_cq


# ---------------------------------------------------------------------------
# Decode round-trip — needs the gen_ft8 binary built.
# ---------------------------------------------------------------------------
def test_decode_slot_recovers_known_message(tmp_path: Path) -> None:
    wav = _gen_wav("CQ DK9XR JN58", freq_hz=1500, tmp_path=tmp_path)
    pcm = _wav_to_pcm_s16le(wav)

    results = decode_slot(pcm)

    assert results, "expected at least one decode from the synthesised slot"
    msgs = [r.message.strip() for r in results]
    assert "CQ DK9XR JN58" in msgs
    # The signal we put at 1500 Hz should show up close to that
    cq = next(r for r in results if r.message.strip() == "CQ DK9XR JN58")
    assert abs(cq.freq_hz - 1500) < 20, f"expected ~1500 Hz, got {cq.freq_hz}"


def test_decode_slot_two_messages(tmp_path: Path) -> None:
    """Two non-overlapping decodable signals should both come out."""
    # Generate two separate slot wavs and add their samples sample-wise.
    wav_a = _gen_wav("CQ DK9XR JN58", freq_hz=1200, tmp_path=tmp_path)
    wav_b = _gen_wav("CQ W1AW FN31",  freq_hz=2000, tmp_path=tmp_path)
    a = _wav_to_pcm_s16le(wav_a)
    b = _wav_to_pcm_s16le(wav_b)

    # Mix samples; clip to int16 range
    n = min(len(a), len(b)) // 2
    mix = bytearray()
    for i in range(n):
        sa = struct.unpack_from("<h", a, i * 2)[0]
        sb = struct.unpack_from("<h", b, i * 2)[0]
        s = max(-32768, min(32767, sa + sb))
        mix += struct.pack("<h", s)

    results = decode_slot(bytes(mix))
    msgs = {r.message.strip() for r in results}
    assert "CQ DK9XR JN58" in msgs
    assert "CQ W1AW FN31"  in msgs


# ---------------------------------------------------------------------------
# Pipeline ↔ SlotBuffer ↔ orchestrator-tick integration
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pipeline_feeds_decoded_msgs_through_orchestrator_tick(tmp_path: Path) -> None:
    wav = _gen_wav("CQ DK9XR JN58", freq_hz=1500, tmp_path=tmp_path)
    pcm = _wav_to_pcm_s16le(wav)
    assert len(pcm) >= SAMPLES_PER_SLOT * 2

    # Build a SlotBuffer with that single slot, aligned to a fake POSIX time
    slot_start = 1_700_000_000.0
    buf = SlotBuffer()
    buf.feed(pcm, posix_start=slot_start)

    pipeline = DecodePipeline(slot_buffer=buf, band_hint="20m")
    # Tick "fires" 15 seconds later — i.e. the slot has just *ended*.
    tick = SlotTick(
        index=0,
        posix=slot_start + 15.0,
        utc_start=__import__("datetime").datetime.fromtimestamp(
            slot_start + 15.0, tz=__import__("datetime").timezone.utc
        ),
    )

    decodes = await pipeline(tick)
    assert decodes, "pipeline returned no decodes"
    cq = next(d for d in decodes if d.message.strip() == "CQ DK9XR JN58")
    assert cq.call_from == "DK9XR"
    assert cq.grid == "JN58"
    assert cq.band == "20m"
    assert pipeline.metrics.last_decode_count >= 1
    # drift should be zero or tiny (we fed exactly one slot of samples)
    assert abs(pipeline.metrics.last_drift_samples) <= 5
