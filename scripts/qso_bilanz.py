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

Zwei Fallen, in die eine Auswertung sonst laeuft:

* **psk_heard_us ist nicht immer aussagekraeftig.** Solange Auto-CQ lief,
  pausierte der PSK-Abruf (bis v0.87.1) — die Liste war leer, und *jede*
  Zeile bekam ``psk_heard_us = 0`` gestempelt. Das sieht aus wie "die
  Station hat uns nicht gehoert", heisst aber nur "wir haben nicht
  nachgesehen". Der Block "PSK-Datenlage" unten zeigt pro Tag, ob die Liste
  stand; Tage mit 0 % gehoeren aus jeder PSK-Rechnung heraus.
* **Der Picker hat meistens gar keine Wahl.** Ueber den 6.-10.9. lag bei
  91 % der Anrufe genau *ein* Kandidat vor. Die Tier-Reihenfolge entscheidet
  damit fast nie etwas — wer an ihr dreht, dreht an einer Schraube ohne
  Wirkung. Die Gates dagegen (ja/nein statt wer) wirken auf jeden Slot.
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


def _token() -> str:
    """API-Token der Station — nur lesen, nie ausgeben."""
    roh = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", PI,
         "grep -m1 -E '^\\s*api_token' /etc/ft8-appliance/config.yaml"],
        capture_output=True, text=True, timeout=30,
    ).stdout
    return roh.split(":", 1)[1].strip() if ":" in roh else ""


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

    print("\n=== PSK-Datenlage: an welchen Tagen stand die Liste? ===")
    tabelle(con.execute(
        "select date(ts,'localtime'), count(*), sum(psk_heard_us=1), "
        "  round(100.0*sum(psk_heard_us=1)/count(*),1)||' %' "
        "from pick_attempt where ts > datetime('now',?) group by 1 order by 1", (seit,)
    ).fetchall(), ("Tag", "Versuche", "psk=1", "Anteil"))
    print("    0 % heisst: Liste leer, nicht 'niemand hat uns gehoert'.")

    print("\n=== Hatte der Picker eine Wahl? (entscheidet, ob Tiers ueberhaupt wirken) ===")
    tabelle(con.execute(
        "select case when n_candidates is null then '(nicht erfasst)' "
        "            when n_candidates <= 1 then '1 Kandidat — keine Wahl' "
        "            when n_candidates <= 3 then '2-3 Kandidaten' "
        "            else '4+ Kandidaten' end, "
        "  count(*), sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %' "
        "from pick_attempt where pick_kind='cq' and ts > datetime('now',?) "
        "group by 1 order by 1", (seit,)
    ).fetchall(), ("Lage", "Versuche", "fertig", "Quote"))

    print("\n=== Fernziel-Gate: bringt CQ-Rufen mehr als ein 2-%-Anruf? (A/B) ===")
    zeilen = con.execute(
        "select case when fern_gate=1 then 'Gate an' else 'Gate aus' end, "
        "  count(*), sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %', "
        "  sum(pick_kind<>'cq') "
        "from pick_attempt where fern_gate is not null and ts > datetime('now',?) "
        "group by 1 order by 1", (seit,)
    ).fetchall()
    if zeilen:
        tabelle(zeilen, ("Arm", "Versuche", "fertig", "Quote", "eingehend"))
        print("    Beide Arme haben gleich viele Slots — die QSO-Zahlen sind")
        print("    direkt vergleichbar. 'eingehend' zeigt, ob die frei")
        print("    gewordene Zeit als Rufer zurueckkommt.")
    else:
        print("    (keine Daten — hunt_sole_dx_gate ist aus)")

    print("\n=== Vorab-Decode: was der fruehere Durchgang bringt (A/B) ===")
    zeilen = con.execute(
        "select case when pre_decode=1 then 'vorab' else 'regulaer' end, "
        "  count(*), sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %', "
        "  round(avg(tx_offset_s),3), "
        "  round(100.0*sum(tx_offset_s < 0.1)/"
        "        nullif(sum(tx_offset_s is not null),0),0)||' %', "
        "  round(avg(n_candidates),2) "
        "from pick_attempt where pre_decode is not null and ts > datetime('now',?) "
        "group by 1 order by 1", (seit,)
    ).fetchall()
    if zeilen:
        tabelle(zeilen, ("Durchgang", "Versuche", "fertig", "Quote",
                         "Versatz s", "puenktlich", "Kandidaten"))
        print("    Versatz = wie spaet die Aussendung nach der Slot-Grenze begann.")
        print("    puenktlich = Anteil der Aussendungen unter 0,1 s Versatz.")
        print("    Der Mittelwert des Vorab-Arms ist ueber laengere Zeitraeume")
        print("    verwaessert: Bis v0.105.0 fuehrte der Durchgang seine")
        print("    Entscheidung nicht selbst aus, der Arm war dann sogar")
        print("    langsamer als der regulaere.")
        print("    Der fruehere Durchgang sieht rund 8 % weniger Decodes; die")
        print("    Spalte Kandidaten zeigt, was ihn das an Auswahl kostet.")
    else:
        print("    (keine Daten — decoder_pre_decode ist aus)")

    print("\n=== Abschluss nach Signalstaerke — traegt das Schwach-Gate? ===")
    tabelle(con.execute(
        "select case when snr_db >= -10 then '1 stark   >= -10' "
        "            when snr_db >= -15 then '2 mittel  -11..-15' "
        "            when snr_db >= -20 then '3 schwach -16..-20' "
        "            else '4 sehr schwach' end, "
        "  count(*), sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %' "
        "from pick_attempt where snr_db is not null and ts > datetime('now',?) "
        "group by 1 order by 1", (seit,)
    ).fetchall(), ("Klasse", "Versuche", "fertig", "Quote"))

    print("\n=== Datenbasis: worauf stuetzt der Picker seine Entscheidungen? ===")
    import json as _json
    import urllib.request as _u
    _tok = None
    try:
        _tok = _token()
        req = _u.Request("http://100.77.48.117:8000/api/status",
                         headers={"X-API-Token": _tok})
        with _u.urlopen(req, timeout=10) as r:
            st = _json.load(r) or {}
        ch = st.get("context_health") or {}
        dauerhaft = ("gearbeitete_calls", "soft_blacklist", "wunschliste",
                     "kontinent_quoten", "qso_cooldowns")
        moment = ("kontinent_je_call", "dxcc_je_call", "standort_je_call",
                  "rarity_je_call", "pile_up_erkannt")
        tabelle(
            [(k, ch.get(k, "?"), "muss stehen" if k in dauerhaft else
              ("Momentwert" if k in moment else "waechst nach Neustart"))
             for k in list(dauerhaft) + list(moment)
             + ["slot_paritaet_gelernt", "psk_hoert_uns"]],
            ("Quelle", "Eintraege", "Erwartung"),
        )
        print("    Momentwerte stammen aus dem letzten Slot — in einem Sende-Slot")
        print("    sind sie zu Recht null. Eine Null bei 'muss stehen' ist ein Befund.")
    except Exception as e:
        print(f"    (Station nicht erreichbar: {e})")

    print("\n=== Was die Filterstufen des Pickers wegnehmen ===")
    print("    (Live-Zaehler der Station, seit ihrem letzten Neustart)")
    import json as _json
    import urllib.request as _u
    try:
        req = _u.Request(
            "http://100.77.48.117:8000/api/status",
            headers={"X-API-Token": _token()},
        )
        with _u.urlopen(req, timeout=10) as r:
            drops = (_json.load(r) or {}).get("filter_drops") or {}
        if drops:
            gesamt = sum(drops.values())
            tabelle(
                [(k, v, f"{100.0*v/gesamt:.1f} %") for k, v in
                 sorted(drops.items(), key=lambda kv: -kv[1])],
                ("Stufe", "verworfen", "Anteil"),
            )
            print("    Eine Stufe, die hier ploetzlich dominiert, ist der erste")
            print("    Verdaechtige. Eine, die gar nicht auftaucht, ist Ballast.")
        else:
            print("    (noch keine Verwerfungen seit dem letzten Neustart)")
    except Exception as e:
        print(f"    (Station nicht erreichbar: {e})")

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
