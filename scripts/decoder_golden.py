#!/usr/bin/env python3
"""Goldstandard-Vergleich des Decoders: jede Nachricht, jeder Wert, jede Reihenfolge.

Der Korpus-Benchmark (bench_decoder_corpus.py) zaehlt Treffer. Das reicht nicht,
um einen Umbau am Decoder abzunehmen: Zwei Builds koennen dieselben 312 Treffer
liefern und trotzdem verschiedene Nachrichten, Frequenzen oder Reihenfolgen
produzieren. Dieses Skript friert die komplette Ausgabe ein und vergleicht sie
spaeter Feld fuer Feld.

    cd backend
    .venv/bin/python ../scripts/decoder_golden.py --write /tmp/golden.json   # vor dem Umbau
    .venv/bin/python ../scripts/decoder_golden.py --check /tmp/golden.json   # danach

Das Protokoll (Reihenfolge der Modi und Aufnahmen, Fuellen der Hash-Tabelle,
Warmlauf) ist fest, weil die Hash-Tabelle mit jedem Decode waechst und spaetere
Ergebnisse beeinflusst. Beide Laeufe muessen exakt dasselbe tun.

Angelegt 2026-09-09 fuer den Multicore-Umbau: Ein parallelisierter Decoder muss
bit-identisch zum seriellen bleiben, unabhaengig von der Thread-Zahl.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import wave

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
from ft8_appliance.decode.ft8_native import (  # noqa: E402
    SAMPLES_PER_SLOT, decode_slot, decode_slot_v2, get_pass_stats, lib, reset_pass_stats,
)

WAVS = pathlib.Path(__file__).resolve().parents[1] / "vendor" / "ft8_lib" / "test" / "wav"
LINE = re.compile(r"^\d{6}\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+~\s+(.*?)(?:\s{2,}.*)?$")
MODES = ("standard_v1", "standard", "deep", "multi", "extreme")


def load_corpus() -> list[tuple[str, bytes, list[str]]]:
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
        ref = [m.group(4) for m in (LINE.match(l.rstrip()) for l in txt.read_text().splitlines()) if m]
        if ref:
            out.append((wav.name, buf.tobytes(), ref))
    return out


def run(corpus, knobs: list[str]) -> dict:
    # Identisch zum Benchmark: Hash-Tabelle aus dem Korpus, dann Warmlauf.
    for _, _, ref in corpus:
        for msg in ref:
            for tok in msg.split():
                if re.match(r"^[A-Z0-9]{3,}[0-9][A-Z]+$", tok) and tok not in ("CQ", "DX", "RR73", "RRR", "73"):
                    lib.ft8_shim_hash_table_save(tok.encode(), 0)
    lib.ft8_shim_set_osd_depth(2)
    lib.ft8_shim_set_ldpc_factor(150)
    for kv in knobs:
        name, val = kv.split("=")
        if lib.ft8_shim_set_knob(name.encode(), int(val)) != 0:
            raise SystemExit(f"unbekannter Knopf: {name}")
    for _, pcm, _ in corpus:
        decode_slot(pcm)

    result: dict = {"hash_table_after_warmup": lib.ft8_shim_hash_table_count(), "modes": {}}
    for mode in MODES:
        reset_pass_stats()
        per_file = {}
        for name, pcm, _ in corpus:
            decs = decode_slot(pcm) if mode == "standard_v1" else decode_slot_v2(pcm, mode)
            per_file[name] = [
                # Floats als repr: bit-genau, nicht gerundet.
                [d.message, d.snr_db_est, repr(d.dt_s), repr(d.freq_hz), d.score] for d in decs
            ]
        result["modes"][mode] = {"decodes": per_file, "pass_stats": get_pass_stats(),
                                 "hash_table_count": lib.ft8_shim_hash_table_count()}
    return result


def compare(ref: dict, cur: dict) -> list[str]:
    diffs: list[str] = []
    if ref["hash_table_after_warmup"] != cur["hash_table_after_warmup"]:
        diffs.append(f"Hash-Tabelle nach Warmlauf: {ref['hash_table_after_warmup']} vs {cur['hash_table_after_warmup']}")
    for mode in MODES:
        r, c = ref["modes"][mode], cur["modes"][mode]
        if r["pass_stats"] != c["pass_stats"]:
            diffs.append(f"[{mode}] Pass-Statistik: {r['pass_stats']} vs {c['pass_stats']}")
        if r["hash_table_count"] != c["hash_table_count"]:
            diffs.append(f"[{mode}] Hash-Tabelle: {r['hash_table_count']} vs {c['hash_table_count']}")
        for name in r["decodes"]:
            a, b = r["decodes"][name], c["decodes"].get(name)
            if a == b:
                continue
            if b is None:
                diffs.append(f"[{mode}] {name}: fehlt im Vergleichslauf")
                continue
            diffs.append(f"[{mode}] {name}: {len(a)} vs {len(b)} Decodes")
            for i in range(max(len(a), len(b))):
                x = a[i] if i < len(a) else None
                y = b[i] if i < len(b) else None
                if x != y:
                    diffs.append(f"    #{i}: {x}  |  {y}")
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", metavar="JSON")
    g.add_argument("--check", metavar="JSON")
    ap.add_argument("--knob", action="append", default=[], help="name=wert (ft8_shim_set_knob)")
    args = ap.parse_args()
    corpus = load_corpus()
    cur = run(corpus, args.knob)
    total = sum(len(v) for v in cur["modes"]["extreme"]["decodes"].values())
    if args.write:
        pathlib.Path(args.write).write_text(json.dumps(cur, indent=1, sort_keys=True))
        print(f"Referenz geschrieben: {args.write} ({len(corpus)} Aufnahmen, {len(MODES)} Modi, extreme: {total} Decodes)")
        return 0
    ref = json.loads(pathlib.Path(args.check).read_text())
    diffs = compare(ref, cur)
    if diffs:
        print(f"ABWEICHUNGEN ({len(diffs)}):")
        for d in diffs[:60]:
            print("  " + d)
        return 1
    print(f"IDENTISCH zur Referenz: {len(corpus)} Aufnahmen x {len(MODES)} Modi, extreme: {total} Decodes, Pass-Statistik gleich")
    return 0


if __name__ == "__main__":
    sys.exit(main())
