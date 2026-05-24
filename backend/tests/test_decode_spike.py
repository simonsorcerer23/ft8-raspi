"""Phase-B spike test.

Asserts that our cffi binding produces the same FT8 symbol sequence as the
reference ``gen_ft8`` binary from ft8_lib does. That is the load-bearing
proof that the toolchain is wired up correctly end-to-end.
"""

from __future__ import annotations

import pytest

ft8_native = pytest.importorskip(
    "ft8_appliance.decode.ft8_native",
    reason="run `python -m ft8_appliance.decode._build_ft8` first",
)


# Tone sequence produced by `./gen_ft8 "CQ DK9XR JN58" cq.wav 1500` (gen_ft8
# prints them as "FSK tones: ..." with one digit per symbol).
# 79 symbols total = 7 (Costas) + 29 (data) + 7 (Costas) + 29 (data) + 7 (Costas).
EXPECTED_TONES_CQ_DK9XR_JN58 = (
    "3140652000000001045302441110556537273140652416030557033132416327071226023140652"
)
assert len(EXPECTED_TONES_CQ_DK9XR_JN58) == 79


def test_encode_message_text_roundtrip() -> None:
    msg = ft8_native.encode_text("CQ DK9XR JN58")

    # payload is 10 bytes
    assert len(msg.payload) == 10

    # tones: 79 symbols, each 0..7
    assert len(msg.tones) == 79
    assert all(0 <= t <= 7 for t in msg.tones)

    actual_str = "".join(str(t) for t in msg.tones)
    assert actual_str == EXPECTED_TONES_CQ_DK9XR_JN58, (
        f"tones differ from gen_ft8 reference:\n"
        f"  expected: {EXPECTED_TONES_CQ_DK9XR_JN58}\n"
        f"  got:      {actual_str}"
    )


def test_payload_decode_roundtrip() -> None:
    text = "CQ DK9XR JN58"
    enc = ft8_native.encode_text(text)
    decoded = ft8_native.decode_payload(enc.payload)
    assert decoded == text
