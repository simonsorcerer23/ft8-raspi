#!/usr/bin/env python3
"""Marinefunker-Mitgliederliste (MF-Dipl.Such-Abhakliste) Parser.

Liest die PDF von DF7PM und extrahiert NUR AKTIVE Mitglieder
(Austritt-Spalte leer) als JSON für mf_lookup.

Schema des JSON-Outputs:
    {
        "DK9XR": {"mfnr": 1234, "dok": "Z01", "since": "01.09.1977"},
        ...
    }

Sebastian 2026-05-26 v0.9.0: einmaliger Build-Step, JSON ins Repo.
Bei PDF-Updates Script neu laufen lassen.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pdfplumber

PDF_PATH = sys.argv[1] if len(sys.argv) > 1 else "/home/sebastian/Downloads/MF-Dipl.Suchl_.-MFNr.01.2026.pdf"
OUT_PATH = Path(__file__).parent.parent / "backend" / "ft8_appliance" / "data" / "marinefunker.json"

# Callsign-Validierung: 1-2 Zeichen Prefix (alphanum, mindestens 1 Buchstabe)
# + 1-2 Ziffern + 1-4 alphanum. Akzeptiert HB9DAB, OE3FLA, PA3GQV, K1ZZ,
# auch zifferngestartete Prefixe wie 4K1ADQ, 5P2BA, 4Z5LA, 9A1XX.
CALL_RE = re.compile(r"^[A-Z0-9]{1,2}\d{1,2}[A-Z0-9]{1,5}$")


def parse_pdf(pdf_path: str) -> dict[str, dict]:
    members: dict[str, dict] = {}
    skipped_swl = 0
    skipped_inactive = 0
    skipped_invalid = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue
            for table in tables:
                for row in table:
                    if not row or len(row) < 5:
                        continue
                    mfnr_s, call_s, eintritt, austritt, dok = (row[0] or "").strip(), (row[1] or "").strip(), (row[2] or "").strip(), (row[3] or "").strip(), (row[4] or "").strip()
                    # Header skippen
                    if mfnr_s == "MFNr" or not mfnr_s:
                        continue
                    # MFNr ist Zahl (manche Zellen haben Leerzeichen oder leer)
                    try:
                        mfnr = int(mfnr_s)
                    except ValueError:
                        continue
                    # Multi-Call-Cells (e.g. "DL7PL\nDO5ABC") in einzelne Calls splitten
                    raw_calls = [c.upper().replace(" ", "") for c in call_s.replace("\n", "/").split("/") if c.strip()]
                    for call in raw_calls:
                        # SWL: kein TX-Call, skippen
                        if call == "SWL":
                            skipped_swl += 1
                            continue
                        # Aktiv = Austritt-Spalte leer
                        if austritt:
                            skipped_inactive += 1
                            continue
                        # Callsign-Validierung
                        if not CALL_RE.match(call):
                            skipped_invalid += 1
                            continue
                        # Duplicate MFNr (Call-Wechsel): jeder Eintrag separat unter Call-Key
                        members[call] = {
                            "mfnr": mfnr,
                            "dok": dok or None,
                            "since": eintritt or None,
                        }
    print(f"aktive Mitglieder: {len(members)}")
    print(f"skip SWL: {skipped_swl}")
    print(f"skip inactive: {skipped_inactive}")
    print(f"skip invalid call: {skipped_invalid}")
    return members


def main() -> None:
    members = parse_pdf(PDF_PATH)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(members, indent=2, ensure_ascii=False, sort_keys=True))
    print(f"\nwritten: {OUT_PATH}")
    print(f"sample calls: {list(members.keys())[:10]}")


if __name__ == "__main__":
    main()
