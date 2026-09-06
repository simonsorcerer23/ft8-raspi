"""OSD — ordered statistics decoding im Hint-Pass (2026-09-06).

Auf den WSJT-X-Referenzaufnahmen in vendor/ft8_lib/test/wav trifft der
extreme-Modus 79 % der WSJT-X-Decodes; die Luecke liegt unter -13 dB, wo
WSJT-X mit OSD arbeitet. Unser OSD (Ordnung 1 + Ordnung 2 auf den 60
unsichersten Infobits) holt auf dem Korpus mehrere Decodes und ist dreifach
abgesichert: CRC-14, Naehe zur harten Entscheidung (nhard <= 32,
Metrik <= 60) und das Known-Call-Gate — das vorher zirkulaer war, weil
ftx_message_decode die gerade entpackten Calls selbst in die Tabelle
schrieb ("5J4ZBT 45PQH NB60" ging so als "bekannt" durch).

Der Test laeuft ueber den ganzen Korpus, weil sich WELCHE Nachricht erst
per OSD faellt mit jeder Decoder-Aenderung verschiebt (Fenster, osr,
Subtraktion) — die Aussage "OSD holt WSJT-X-bestaetigte Decodes, die BP
verfehlt, und nimmt nichts weg" bleibt.
"""

from __future__ import annotations

import pathlib
import re
import wave

import numpy as np
import pytest

from ft8_appliance.decode.ft8_native import SAMPLES_PER_SLOT, decode_slot_v2, get_pass_stats, lib, reset_pass_stats

WAVS = pathlib.Path(__file__).resolve().parents[2] / "vendor" / "ft8_lib" / "test" / "wav"


def _norm(m: str) -> str:
    return " ".join(m.replace("<", "").replace(">", "").split())


def _corpus() -> list[tuple[str, bytes, set[str]]]:
    out = []
    for wav in sorted(WAVS.glob("*.wav")):
        txt = wav.with_suffix(".txt")
        if not txt.exists():
            continue
        with wave.open(str(wav)) as w:
            if w.getframerate() != 12000:
                continue
            x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
            if w.getnchannels() > 1:
                x = x[:: w.getnchannels()]
        buf = np.zeros(SAMPLES_PER_SLOT, np.int16)
        buf[: min(len(x), SAMPLES_PER_SLOT)] = x[:SAMPLES_PER_SLOT]
        ref = set()
        for line in txt.read_text().splitlines():
            m = re.match(r"^\d{6}\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+~\s+(.*?)(?:\s{2,}.*)?$", line.rstrip())
            if m:
                ref.add(_norm(m.group(4)))
        if ref:
            out.append((wav.name, buf.tobytes(), ref))
    return out


@pytest.fixture
def _osd_reset():
    yield
    lib.ft8_shim_set_osd_depth(2)


def test_osd_recovers_wsjtx_decodes_that_bp_misses_and_loses_nothing(_osd_reset) -> None:
    corpus = _corpus()
    assert len(corpus) >= 20
    # "bekannt" = alle Calls, die WSJT-X in der Session gehoert hat (wie live: Heard-Liste + PSK)
    for _, _, ref in corpus:
        for msg in ref:
            for tok in msg.split():
                if re.match(r"^[A-Z0-9]{3,}[0-9][A-Z]+$", tok) and tok not in ("CQ", "DX", "RR73", "RRR", "73"):
                    lib.ft8_shim_hash_table_save(tok.encode(), 0)
    lib.ft8_shim_set_osd_depth(0)
    without = {name: {_norm(d.message) for d in decode_slot_v2(pcm, "extreme")} for name, pcm, _ in corpus}
    lib.ft8_shim_set_osd_depth(2); reset_pass_stats()
    with_osd = {name: {_norm(d.message) for d in decode_slot_v2(pcm, "extreme")} for name, pcm, _ in corpus}
    ref_by = {name: ref for name, _, ref in corpus}
    def covered(msg: str, pool: set[str]) -> bool:
        # "... ON7EE JO10" (Hash nicht aufloesbar) gilt als getroffen, wenn
        # "OR18RSX ON7EE JO10" da ist — mehr Wissen ist kein Verlust.
        if msg in pool:
            return True
        toks = [t for t in msg.split() if t != "..."]
        return any(all(t in m.split() for t in toks) for m in pool)

    gained = sum(1 for n in ref_by for m in ref_by[n] if covered(m, with_osd[n]) and not covered(m, without[n]))
    lost = sum(1 for n in ref_by for m in ref_by[n] if covered(m, without[n]) and not covered(m, with_osd[n]))
    assert get_pass_stats()["pass_osd"] >= 3
    assert gained >= 3          # WSJT-X-bestaetigte Decodes, die BP allein verfehlt
    assert lost == 0            # OSD nimmt nichts weg


def test_osd_depth_setter_clamps() -> None:
    lib.ft8_shim_set_osd_depth(7); assert lib.ft8_shim_get_osd_depth() == 2
    lib.ft8_shim_set_osd_depth(-1); assert lib.ft8_shim_get_osd_depth() == 0
    lib.ft8_shim_set_osd_depth(2)
