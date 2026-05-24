"""SlotBuffer-Tests — Fokus auf die FT4-Parametrisierung (Audit F6 v0.4.0).

Bis v0.3.x war SAMPLES_PER_SLOT hartcodiert auf 180000 (15s). Mit der
Parametrisierung extract_slot(slot_seconds=...) kann derselbe Buffer
sowohl FT8 (15s) als auch FT4 (7.5s) bedienen.
"""

from __future__ import annotations

from ft8_appliance.audio.slot_sync import (
    FT4_SAMPLES_PER_SLOT,
    FT4_SLOT_SECONDS,
    SAMPLE_RATE_HZ,
    SAMPLES_PER_SLOT,
    SLOT_SECONDS,
    SlotBuffer,
)


def _feed_silence(buf: SlotBuffer, duration_s: float, start_posix: float) -> None:
    samples = int(duration_s * SAMPLE_RATE_HZ)
    pcm = b"\x00\x00" * samples
    buf.feed(pcm, start_posix)


def test_extract_slot_default_15s_ft8() -> None:
    buf = SlotBuffer(max_age_s=60.0)
    _feed_silence(buf, 20.0, 1000.0)
    ex = buf.extract_slot(1000.0)
    assert len(ex.pcm_s16le) == SAMPLES_PER_SLOT * 2
    assert ex.actual_samples == SAMPLES_PER_SLOT


def test_extract_slot_ft4_window_7_5s() -> None:
    buf = SlotBuffer(max_age_s=60.0)
    _feed_silence(buf, 20.0, 1000.0)
    ex = buf.extract_slot(1000.0, slot_seconds=FT4_SLOT_SECONDS)
    assert len(ex.pcm_s16le) == FT4_SAMPLES_PER_SLOT * 2
    assert ex.actual_samples == FT4_SAMPLES_PER_SLOT


def test_extract_slot_zero_pads_when_short() -> None:
    buf = SlotBuffer(max_age_s=60.0)
    _feed_silence(buf, 3.0, 1000.0)
    ex = buf.extract_slot(1000.0, slot_seconds=FT4_SLOT_SECONDS)
    assert len(ex.pcm_s16le) == FT4_SAMPLES_PER_SLOT * 2
    assert ex.actual_samples == 3 * SAMPLE_RATE_HZ
    assert ex.drift_samples < 0


def test_same_buffer_serves_both_modes() -> None:
    buf = SlotBuffer(max_age_s=60.0)
    _feed_silence(buf, 30.0, 1000.0)
    ex_ft8 = buf.extract_slot(1000.0, slot_seconds=SLOT_SECONDS)
    ex_ft4 = buf.extract_slot(1000.0, slot_seconds=FT4_SLOT_SECONDS)
    assert len(ex_ft8.pcm_s16le) == SAMPLES_PER_SLOT * 2
    assert len(ex_ft4.pcm_s16le) == FT4_SAMPLES_PER_SLOT * 2
    assert len(ex_ft8.pcm_s16le) == 2 * len(ex_ft4.pcm_s16le)
