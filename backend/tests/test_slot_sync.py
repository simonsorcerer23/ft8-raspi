"""SlotBuffer-Tests — Fokus auf die FT4-Parametrisierung (Audit F6 v0.4.0).

Bis v0.3.x war SAMPLES_PER_SLOT hartcodiert auf 180000 (15s). Mit der
Parametrisierung extract_slot(slot_seconds=...) kann derselbe Buffer
sowohl FT8 (15s) als auch FT4 (7.5s) bedienen.
"""

from __future__ import annotations

import ft8_appliance.audio.slot_sync as _ss
from ft8_appliance.audio.slot_sync import (
    FT4_SAMPLES_PER_SLOT,
    FT4_SLOT_SECONDS,
    FT4_TX_SECONDS,
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


def test_tail_zero_pad_in_silence_does_not_warn(monkeypatch) -> None:
    """Sebastian 2026-06-11: FT4 spammte jede 7.5s "short by samples".

    Das Zero-Pad sitzt am Slot-ENDE, das TX-Signal (FT4 ~4.48s) am Anfang.
    Solange wir >= signal_seconds erwischt haben, ist das Padding stille
    Lücke → KEIN log.warning (nur debug). Deterministisch via Logger-Patch
    (caplog ist im Full-Suite-Lauf von anderer Logging-Config abhängig)."""
    warns: list = []
    monkeypatch.setattr(_ss.log, "warning", lambda *a, **k: warns.append(a))
    buf = SlotBuffer(max_age_s=60.0)
    _feed_silence(buf, 5.0, 1000.0)  # deckt die 4.48s-TX + etwas Reserve
    ex = buf.extract_slot(
        1000.0, slot_seconds=FT4_SLOT_SECONDS, signal_seconds=FT4_TX_SECONDS
    )
    assert ex.actual_samples < FT4_SAMPLES_PER_SLOT  # genuinely short (Tail fehlt)
    assert warns == []  # ... aber NICHT gewarnt


def test_zero_pad_into_signal_still_warns(monkeypatch) -> None:
    """Reicht das Padding VOR signal_seconds (Signal abgeschnitten) → log.warning."""
    warns: list = []
    monkeypatch.setattr(_ss.log, "warning", lambda *a, **k: warns.append(a))
    buf = SlotBuffer(max_age_s=60.0)
    _feed_silence(buf, 4.0, 1000.0)  # < 4.48s → Signal-Bereich getroffen
    buf.extract_slot(
        1000.0, slot_seconds=FT4_SLOT_SECONDS, signal_seconds=FT4_TX_SECONDS
    )
    assert len(warns) == 1
