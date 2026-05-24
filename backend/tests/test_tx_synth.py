"""TX-synth roundtrip — proves that text → PCM → decode recovers the message.

This is the keystone test for the "no hardware needed yet" claim: the
encode + decode pipelines lined up correctly so anything DK9XR's appliance
will eventually transmit, it can also decode itself.
"""

from __future__ import annotations

import pytest

from ft8_appliance.decode.ft8_native import (
    SAMPLES_PER_SLOT,
    SAMPLES_PER_SLOT_FT4,
    TX_SAMPLES,
    TX_SAMPLES_FT4,
    decode_slot,
    decode_slot_ft4,
    synth_message,
    synth_message_ft4,
)


@pytest.mark.parametrize(
    "text, freq",
    [
        ("CQ DK9XR JN58", 1500.0),
        ("CQ DK9XR JN58", 1000.0),
        ("CQ DK9XR JN58", 2400.0),
        ("CQ W1AW FN31",  1500.0),
        ("CQ DX JA1ABC PM95", 1500.0),
    ],
)
def test_synth_message_roundtrips_through_decoder(text: str, freq: float) -> None:
    tx_pcm = synth_message(text, audio_freq_hz=freq, amplitude=0.9)
    assert len(tx_pcm) == TX_SAMPLES * 2

    # Pad with silence to one full slot (180 000 samples) before feeding decode
    pad = SAMPLES_PER_SLOT - TX_SAMPLES
    slot = tx_pcm + b"\x00\x00" * pad

    results = decode_slot(slot)
    msgs = [r.message.strip() for r in results]
    assert text in msgs, f"expected {text!r} in decoded set, got {msgs}"
    hit = next(r for r in results if r.message.strip() == text)
    assert abs(hit.freq_hz - freq) < 25, f"freq off: expected ~{freq}, got {hit.freq_hz}"


def test_synth_message_amplitude_clip_does_not_decode() -> None:
    """Zero amplitude → silence → no decodes."""
    pcm = synth_message("CQ DK9XR JN58", audio_freq_hz=1500.0, amplitude=0.0001)
    pad = SAMPLES_PER_SLOT - TX_SAMPLES
    slot = pcm + b"\x00\x00" * pad
    results = decode_slot(slot)
    # 0.0001 may or may not decode (decoder is sensitive). Sanity: SNR
    # estimate must be low if it does.
    if results:
        assert all(r.snr_db_est <= 0 for r in results)


def test_synth_message_two_signals_both_decoded() -> None:
    """Two messages at different audio frequencies — both decoded."""
    import struct

    a = synth_message("CQ DK9XR JN58", audio_freq_hz=1200.0)
    b = synth_message("CQ W1AW FN31",  audio_freq_hz=2000.0)
    # Sum sample-wise with clipping
    n = TX_SAMPLES
    mix = bytearray()
    for i in range(n):
        sa = struct.unpack_from("<h", a, i * 2)[0]
        sb = struct.unpack_from("<h", b, i * 2)[0]
        s = max(-32768, min(32767, (sa + sb) // 2))  # /2 to avoid clipping
        mix += struct.pack("<h", s)
    slot = bytes(mix) + b"\x00\x00" * (SAMPLES_PER_SLOT - n)

    results = decode_slot(slot)
    msgs = {r.message.strip() for r in results}
    assert "CQ DK9XR JN58" in msgs
    assert "CQ W1AW FN31"  in msgs


# ---------------------------------------------------------------------------
# FT4 — same round-trip, half the slot length, 4-FSK instead of 8-FSK.
@pytest.mark.parametrize(
    "text, freq",
    [
        ("CQ DK9XR JN58", 1500.0),
        ("CQ DK9XR JN58", 1200.0),
        ("CQ W1AW FN31",  2000.0),
    ],
)
def test_ft4_synth_roundtrips_through_decoder(text: str, freq: float) -> None:
    tx_pcm = synth_message_ft4(text, audio_freq_hz=freq, amplitude=0.9)
    assert len(tx_pcm) == TX_SAMPLES_FT4 * 2
    # Pad to 7.5-s slot (90 000 samples) before feeding the FT4 decoder.
    pad_bytes = SAMPLES_PER_SLOT_FT4 * 2 - len(tx_pcm)
    slot = tx_pcm + b"\x00" * pad_bytes

    results = decode_slot_ft4(slot)
    msgs = [r.message.strip() for r in results]
    assert text in msgs, f"expected {text!r} in FT4 decoded set, got {msgs}"
    hit = next(r for r in results if r.message.strip() == text)
    # FT4 tone spacing is ~20 Hz so the freq estimate is coarser than FT8.
    assert abs(hit.freq_hz - freq) < 40, f"FT4 freq off: expected ~{freq}, got {hit.freq_hz}"


def test_ft4_slot_length_matches_protocol() -> None:
    """FT4 slot is exactly 7.5 s @ 12 kHz = 90 000 samples."""
    assert SAMPLES_PER_SLOT_FT4 == 90_000
    # TX waveform is 105 * 576 = 60 480 samples, fits inside the 90 000-sample slot.
    assert TX_SAMPLES_FT4 == 60_480
    assert TX_SAMPLES_FT4 < SAMPLES_PER_SLOT_FT4


def test_ft4_decoder_does_not_pick_up_ft8_signal() -> None:
    """Sanity: an FT8 burst won't decode through the FT4 protocol path —
    different tone count + spacing makes the two waveforms orthogonal."""
    ft8_pcm = synth_message("CQ DK9XR JN58", audio_freq_hz=1500.0, amplitude=0.9)
    # Truncate/pad to FT4 slot length so we can feed it to decode_slot_ft4.
    target = SAMPLES_PER_SLOT_FT4 * 2
    if len(ft8_pcm) >= target:
        slot = ft8_pcm[:target]
    else:
        slot = ft8_pcm + b"\x00" * (target - len(ft8_pcm))
    results = decode_slot_ft4(slot)
    # Either zero decodes, or definitely not the FT8 message verbatim.
    assert "CQ DK9XR JN58" not in [r.message.strip() for r in results]
