"""OSD — ordered statistics decoding im Hint-Pass (2026-09-06).

Auf den WSJT-X-Referenzaufnahmen in vendor/ft8_lib/test/wav trifft der
extreme-Modus 79 % der WSJT-X-Decodes; die Luecke liegt unter -13 dB, wo
WSJT-X mit OSD arbeitet. Unser OSD (Ordnung 1 + Ordnung 2 auf den 60
unsichersten Infobits) holt auf dem Korpus +6 Decodes und ist dreifach
abgesichert: CRC-14, Naehe zur harten Entscheidung (nhard <= 32,
Metrik <= 60) und das Known-Call-Gate — das vorher zirkulaer war, weil
ftx_message_decode die gerade entpackten Calls selbst in die Tabelle
schrieb ("5J4ZBT 45PQH NB60" ging so als "bekannt" durch).
"""

from __future__ import annotations

import pathlib
import wave

import numpy as np
import pytest

from ft8_appliance.decode.ft8_native import SAMPLES_PER_SLOT, decode_slot_v2, get_pass_stats, lib, reset_pass_stats

WAVS = pathlib.Path(__file__).resolve().parents[2] / "vendor" / "ft8_lib" / "test" / "wav"


def _pcm(name: str) -> bytes:
    with wave.open(str(WAVS / name)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    buf = np.zeros(SAMPLES_PER_SLOT, np.int16)
    buf[: min(len(x), SAMPLES_PER_SLOT)] = x[:SAMPLES_PER_SLOT]
    return buf.tobytes()


@pytest.fixture
def known_calls():
    for c in ("DL8FBD", "LZ2KV", "DB4BU", "DK5OK"):
        lib.ft8_shim_hash_table_save(c.encode(), 0)
    yield
    lib.ft8_shim_set_osd_depth(2)


@pytest.mark.parametrize("wav,msg", [
    ("websdr_test6.wav", "DL8FBD LZ2KV -16"),   # WSJT-X: -16 dB
    ("websdr_test5.wav", "DB4BU DK5OK RR73"),   # WSJT-X: 0 dB, von BP trotzdem verfehlt
])
def test_osd_recovers_a_wsjtx_decode_that_bp_misses(known_calls, wav, msg) -> None:
    pcm = _pcm(wav)
    lib.ft8_shim_set_osd_depth(0); reset_pass_stats()
    without = {d.message for d in decode_slot_v2(pcm, "extreme")}
    lib.ft8_shim_set_osd_depth(2); reset_pass_stats()
    with_osd = {d.message for d in decode_slot_v2(pcm, "extreme")}
    assert msg not in without
    assert msg in with_osd
    assert get_pass_stats()["pass_osd"] >= 1
    assert without <= with_osd                       # OSD nimmt nichts weg


def test_osd_depth_setter_clamps() -> None:
    lib.ft8_shim_set_osd_depth(7); assert lib.ft8_shim_get_osd_depth() == 2
    lib.ft8_shim_set_osd_depth(-1); assert lib.ft8_shim_get_osd_depth() == 0
    lib.ft8_shim_set_osd_depth(2)
