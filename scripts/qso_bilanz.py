#!/usr/bin/env python3
"""Abschlussquoten der Station — getrennt nach Weg.

Die Auswertung nach den Reparaturen vom 2026-09-09/10. Bis dahin fiel jedes
QSO durch, bei dem der Partner unseren Report mit einem R-Report bestaetigte
(v0.85.0), dazu vier verwandte Sequenz-Loecher (v0.86.0). Die Telemetrie
erfasste ausserdem nur Stationen, die der Picker selbst angerufen hat —
eingehende Anrufe liefen ungemessen (v0.87.0).

    ./scripts/qso_bilanz.py                 # gegen den Pi ueber Tailscale
    ./scripts/qso_bilanz.py --tage 3
    ./scripts/qso_bilanz.py --db /pfad/qso.sqlite   # lokale Kopie

Worauf zu achten ist:

* **inbound_* gegen cq/to_us/to_other** — kommen Verbindungen aus eingehenden
  Anrufen jetzt genauso zuverlaessig zustande wie selbst gerufene? Vor den
  Fixes war die Quote dort praktisch null.
* **inbound_resume** — jede Zeile ist ein QSO, das ohne den Nachklang-Speicher
  verloren gewesen waere (Partner meldete sich nach unserem Timeout).
* **R-Reports ohne QSO** — die Kennzahl, an der die Loecher sichtbar wurden.
  Sollte gegen null gehen; bleibt sie hoch, steckt noch ein Fall drin.
* **Wunschliste** — seit v0.87.0 uebersteuert sie Pile-Up- und SNR-Gates.
  Tauchen dort Anrufversuche auf, greift die Aenderung.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PI = "sebastian@100.77.48.117"
FERN = "/var/lib/ft8-appliance/qso.sqlite"


def hole_db(quelle: str | None) -> Path:
    if quelle:
        return Path(quelle)
    ziel = Path(tempfile.mkdtemp()) / "qso.sqlite"
    # Ueber die Kopie arbeiten: die Station schreibt waehrenddessen weiter.
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", PI, f"sudo cat {FERN}"],
        stdout=ziel.open("wb"), check=True, timeout=120,
    )
    return ziel


def tabelle(zeilen: list[tuple], kopf: tuple[str, ...]) -> None:
    if not zeilen:
        print("    (keine Daten)")
        return
    breiten = [max(len(str(k)), *(len(str(z[i])) for z in zeilen)) for i, k in enumerate(kopf)]
    print("    " + "  ".join(str(k).ljust(b) for k, b in zip(kopf, breiten)))
    print("    " + "  ".join("-" * b for b in breiten))
    for z in zeilen:
        print("    " + "  ".join(str(w).ljust(b) for w, b in zip(z, breiten)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tage", type=int, default=7)
    ap.add_argument("--db")
    args = ap.parse_args()

    db = hole_db(args.db)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    seit = f"-{args.tage} days"

    print(f"\n=== QSOs pro Tag (letzte {args.tage} Tage) ===")
    tabelle(con.execute(
        "select date(qso_start,'localtime'), count(*) from qso "
        "where qso_start > datetime('now',?) group by 1 order by 1", (seit,)
    ).fetchall(), ("Tag", "QSOs"))

    print("\n=== Anrufversuche nach Weg: kommen eingehende Anrufe zum Abschluss? ===")
    tabelle(con.execute(
        "select coalesce(pick_kind,'?'), count(*), "
        "  sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %' "
        "from pick_attempt where ts > datetime('now',?) group by 1 order by 2 desc",
        (seit,)
    ).fetchall(), ("Weg", "Versuche", "fertig", "Quote"))

    print("\n=== Ausgang im Detail ===")
    tabelle(con.execute(
        "select coalesce(pick_kind,'?'), outcome, coalesce(bail_reason,'-'), count(*) "
        "from pick_attempt where ts > datetime('now',?) group by 1,2,3 order by 4 desc limit 15",
        (seit,)
    ).fetchall(), ("Weg", "Ausgang", "Grund", "n"))

    print("\n=== R-Reports an uns ohne QSO — die Kennzahl der Sequenz-Loecher ===")
    tabelle(con.execute(
        "select d.call_from, count(*), "
        "  case when q.call is null then 'KEIN QSO' else 'QSO ok' end "
        "from decode d left join qso q on q.call = d.call_from "
        "where d.call_to in (select distinct user_callsign from qso where user_callsign is not null) "
        "  and d.message glob '* R[-+]*' and d.ts > datetime('now',?) "
        "group by d.call_from order by 3, 2 desc limit 15", (seit,)
    ).fetchall(), ("Station", "Wiederholungen", "Status"))

    print("\n=== Wunschliste: gesehen und versucht? ===")
    tabelle(con.execute(
        "select w.call, "
        "  (select count(*) from decode d where d.call_from=w.call and d.ts > datetime('now',?)), "
        "  (select count(*) from pick_attempt p where p.target_call=w.call and p.ts > datetime('now',?)), "
        "  case when exists(select 1 from qso q where q.call=w.call) then 'im Log' else '-' end "
        "from watchlist w order by 2 desc", (seit, seit)
    ).fetchall(), ("Call", "Decodes", "Versuche", "QSO"))

    con.close()
    if not args.db:
        shutil.rmtree(db.parent, ignore_errors=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
