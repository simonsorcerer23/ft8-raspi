"""Unit tests for the phase-locked SlotBuffer."""

from __future__ import annotations

import struct

import pytest

from ft8_appliance.audio.slot_sync import (
    BYTES_PER_SAMPLE,
    SAMPLE_RATE_HZ,
    SAMPLES_PER_SLOT,
    SlotBuffer,
)


def _pcm(samples: int, value: int = 1) -> bytes:
    return struct.pack(f"<{samples}h", *([value] * samples))


def test_exact_slot_zero_drift() -> None:
    buf = SlotBuffer()
    buf.feed(_pcm(SAMPLES_PER_SLOT), posix_start=100.0)
    ex = buf.extract_slot(100.0)
    assert len(ex.pcm_s16le) == SAMPLES_PER_SLOT * 2
    assert ex.actual_samples == SAMPLES_PER_SLOT
    assert ex.drift_samples == 0
    assert ex.anchor_posix == pytest.approx(100.0)


def test_pad_when_short() -> None:
    """If only half a slot is buffered, the rest is zero-padded."""
    half = SAMPLES_PER_SLOT // 2
    buf = SlotBuffer()
    buf.feed(_pcm(half, value=7), posix_start=100.0)
    ex = buf.extract_slot(100.0)
    # Output is always one full slot
    assert len(ex.pcm_s16le) == SAMPLES_PER_SLOT * 2
    # First half is the value we fed, second half is zero
    first_half = ex.pcm_s16le[: half * BYTES_PER_SAMPLE]
    second_half = ex.pcm_s16le[half * BYTES_PER_SAMPLE :]
    assert struct.unpack("<h", first_half[:2])[0] == 7
    assert second_half == b"\x00\x00" * half
    assert ex.actual_samples == half
    assert ex.drift_samples == -half


def test_chunks_are_concatenated() -> None:
    buf = SlotBuffer()
    third = SAMPLES_PER_SLOT // 3
    # three back-to-back chunks summing to a full slot
    buf.feed(_pcm(third, value=1), posix_start=100.0)
    buf.feed(_pcm(third, value=2), posix_start=100.0 + third / SAMPLE_RATE_HZ)
    buf.feed(
        _pcm(SAMPLES_PER_SLOT - 2 * third, value=3),
        posix_start=100.0 + 2 * third / SAMPLE_RATE_HZ,
    )
    ex = buf.extract_slot(100.0)
    assert ex.actual_samples == SAMPLES_PER_SLOT
    # Sanity-check that the seam values appear in the right places
    assert struct.unpack("<h", ex.pcm_s16le[0:2])[0] == 1
    mid_byte = third * BYTES_PER_SAMPLE
    assert struct.unpack("<h", ex.pcm_s16le[mid_byte : mid_byte + 2])[0] == 2
    last_third_byte = 2 * third * BYTES_PER_SAMPLE
    assert struct.unpack("<h", ex.pcm_s16le[last_third_byte : last_third_byte + 2])[0] == 3


def test_drops_old_chunks_on_prune() -> None:
    buf = SlotBuffer(max_age_s=5.0)
    buf.feed(_pcm(1000), posix_start=0.0)
    # feeding "now" 100 seconds later should prune the old chunk
    buf.feed(_pcm(1000), posix_start=100.0)
    # Asking for the old slot now yields zero-padded silence
    ex = buf.extract_slot(0.0)
    assert ex.actual_samples == 0
    assert ex.drift_samples == -SAMPLES_PER_SLOT


def test_late_start_is_padded_at_end() -> None:
    """Chunk arrives late — start of the slot is missing samples."""
    buf = SlotBuffer()
    # samples arrive 0.5s after slot start
    buf.feed(
        _pcm(int(14.5 * SAMPLE_RATE_HZ), value=42),
        posix_start=100.5,
    )
    ex = buf.extract_slot(100.0)
    # Zero-padding goes at the *end* (current behaviour), so the first
    # 14.5s of output is the value 42, the last 0.5s is zero. The
    # `actual_samples` reflects what we recovered.
    expected_real = int(14.5 * SAMPLE_RATE_HZ)
    assert ex.actual_samples == expected_real
    assert ex.drift_samples == expected_real - SAMPLES_PER_SLOT
