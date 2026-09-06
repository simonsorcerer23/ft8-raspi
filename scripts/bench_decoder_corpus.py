#!/usr/bin/env python3
"""Decoder-Benchmark gegen die WSJT-X-Referenzaufnahmen in vendor/ft8_lib/test/wav.

Misst, wie viele der WSJT-X-Decodes (die .txt neben jeder .wav) unser
Shim in einem Modus findet — die Messbasis der Kampagne vom 2026-09-06
(docs/decoder_evolution.md). Laeuft auf dem Entwicklungsrechner gegen die
lokal gebaute Extension:

    cd backend && .venv/bin/python ../scripts/bench_decoder_corpus.py [--mode extreme] [--osd 2] [--knob name=wert ...]

Die Hash-Tabelle wird mit allen Calls des Korpus gefuellt (wie live: Heard-
Liste + PSK-Empfaenger), damit Hint-Pass/OSD ihr Known-Call-Gate haben.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys
import time
import wave

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
from ft8_appliance.decode.ft8_native import (  # noqa: E402
    SAMPLES_PER_SLOT, decode_slot, decode_slot_v2, get_pass_stats, lib, reset_pass_stats,
)

WAVS = pathlib.Path(__file__).resolve().parents[1] / "vendor" / "ft8_lib" / "test" / "wav"
LINE = re.compile(r"^\d{6}\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+~\s+(.*?)(?:\s{2,}.*)?$")


def norm(m: str) -> str:
    return " ".join(m.replace("<", "").replace(">", "").split())


def covered(msg: str, pool: set[str]) -> bool:
    if msg in pool:
        return True
    toks = [t for t in msg.split() if t != "..."]
    return any(all(t in m.split() for t in toks) for m in pool)


def load_corpus() -> list[tuple[str, bytes, dict[str, int]]]:
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
        ref: dict[str, int] = {}
        for line in txt.read_text().splitlines():
            m = LINE.match(line.rstrip())
            if m:
                ref[norm(m.group(4))] = int(m.group(1))
        if ref:
            out.append((wav.name, buf.tobytes(), ref))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="extreme", choices=["standard", "deep", "multi", "extreme"])
    ap.add_argument("--osd", type=int, default=2)
    ap.add_argument("--ldpc-pct", type=int, default=150)
    ap.add_argument("--knob", action="append", default=[], help="name=wert (ft8_shim_set_knob)")
    ap.add_argument("--by-snr", action="store_true")
    args = ap.parse_args()
    corpus = load_corpus()
    for _, _, ref in corpus:
        for msg in ref:
            for tok in msg.split():
                if re.match(r"^[A-Z0-9]{3,}[0-9][A-Z]+$", tok) and tok not in ("CQ", "DX", "RR73", "RRR", "73"):
                    lib.ft8_shim_hash_table_save(tok.encode(), 0)
    lib.ft8_shim_set_osd_depth(args.osd)
    lib.ft8_shim_set_ldpc_factor(args.ldpc_pct)
    for kv in args.knob:
        name, val = kv.split("=")
        if lib.ft8_shim_set_knob(name.encode(), int(val)) != 0:
            print(f"unbekannter Knopf: {name}", file=sys.stderr)
            return 2
    # Warmlauf: fuellt die Hash-Tabelle mit allem, was der std-Pass hoert (wie live)
    for _, pcm, _ in corpus:
        decode_slot(pcm)
    reset_pass_stats()
    n_ref = hit = 0
    extra: list[str] = []
    found: collections.Counter = collections.Counter()
    missed: collections.Counter = collections.Counter()
    durations = []
    for name, pcm, ref in corpus:
        t0 = time.monotonic()
        if args.mode == "standard":
            dec = {norm(d.message) for d in decode_slot(pcm)}
        else:
            dec = {norm(d.message) for d in decode_slot_v2(pcm, args.mode)}
        durations.append(time.monotonic() - t0)
        n_ref += len(ref)
        for msg, snr in ref.items():
            b = (snr // 3) * 3
            if covered(msg, dec):
                hit += 1
                found[b] += 1
            else:
                missed[b] += 1
        extra += [f"{name}: {m}" for m in sorted(dec - set(ref)) if not any(covered(r, {m}) for r in ref)]
    st = get_pass_stats()
    print(f"mode={args.mode} osd={args.osd} ldpc={args.ldpc_pct}% knobs={args.knob}")
    print(f"  WSJT-X {n_ref} | wir treffen {hit} ({100 * hit / n_ref:.1f} %) | nur wir {len(extra)} | {np.mean(durations) * 1000:.0f} ms/Slot")
    print("  Pass-Stats:", {k: v for k, v in st.items() if v})
    if args.by_snr:
        for b in sorted(set(found) | set(missed)):
            f, m = found[b], missed[b]
            print(f"  SNR {b:+3d}..{b + 2:+3d}: gefunden {f:3d} verfehlt {m:3d}  ({100 * f / (f + m):.0f} %)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
