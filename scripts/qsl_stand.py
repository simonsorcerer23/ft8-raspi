#!/usr/bin/env python3
"""Wie viele Verbindungen sind bestaetigt — und um wie viel hat sich das bewegt?

    ./scripts/qsl_stand.py                  # Stand von DK9XR
    ./scripts/qsl_stand.py --call DO3XR
    ./scripts/qsl_stand.py --merken         # Stand als Vergleichspunkt sichern

Gefragt wird die QRZ-Logbook-API mit ``ACTION=STATUS``; sie liefert
Gesamtzahl, bestaetigte Verbindungen und die Zahl der DXCC-Gebiete in
einer einzigen Antwort, ohne das Logbuch herunterzuladen.

**Was QRZ als bestaetigt zaehlt**, steht in deren Hilfe: entweder der
eigene Double-Blind-Abgleich — beide Seiten haben dieselbe Verbindung
unabhaengig voneinander ins QRZ-Logbuch geschrieben, Rufzeichen, Band,
Modus und Zeit auf dreissig Minuten genau — oder eine Bestaetigung, die
**direkt aus LoTW importiert** wurde. eQSL, ClubLog und Papierkarten
zaehlen dort nicht mit. Der LoTW-Import laeuft nicht von selbst: er wird
im QRZ-Logbuch unter Settings angestossen.

Deshalb ist dieses Skript das Messinstrument fuer die Frage "was hat
LoTW gebracht": einmal vor dem Import mit ``--merken`` laufen lassen,
nach dem Import erneut, und die Differenz steht da.

Der API-Key wird auf dem Pi aus der Konfiguration gelesen und **nie**
ausgegeben — auch nicht in Fehlermeldungen.
"""
from __future__ import annotations

import argparse
import os
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# SSH-Ziel der Station. Der Tailscale-Name genuegt; abweichende
# Installationen setzen FT8_PI_SSH (etwa "pi@192.168.1.50").
PI = os.environ.get("FT8_PI_SSH", "ft8-pi5")
KONFIG = "/etc/ft8-appliance/config.yaml"
STAND = Path.home() / ".cache" / "ft8-qsl-stand.json"

# Das Abfrageprogramm laeuft auf dem Pi, damit der Key das Geraet nicht
# verlaesst. Es gibt ausschliesslich die Zaehlerzeilen aus.
FERNPROGRAMM = r"""
import re, sys, urllib.parse, urllib.request
ziel = sys.argv[1].upper()
txt = open("/etc/ft8-appliance/config.yaml").read()
key = None
for block in txt.split("- callsign:"):
    if block.strip().upper().startswith(ziel):
        m = re.search(r"qrz_logbook_api_key:\s*([^\s#]+)", block)
        if m:
            key = m.group(1).strip().strip("\"'")
if not key:
    print("FEHLER kein QRZ-Logbook-Key fuer " + ziel); sys.exit(1)
daten = urllib.parse.urlencode({"KEY": key, "ACTION": "STATUS"}).encode()
try:
    roh = urllib.request.urlopen(
        urllib.request.Request("https://logbook.qrz.com/api", data=daten),
        timeout=30).read().decode()
except Exception as exc:
    # Der Key koennte in einer Fehlermeldung stecken (etwa in einer
    # zurueckgegebenen URL) — deshalb nur den Ausnahmetyp nennen.
    print("FEHLER Abruf gescheitert: " + type(exc).__name__); sys.exit(1)
for teil in roh.split("&"):
    if key and key in teil:
        continue
    print(teil)
"""


def frag_qrz(call: str) -> dict[str, str]:
    lauf = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", PI, "python3", "-", call],
        input=FERNPROGRAMM, capture_output=True, text=True, timeout=90,
    )
    if lauf.returncode != 0 and not lauf.stdout.strip():
        sys.exit(f"Abruf gescheitert: {lauf.stderr.strip()[:200]}")
    werte: dict[str, str] = {}
    for zeile in lauf.stdout.splitlines():
        if zeile.startswith("FEHLER"):
            sys.exit(zeile)
        if "=" in zeile:
            k, _, v = zeile.partition("=")
            werte[k.strip()] = v.strip()
    if werte.get("RESULT") != "OK":
        sys.exit(f"QRZ meldet: {werte.get('REASON', werte.get('RESULT', '?'))}")
    return werte


def lies_stand() -> dict:
    try:
        return json.loads(STAND.read_text())
    except (OSError, ValueError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--call", default="DK9XR", help="Operator (Vorgabe DK9XR)")
    ap.add_argument("--merken", action="store_true",
                    help="diesen Stand als Vergleichspunkt sichern")
    args = ap.parse_args()

    w = frag_qrz(args.call)
    gesamt = int(w.get("COUNT", 0))
    best = int(w.get("CONFIRMED", 0))
    dxcc = int(w.get("DXCC_COUNT", 0))
    quote = (100.0 * best / gesamt) if gesamt else 0.0

    print(f"\n{w.get('BOOK_NAME', args.call)} — Stand "
          f"{datetime.now(UTC):%d.%m.%Y %H:%M} UTC")
    print(f"  Verbindungen   {gesamt:6d}")
    print(f"  bestaetigt     {best:6d}   ({quote:.1f} %)")
    print(f"  DXCC-Gebiete   {dxcc:6d}")

    alt = lies_stand().get(args.call.upper())
    if alt:
        d_ges = gesamt - alt["gesamt"]
        d_best = best - alt["bestaetigt"]
        d_dxcc = dxcc - alt["dxcc"]
        print(f"\n  seit {alt['zeit'][:16].replace('T', ' ')} UTC:"
              f"  {d_ges:+d} Verbindungen,"
              f"  {d_best:+d} bestaetigt,"
              f"  {d_dxcc:+d} DXCC")
        if d_best > d_ges:
            # Vorsicht mit der Deutung: Ein Ueberschuss heisst nur, dass
            # AELTERE Verbindungen bestaetigt wurden. Das macht QRZs
            # eigener Abgleich laufend von allein, sobald die Gegenseite
            # ihr Log nachtraegt — es ist kein Beleg fuer einen
            # LoTW-Import. Bei kleinen Zahlen ist es schlicht Rauschen.
            print(f"  → {d_best - d_ges} Bestaetigung(en) mehr als neue"
                  " Verbindungen, also fuer aeltere QSOs.")
            print("     Das macht QRZs eigener Abgleich auch ohne LoTW."
                  " Ob der Import gewirkt hat,")
            print("     zeigt erst ein Sprung in der Groessenordnung"
                  " der hochgeladenen Menge.")
    elif not args.merken:
        print("\n  Kein Vergleichspunkt gesichert."
              " Mit --merken einen setzen.")

    if args.merken:
        daten = lies_stand()
        daten[args.call.upper()] = {
            "zeit": datetime.now(UTC).isoformat(timespec="seconds"),
            "gesamt": gesamt, "bestaetigt": best, "dxcc": dxcc,
        }
        STAND.parent.mkdir(parents=True, exist_ok=True)
        STAND.write_text(json.dumps(daten, indent=2))
        print(f"\n  Vergleichspunkt gesichert in {STAND}")

    print("\n  QRZ zaehlt nur den eigenen Abgleich und direkt importierte"
          "\n  LoTW-Bestaetigungen. eQSL und ClubLog stehen dort nie drin.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
