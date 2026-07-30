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


# ---------------------------------------------------------------------------
# CPU-adaptiver Auto-Fallback bei Late-Slots
#
# Regression: die Fallback-Bedingung listete nur ("deep", "multi") und liess
# ausgerechnet "extreme" aussen vor — also den Default-Modus. Der Kommentar
# an OperatingConfig.decoder_mode versprach das Gegenteil ("CPU-Adaptive
# Fallback bleibt aktiv ... damit der Default auch auf Pi 4 nicht hangs
# ist"). Kein Test deckte den Pfad ab, deshalb fiel es nicht auf.
# ---------------------------------------------------------------------------
class _JumpClock:
    """monotonic()-Ersatz, der pro Aufruf um *step* Sekunden springt.

    Die Pipeline misst die Decode-Dauer mit zwei Aufrufen (t0/t1), also
    ergibt step=12.5 eine gemessene Dauer von 12.5 s — ueber der
    Late-Schwelle von 12 s (0.8 x 15 s), ohne dass der Test wartet.
    """

    def __init__(self, step: float = 12.5) -> None:
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def _tick(index: int, slot_start: float) -> SlotTick:
    import datetime as _dt

    posix = slot_start + 15.0 * (index + 1)
    return SlotTick(
        index=index,
        posix=posix,
        utc_start=_dt.datetime.fromtimestamp(posix, tz=_dt.timezone.utc),
    )


async def _run_late_slots(monkeypatch, mode: str, n: int) -> DecodePipeline:
    """*n* kuenstlich verspaetete Slots durch eine Pipeline im Modus *mode*."""
    import time as _time

    from ft8_appliance.decode import pipeline as _pipe

    slot_start = 1_700_000_000.0
    buf = SlotBuffer()
    buf.feed(b"\x00\x00" * SAMPLES_PER_SLOT, posix_start=slot_start)

    pl = DecodePipeline(slot_buffer=buf, band_hint="20m")
    pl.decoder_mode = mode
    pl.extract_delay_s = 0.0  # kein echtes Warten im Test

    # Decoder-Aufruf neutralisieren: hier interessiert nur das Timing.
    monkeypatch.setattr(_pipe, "decode_slot_v2", lambda pcm, mode="standard": [])
    monkeypatch.setattr(_pipe, "decode_slot", lambda pcm: [])
    monkeypatch.setattr(_time, "monotonic", _JumpClock())

    for i in range(n):
        await pl(_tick(i, slot_start))
    return pl


@pytest.mark.asyncio
async def test_extreme_falls_back_to_standard_after_three_late_slots(monkeypatch) -> None:
    pl = await _run_late_slots(monkeypatch, "extreme", 3)
    assert pl.decoder_mode == "standard"
    assert pl.metrics.late_slot_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["deep", "multi", "extreme"])
async def test_all_expensive_modes_fall_back(monkeypatch, mode: str) -> None:
    pl = await _run_late_slots(monkeypatch, mode, 3)
    assert pl.decoder_mode == "standard", f"{mode} ist nicht zurueckgefallen"


@pytest.mark.asyncio
async def test_two_late_slots_do_not_trigger_fallback(monkeypatch) -> None:
    """Erst ab 3 Slots in Folge — ein einzelner CPU-Spike darf nicht degradieren."""
    pl = await _run_late_slots(monkeypatch, "extreme", 2)
    assert pl.decoder_mode == "extreme"


@pytest.mark.asyncio
async def test_standard_mode_has_nothing_to_fall_back_to(monkeypatch) -> None:
    pl = await _run_late_slots(monkeypatch, "standard", 5)
    assert pl.decoder_mode == "standard"
