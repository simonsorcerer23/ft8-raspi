"""High-level Python facade over the cffi binding to ft8_lib.

Today this exposes only what's needed for the build/integration spike:
text-to-payload encoding and the symbol-level ``ft8_encode``. The full
decode pipeline lands in a later iteration.

Build the extension once with::

    python -m ft8_appliance.decode._build_ft8
"""

from __future__ import annotations

# Compiled by _build_ft8.py — import lazily so a missing build gives a
# clearer error message than a raw ImportError on package import.
# The compiled .so sits next to this file as ``_ft8_native.cpython-*.so``;
# we add this directory to sys.path so a flat top-level name resolves.
import sys
from dataclasses import dataclass
from pathlib import Path

_DECODE_DIR = Path(__file__).parent
if str(_DECODE_DIR) not in sys.path:
    sys.path.insert(0, str(_DECODE_DIR))

try:
    from _ft8_native import ffi, lib  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - import-time diagnostic
    raise ImportError(
        "ft8_lib native extension not built yet. Run:\n"
        "  python -m ft8_appliance.decode._build_ft8\n"
        f"Original error: {exc}"
    ) from exc


FT8_NN = 79  # number of FT8 tones per message
PAYLOAD_BYTES = 10


class FT8EncodeError(RuntimeError):
    """Raised when ftx_message_encode rejects the input text."""


@dataclass(frozen=True, slots=True)
class EncodedMessage:
    """Output of :func:`encode_text`."""

    text: str
    payload: bytes  # 10 bytes
    tones: bytes  # 79 bytes, each value 0..7


def encode_text(message_text: str) -> EncodedMessage:
    """Pack a textual FT8 message into the 77-bit payload and 79 tone symbols.

    Mirrors what ``gen_ft8`` does internally.
    """
    msg = ffi.new("ftx_message_t *")
    lib.ftx_message_init(msg)

    rc = lib.ftx_message_encode(msg, ffi.NULL, message_text.encode("ascii"))
    if rc != lib.FTX_MESSAGE_RC_OK:
        raise FT8EncodeError(f"ftx_message_encode returned {rc} for {message_text!r}")

    tones_buf = ffi.new(f"uint8_t[{FT8_NN}]")
    lib.ft8_encode(msg.payload, tones_buf)

    payload = bytes(ffi.buffer(msg.payload, PAYLOAD_BYTES))
    tones = bytes(ffi.buffer(tones_buf, FT8_NN))
    return EncodedMessage(text=message_text, payload=payload, tones=tones)


def decode_payload(payload: bytes) -> str:
    """Decode a 10-byte payload back to text (round-trip helper)."""
    if len(payload) != PAYLOAD_BYTES:
        raise ValueError(f"payload must be {PAYLOAD_BYTES} bytes, got {len(payload)}")
    msg = ffi.new("ftx_message_t *")
    ffi.memmove(msg.payload, payload, PAYLOAD_BYTES)
    out_buf = ffi.new("char[64]")  # FTX_MAX_MESSAGE_LENGTH is 35, leave headroom
    offsets = ffi.new("ftx_message_offsets_t *")
    rc = lib.ftx_message_decode(msg, ffi.NULL, out_buf, offsets)
    if rc != lib.FTX_MESSAGE_RC_OK:
        raise FT8EncodeError(f"ftx_message_decode returned {rc}")
    return ffi.string(out_buf).decode("ascii").strip()


# ============================================================================
# Slot decoding (ft8_shim.c wrapper)
# ============================================================================

SAMPLE_RATE_HZ = 12_000
SLOT_SECONDS = 15
SAMPLES_PER_SLOT = SAMPLE_RATE_HZ * SLOT_SECONDS  # 180 000
MAX_DECODES_PER_SLOT = 50


@dataclass(frozen=True, slots=True)
class ShimDecode:
    """One raw decode as returned by ``ft8_shim_decode_slot``."""

    message: str        # decoded text, e.g. "CQ DK9XR JN58" or "DK9XR W1AW FN31"
    snr_db_est: int     # rough SNR estimate (~ WSJT-X dB)
    dt_s: float         # time offset in seconds
    freq_hz: float      # audio-band frequency
    score: int          # raw Costas sync score


def decode_slot(pcm_s16le: bytes) -> list[ShimDecode]:
    """Decode one 15-second slot of audio.

    *pcm_s16le* must be little-endian signed 16-bit PCM, mono, 12000 Hz,
    exactly :data:`SAMPLES_PER_SLOT` samples (= 360 000 bytes). Slightly
    longer inputs are accepted (extra samples are ignored); shorter
    inputs raise :class:`ValueError`.
    """
    expected_bytes = SAMPLES_PER_SLOT * 2
    if len(pcm_s16le) < expected_bytes:
        raise ValueError(
            f"need at least {expected_bytes} bytes ({SAMPLES_PER_SLOT} samples), "
            f"got {len(pcm_s16le)}"
        )

    pcm_buf = ffi.from_buffer("int16_t[]", pcm_s16le[:expected_bytes])
    out_buf = ffi.new(f"ft8_shim_result_t[{MAX_DECODES_PER_SLOT}]")

    n = lib.ft8_shim_decode_slot(pcm_buf, SAMPLES_PER_SLOT, out_buf, MAX_DECODES_PER_SLOT)
    if n < 0:
        raise RuntimeError("ft8_shim_decode_slot failed")

    results: list[ShimDecode] = []
    for i in range(n):
        r = out_buf[i]
        results.append(
            ShimDecode(
                message=ffi.string(r.message).decode("ascii", errors="replace").strip(),
                snr_db_est=int(r.snr_db_est),
                dt_s=float(r.dt_s),
                freq_hz=float(r.freq_hz),
                score=int(r.score),
            )
        )
    return results


def decode_slot_ap(
    pcm_s16le: bytes,
    ap_callsigns: list[str] | None = None,
) -> list[ShimDecode]:
    """Decode one FT8 slot with a-priori callsign hints.

    Sweep-B feature: pass a list of callsigns the orchestrator knows
    are in play (own call, current QSO partner) so the LDPC decoder
    can pin those soft bits and pull weaker decodes out of the noise.

    The C shim currently delegates to :func:`decode_slot` — the AP
    soft-bit pinning lands in the next iteration. This wrapper is the
    stable Python-side surface so the orchestrator can wire the hook
    today and benefit automatically once the C side fills in.
    """
    expected_bytes = SAMPLES_PER_SLOT * 2
    if len(pcm_s16le) < expected_bytes:
        raise ValueError(
            f"need at least {expected_bytes} bytes ({SAMPLES_PER_SLOT} samples), "
            f"got {len(pcm_s16le)}"
        )

    pcm_buf = ffi.from_buffer("int16_t[]", pcm_s16le[:expected_bytes])
    out_buf = ffi.new(f"ft8_shim_result_t[{MAX_DECODES_PER_SLOT}]")

    if ap_callsigns:
        joined = " ".join(c.upper() for c in ap_callsigns).encode("ascii", errors="ignore")
        ap_arg = ffi.new("char[]", joined)
        ap_len = len(joined)
    else:
        ap_arg = ffi.NULL
        ap_len = 0

    n = lib.ft8_shim_decode_slot_ap(
        pcm_buf, SAMPLES_PER_SLOT, ap_arg, ap_len, out_buf, MAX_DECODES_PER_SLOT
    )
    if n < 0:
        raise RuntimeError("ft8_shim_decode_slot_ap failed")

    return _shim_results_to_list(out_buf, n)


def decode_slot_multipass(
    pcm_s16le: bytes,
    num_passes: int = 2,
) -> list[ShimDecode]:
    """Decode one FT8 slot using multi-pass + subtract.

    Sweep-B feature: after pass 1, synthesise+subtract decoded signals
    and re-run the decoder on the residual. ``num_passes >= 2``
    recovers decodes hidden by stronger neighbouring signals.

    Currently the shim delegates to single-pass — the subtract loop
    lands once ft8_lib exposes a residual-buffer hook.
    """
    expected_bytes = SAMPLES_PER_SLOT * 2
    if len(pcm_s16le) < expected_bytes:
        raise ValueError(
            f"need at least {expected_bytes} bytes ({SAMPLES_PER_SLOT} samples), "
            f"got {len(pcm_s16le)}"
        )

    pcm_buf = ffi.from_buffer("int16_t[]", pcm_s16le[:expected_bytes])
    out_buf = ffi.new(f"ft8_shim_result_t[{MAX_DECODES_PER_SLOT}]")

    n = lib.ft8_shim_decode_slot_multipass(
        pcm_buf, SAMPLES_PER_SLOT, max(1, int(num_passes)), out_buf, MAX_DECODES_PER_SLOT
    )
    if n < 0:
        raise RuntimeError("ft8_shim_decode_slot_multipass failed")

    return _shim_results_to_list(out_buf, n)


def _shim_results_to_list(out_buf, n: int) -> list[ShimDecode]:
    """Common decoder for the ft8_shim_result_t arrays our shims write."""
    results: list[ShimDecode] = []
    for i in range(n):
        r = out_buf[i]
        results.append(
            ShimDecode(
                message=ffi.string(r.message).decode("ascii", errors="replace").strip(),
                snr_db_est=int(r.snr_db_est),
                dt_s=float(r.dt_s),
                freq_hz=float(r.freq_hz),
                score=int(r.score),
            )
        )
    return results


# FT4 slot is 7.5 s — half of FT8 — same 12 kHz mono S16LE format.
SAMPLES_PER_SLOT_FT4 = (SAMPLE_RATE_HZ * 75) // 10  # 90 000


def decode_slot_ft4(pcm_s16le: bytes) -> list[ShimDecode]:
    """Decode one 7.5-second FT4 slot of audio.

    Same format as :func:`decode_slot` but expects exactly
    :data:`SAMPLES_PER_SLOT_FT4` samples (= 180 000 bytes).
    """
    expected_bytes = SAMPLES_PER_SLOT_FT4 * 2
    if len(pcm_s16le) < expected_bytes:
        raise ValueError(
            f"need at least {expected_bytes} bytes "
            f"({SAMPLES_PER_SLOT_FT4} samples), got {len(pcm_s16le)}"
        )

    pcm_buf = ffi.from_buffer("int16_t[]", pcm_s16le[:expected_bytes])
    out_buf = ffi.new(f"ft8_shim_result_t[{MAX_DECODES_PER_SLOT}]")

    n = lib.ft4_shim_decode_slot(pcm_buf, SAMPLES_PER_SLOT_FT4, out_buf, MAX_DECODES_PER_SLOT)
    if n < 0:
        raise RuntimeError("ft4_shim_decode_slot failed")

    results: list[ShimDecode] = []
    for i in range(n):
        r = out_buf[i]
        results.append(
            ShimDecode(
                message=ffi.string(r.message).decode("ascii", errors="replace").strip(),
                snr_db_est=int(r.snr_db_est),
                dt_s=float(r.dt_s),
                freq_hz=float(r.freq_hz),
                score=int(r.score),
            )
        )
    return results


# ============================================================================
# TX synthesis (ft8_shim_synth_message)
# ============================================================================

TX_SAMPLES = 79 * 1920  # FT8: 79 symbols * 1920 samples/symbol @ 12 kHz = 151 680
# FT4: 105 symbols * 576 samples/symbol @ 12 kHz = 60 480 samples (~5 s)
# fits comfortably inside the 7.5 s FT4 slot.
TX_SAMPLES_FT4 = 105 * 576


def synth_message(
    text: str,
    audio_freq_hz: float = 1500.0,
    amplitude: float = 0.9,
) -> bytes:
    """Synthesise an FT8 message into 12 kHz mono S16LE PCM.

    Returned bytes contain exactly :data:`TX_SAMPLES` samples
    (~12.6 seconds) — a touch shorter than a 15-s slot, matching the
    reference ``gen_ft8`` output. The caller can pad with silence to a
    full slot for transmission, but the audio itself is the FT8 burst.
    """
    text_bytes = text.encode("ascii")
    out_buf = ffi.new(f"int16_t[{TX_SAMPLES}]")
    n = lib.ft8_shim_synth_message(text_bytes, audio_freq_hz, amplitude, out_buf, TX_SAMPLES)
    if n < 0:
        raise FT8EncodeError(f"ft8_shim_synth_message failed for {text!r}")
    return bytes(ffi.buffer(out_buf, n * 2))


def synth_message_ft4(
    text: str,
    audio_freq_hz: float = 1500.0,
    amplitude: float = 0.9,
) -> bytes:
    """Synthesise an FT4 message into 12 kHz mono S16LE PCM.

    Returned bytes contain exactly :data:`TX_SAMPLES_FT4` samples
    (~5 seconds) — the FT4 burst is shorter than its 7.5 s slot. The
    caller pads with silence to align with the slot boundary.
    """
    text_bytes = text.encode("ascii")
    out_buf = ffi.new(f"int16_t[{TX_SAMPLES_FT4}]")
    n = lib.ft4_shim_synth_message(text_bytes, audio_freq_hz, amplitude, out_buf, TX_SAMPLES_FT4)
    if n < 0:
        raise FT8EncodeError(f"ft4_shim_synth_message failed for {text!r}")
    return bytes(ffi.buffer(out_buf, n * 2))
