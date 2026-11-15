#!/usr/bin/env python3
"""Beide Stationen nebeneinander, in den Stunden, in denen beide liefen.

Seit 2026-11-15: Raymonds Pi5 (DK9XR, IC-7300, Dipol, meist 20 m) und der
MK2 (DO3XR, IC-7300MK2, Richtantenne am Mast in der Firma, meist 15 m)
laufen gleichzeitig. Das ist kein sauberer Antennenvergleich — Band,
Rufzeichen und Standort sind verschieden —, aber zur selben Uhrzeit
nebeneinander gelesen sagt es mehr als Oktober gegen November.

    ./scripts/stationsvergleich.py               # ft8-mk2 gegen ft8-pi5
    ./scripts/stationsvergleich.py --tage 3

Der aussagekraeftigste Teil ist der letzte: Empfaenger, die in derselben
Stunde BEIDE Stationen gemeldet haben. Jeder ist sein eigener Vergleich
(gleicher Empfaenger, gleiche Stunde), uebrig bleiben Antenne, Band und
Standort. Haengt der Unterschied stark von der Richtung ab, in der der
Empfaenger liegt, ist das die Handschrift der Richtantenne — ein Band-
oder Standortunterschied wuerde in alle Richtungen ungefaehr gleich wirken.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ft8_appliance.analyse.richtung import peilung  # noqa: E402

FERN = "/var/lib/ft8-appliance/qso.sqlite"
EIGENES_FELD = "JN58"
MIN_DECODES_JE_STUNDE = 50


def hole(host: str, tage: int) -> sqlite3.Connection:
    """Nur die noetigen Zeilen der letzten Tage, auf dem Pi zusammengestellt.

    Die ganze Datenbank waehrend des Betriebs mit cat zu kopieren ging beim
    Pi5 (400 MB) schief: Die Kopie war in sich nicht stimmig ("malformed").
    SQLite liest die Quelle hier nur lesend und schreibt in eine kleine
    Datei unter /tmp, die danach geholt und geloescht wird.
    """
    seit = f"datetime('now', '-{int(tage)} days')"
    sql = (
        f"ATTACH 'file:{FERN}?mode=ro' AS q; "
        f"CREATE TABLE decode AS SELECT ts, band FROM q.decode WHERE ts >= {seit}; "
        f"CREATE TABLE psk_reporter_in AS SELECT ts, rx_call, rx_grid, snr_db, band "
        f"FROM q.psk_reporter_in WHERE ts >= {seit}; "
        f"CREATE TABLE qso AS SELECT id, qso_end, station_callsign FROM q.qso "
        f"WHERE qso_end >= {seit};"
    )
    tmp = "/tmp/stationsvergleich.sqlite"
    befehl = (f"rm -f {tmp} && sqlite3 {tmp} \"{sql}\" && cat {tmp}; rc=$?; rm -f {tmp}; exit $rc")
    ziel = Path(tempfile.mkdtemp()) / "auszug.sqlite"
    subprocess.run(["ssh", "-o", "BatchMode=yes", host, f"sudo bash -c {sh_quote(befehl)}"],
                   stdout=ziel.open("wb"), check=True, timeout=300)
    return sqlite3.connect(f"file:{ziel}?mode=ro", uri=True)


def sh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def tabelle(zeilen: list[tuple], kopf: tuple[str, ...]) -> None:
    if not zeilen:
        print("    (keine Daten)")
        return
    b = [max(len(str(k)), *(len(str(z[i])) for z in zeilen)) for i, k in enumerate(kopf)]
    print("    " + "  ".join(str(k).ljust(w) for k, w in zip(kopf, b)))
    print("    " + "  ".join("-" * w for w in b))
    for z in zeilen:
        print("    " + "  ".join(str(v).ljust(w) for v, w in zip(z, b)))


def betriebsstunden(con, seit: str) -> set[str]:
    return {h for (h,) in con.execute(
        "select strftime('%Y-%m-%d %H', ts) h from decode where ts >= ? "
        "group by h having count(*) >= ?", (seit, MIN_DECODES_JE_STUNDE))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="ft8-mk2", help="SSH-Ziel der ersten Station")
    ap.add_argument("--b", default="ft8-pi5", help="SSH-Ziel der zweiten Station")
    ap.add_argument("--tage", type=int, default=7)
    args = ap.parse_args()

    con = {args.a: hole(args.a, args.tage), args.b: hole(args.b, args.tage)}
    seit = con[args.a].execute(
        "select datetime('now', ?)", (f"-{args.tage} days",)).fetchone()[0]
    stunden = betriebsstunden(con[args.a], seit) & betriebsstunden(con[args.b], seit)
    print(f"\n=== Stunden, in denen beide liefen (je >= {MIN_DECODES_JE_STUNDE} Decodes) ===")
    if not stunden:
        print("    keine — noch nie gleichzeitig in Betrieb")
        return
    print(f"    {len(stunden)} Stunden, {min(stunden)} bis {max(stunden)} UTC")
    platz = ",".join("?" * len(stunden))
    liste = sorted(stunden)

    print("\n=== Je Station in diesen Stunden ===")
    zeilen = []
    for host, c in con.items():
        call = (c.execute("select station_callsign from qso order by id desc limit 1")
                .fetchone() or ("?",))[0]
        baender = ", ".join(b for (b,) in c.execute(
            f"select band from decode where strftime('%Y-%m-%d %H', ts) in ({platz}) "
            "group by band order by count(*) desc limit 2", liste))
        qsos = c.execute(
            f"select count(*) from qso where strftime('%Y-%m-%d %H', qso_end) in ({platz})",
            liste).fetchone()[0]
        dec = c.execute(
            f"select count(*) from decode where strftime('%Y-%m-%d %H', ts) in ({platz})",
            liste).fetchone()[0]
        hoerer = c.execute(
            f"select count(distinct strftime('%Y-%m-%d %H', ts) || rx_call) from psk_reporter_in "
            f"where strftime('%Y-%m-%d %H', ts) in ({platz})", liste).fetchone()[0]
        snrs = [s for (s,) in c.execute(
            f"select snr_db from psk_reporter_in where snr_db is not null "
            f"and strftime('%Y-%m-%d %H', ts) in ({platz})", liste)]
        n = len(stunden)
        zeilen.append((host, call, baender, f"{qsos / n:.1f}", f"{dec / n:.0f}",
                       f"{hoerer / n:.0f}",
                       f"{statistics.median(snrs):.0f}" if snrs else "-"))
    tabelle(zeilen, ("Station", "Call", "Band", "QSOs/Std", "Decodes/Std",
                     "Hoerer/Std", "SNR-Median dB"))

    print("\n=== Dieselben Empfaenger, dieselbe Stunde: wie viel lauter ist "
          f"{args.a} als {args.b}? ===")
    meld: dict[str, dict[tuple[str, str], list[int]]] = {}
    gitter: dict[str, str] = {}
    for host, c in con.items():
        m: dict[tuple[str, str], list[int]] = defaultdict(list)
        for h, rx, grid, snr in c.execute(
                f"select strftime('%Y-%m-%d %H', ts), rx_call, rx_grid, snr_db "
                f"from psk_reporter_in where snr_db is not null "
                f"and strftime('%Y-%m-%d %H', ts) in ({platz})", liste):
            m[(h, rx)].append(snr)
            if grid:
                gitter[rx] = grid
        meld[host] = m
    gemeinsam = set(meld[args.a]) & set(meld[args.b])
    je_richtung: dict[int, list[float]] = defaultdict(list)
    alle: list[float] = []
    for key in gemeinsam:
        d = (statistics.median(meld[args.a][key]) - statistics.median(meld[args.b][key]))
        alle.append(d)
        p = peilung(EIGENES_FELD, gitter.get(key[1], ""))
        if p is not None and p[1] >= 500:
            je_richtung[int(((p[0] + 30) % 360) // 60) * 60].append(d)
    if not alle:
        print("    kein Empfaenger hat beide in derselben Stunde gemeldet")
        return
    print(f"    {len(alle)} Paare (Empfaenger x Stunde), Median {statistics.median(alle):+.1f} dB")
    tabelle([(f"{s:3d}° ±30", len(v), f"{statistics.median(v):+.1f}")
             for s, v in sorted(je_richtung.items())],
            ("Richtung vom Feld", "Paare", "Unterschied dB (Median)"))
    print("    Positiv = die erste Station kommt bei denselben Empfaengern lauter an.")
    print("    Band und Standort wirken in alle Richtungen; ein Unterschied, der stark")
    print("    von der Richtung abhaengt, kommt von der Antenne und wohin sie zeigt.")


if __name__ == "__main__":
    main()
