"""Nicht-Standard-Rufzeichen (2026-09-06).

Gefunden beim Benchmark der zweiten Subtract-Runde: ein synthetischer
Slot mit "CQ W1WEAK FN31" hat den Python-Prozess mit "stack smashing
detected" abgeschossen. shim_lookup_hash schrieb 14 Bytes in den
char[12]-Puffer von ft8_lib (message.c lookup_callsign). Jeder decodierte
Hash-Call mit Treffer in der Hint-Tabelle haette live den Controller
gekillt — und seit den Hint-Feeds ist die Tabelle immer gefuellt.
"""

from __future__ import annotations

import numpy as np
import pytest

from ft8_appliance.decode.ft8_native import SAMPLES_PER_SLOT, decode_slot, decode_slot_v2, synth_message


def _slot(text: str, hz: float = 1500.0) -> bytes:
    buf = np.zeros(SAMPLES_PER_SLOT, dtype=np.int16)
    pcm = np.frombuffer(synth_message(text, hz, 0.2), dtype=np.int16)
    buf[: len(pcm)] = pcm
    return buf.tobytes()


@pytest.mark.parametrize("text", ["CQ W1WEAK FN31", "CQ K1STRONG FN42", "DK9XR W1WEAK -10"])
def test_nonstandard_callsigns_decode_without_smashing_the_stack(text: str) -> None:
    # Encode legt den Call in die Hash-Tabelle; Decode findet ihn dort wieder
    # (lookup_hash) — genau der Pfad, der vorher den Stack ueberschrieb.
    pcm = _slot(text)
    call = text.split()[1] if text.startswith("CQ") else text.split()[1]
    # ft8_lib gibt den aufgeloesten Hash-Call in spitzen Klammern zurueck: "CQ <W1WEAK> FN31"
    assert any(call in d.message for d in decode_slot(pcm))
    assert any(call in d.message for d in decode_slot_v2(pcm, "extreme"))
