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
import math
import statistics
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
    wert = roh.split(":", 1)[1].strip() if ":" in roh else ""
    # YAML darf den Wert quoten; ohne das Abstreifen wandern die
    # Anfuehrungszeichen in den Header und die Anfrage scheitert.
    if len(wert) >= 2 and wert[0] == wert[-1] and wert[0] in "\"'":
        wert = wert[1:-1]
    return wert


def tabelle(zeilen: list[tuple], kopf: tuple[str, ...]) -> None:
    if not zeilen:
        print("    (keine Daten)")
        return
    breiten = [max(len(str(k)), *(len(str(z[i])) for z in zeilen)) for i, k in enumerate(kopf)]
    print("    " + "  ".join(str(k).ljust(b) for k, b in zip(kopf, breiten)))
    print("    " + "  ".join("-" * b for b in breiten))
    for z in zeilen:
        print("    " + "  ".join(str(w).ljust(b) for w, b in zip(z, breiten)))


# Alle Stufen, die die Zustandsmaschine kennt. Ohne diese Liste faellt eine
# Stufe ohne jeden Treffer gar nicht auf — sie fehlt dann einfach in der
# Tabelle und sieht aus wie nicht vorhanden.
ALLE_FILTERSTUFEN = {
    "bandrand", "cooldown", "dt_fenster", "einzelner_schwacher_cq",
    "fernziel_allein", "kontinent_gate", "pile_up", "schon_gearbeitet",
    "schwach_ohne_psk", "slot_paritaet", "snr_floor", "soft_blacklist",
    "strict_modus",
}


def urteil(k_a: int, n_a: int, k_b: int, n_b: int) -> str:
    """Ist der Unterschied zweier Quoten echt oder Rauschen?

    Zweiseitiger z-Test auf zwei Anteile, ohne scipy (auf dem Pi nicht
    installiert). Rueckgabe ist eine kurze Klartextspalte fuer die Tabellen.

    Warum das hier steht: Wir haben mehrfach Quoten verglichen und ueber
    Unterschiede von fuenf Prozentpunkten geredet, ohne nachzurechnen, ob sie
    ueberhaupt vom Zufall zu unterscheiden sind. Beim Bandrand-Filter waren es
    11,9 % gegen 17,0 % bei n=59 — das sieht nach einem Befund aus und ist
    keiner (z = -1,05). Ohne diese Spalte optimiert man irgendwann Rauschen.
    """
    if n_a < 1 or n_b < 1:
        return "zu wenig"
    p_a, p_b = k_a / n_a, k_b / n_b
    p_gesamt = (k_a + k_b) / (n_a + n_b)
    nenner = p_gesamt * (1 - p_gesamt) * (1 / n_a + 1 / n_b)
    if nenner <= 0:
        return "zu wenig"
    z = (p_a - p_b) / math.sqrt(nenner)
    # NormalDist statt scipy: zweiseitiger p-Wert aus der Standardnormalen.
    p_wert = 2 * (1 - statistics.NormalDist().cdf(abs(z)))
    if p_wert < 0.01:
        return f"z={z:+.2f} sicher"
    if p_wert < 0.05:
        return f"z={z:+.2f} echt"
    return f"z={z:+.2f} Rauschen"


def n_fuer_nachweis(p_erwartet: float, p_referenz: float) -> int | None:
    """Wie viele Beobachtungen braeuchte es, damit dieser Unterschied
    nachweisbar waere? Beantwortet die Frage "noch warten oder nie?"."""
    if not 0 < p_erwartet < 1 or not 0 < p_referenz < 1:
        return None
    unterschied = abs(p_erwartet - p_referenz)
    if unterschied < 1e-9:
        return None
    # z=1,96 fuer 5 %; Referenzquote als Streuungsschaetzer.
    return int(math.ceil(
        (1.96 ** 2) * p_referenz * (1 - p_referenz) / unterschied ** 2))


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

    print("\n=== FT8-MUF: wie weit ueber die Vorhersage traegt FT8? ===")
    # Jede Referenzrichtung deckt ein Grid-Feld-Gebiet ab; die Zuordnung
    # ist grob, reicht aber fuer die Frage "offen oder zu".
    zeilen = con.execute(
        # Je Bericht genau EINE Vorhersage — die zeitlich naechste. Ohne das
        # trifft der Verbund alle Vorhersagen im +/-15-Minuten-Fenster und
        # zaehlt denselben Bericht mehrfach, im Schnitt 2,3-mal und bis zu
        # viermal (gemessen 2026-09-12: 636 Berichte wurden zu 1452 Paaren).
        # Das blaeht nicht nur die Zahlen auf, es verwischt auch die Grenzen
        # zwischen den Lagen, weil ein Bericht in mehreren Klassen landet.
        "with paare as ("
        "  select r.snr_db,"
        # Mittelwert ueber das Fenster statt "die zeitlich naechste": ein
        # Bericht zaehlt so genau einmal, und die Glaettung ist hier sogar
        # richtiger, weil die Vorhersage selbst im Stundenraster kommt.
        # (SQLite laesst die aeussere Spalte nicht im ORDER BY einer
        # korrelierten Unterabfrage zu, eine Auswahl "die naechste" ginge
        # also ohnehin nur ueber einen zweiten Durchgang.)
        "         14.074 / nullif((select avg(max(p2.muf_sp, p2.muf_lp))"
        "             from path_prediction p2"
        "             where substr(upper(p2.ziel_grid),1,2) = substr(upper(r.rx_grid),1,2)"
        "               and abs(julianday(p2.ts) - julianday(r.ts)) < 0.0105), 0)"
        "         as ueber_muf"
        "  from psk_reporter_in r"
        "  where 1=1"
        # 0,0105 Tage = gut 15 Minuten, der Takt der Vorhersage-Abfrage
        "    and r.ts > datetime('now',?) and r.snr_db is not null)"
        # Berichte ohne passende Vorhersage im Fenster liefern NULL. Ohne
        # diesen Filter fallen sie in der Fallunterscheidung in den
        # else-Zweig und erscheinen als "mehr als 80 % darueber" — am
        # 2026-09-12 waren das 5390 angebliche Berichte bei null
        # Gelegenheiten. Aufgefallen ist es nur, weil die Gelegenheiten
        # danebenstehen; ohne sie haette die Zeile wie ein Befund ausgesehen.
        " select case when ueber_muf is null then '0 ohne Vorhersage'"
        "             when ueber_muf <= 0.8 then '1 klar unter der MUF'"
        "             when ueber_muf <= 1.0 then '2 knapp unter'"
        "             when ueber_muf <= 1.3 then '3 bis 30 % darueber'"
        "             when ueber_muf <= 1.8 then '4 bis 80 % darueber'"
        "             else '5 mehr als 80 % darueber' end as lage,"
        "        count(*), round(avg(snr_db),1), min(snr_db)"
        " from paare group by 1 order by 1", (seit,)
    ).fetchall()
    # Ohne die Gelegenheiten daneben ist die Tabelle eine Falle: Am
    # 2026-09-12 fehlte die Zeile "mehr als 80 % darueber" komplett, was
    # nach einer Grenze aussah. Tatsaechlich lag 14,074 MHz in 128
    # Vorhersagen kein einziges Mal so hoch ueber der MUF — gemessen wurde
    # dort nie. Eine leere Zeile heisst "nicht beobachtet", nicht "geht nicht".
    gelegenheiten = dict(con.execute(
        "select case when 14.074/max(muf_sp,muf_lp) <= 0.8 then '1 klar unter der MUF'"
        "            when 14.074/max(muf_sp,muf_lp) <= 1.0 then '2 knapp unter'"
        "            when 14.074/max(muf_sp,muf_lp) <= 1.3 then '3 bis 30 % darueber'"
        "            when 14.074/max(muf_sp,muf_lp) <= 1.8 then '4 bis 80 % darueber'"
        "            else '5 mehr als 80 % darueber' end,"
        "       count(*)"
        " from path_prediction where muf_sp is not null and ts > datetime('now',?)"
        " group by 1", (seit,)
    ).fetchall())
    nach_lage = {z[0]: z for z in zeilen if not z[0].startswith("0 ")}
    ohne = next((z for z in zeilen if z[0].startswith("0 ")), None)
    alle_lagen = ("1 klar unter der MUF", "2 knapp unter", "3 bis 30 % darueber",
                  "4 bis 80 % darueber", "5 mehr als 80 % darueber")
    if gelegenheiten or zeilen:
        ausgabe = []
        for lage in alle_lagen:
            chancen = gelegenheiten.get(lage, 0)
            z = nach_lage.get(lage)
            if z:
                ausgabe.append((lage, chancen, z[1], z[2], z[3]))
            else:
                ausgabe.append((lage, chancen,
                                0, "-", "nie beobachtet" if not chancen else "-"))
        tabelle(ausgabe, ("Arbeitsfrequenz zur MUF", "Gelegenheiten", "Berichte",
                          "SNR im Mittel", "schwaechster"))
        print("    Die klassische MUF gilt fuer SSB-taugliche Signale; FT8")
        print("    arbeitet rund 20 dB darunter. Die gesuchte FT8-MUF liegt dort,")
        print("    wo es Gelegenheiten GAB und trotzdem keine Berichte kamen —")
        print("    eine Zeile ohne Gelegenheiten sagt gar nichts.")
        if ohne:
            print(f"    ({ohne[1]} Berichte ohne Vorhersage im Zeitfenster — "
                  "nicht zugeordnet, nicht gezaehlt.)")
    else:
        print("    (noch keine ueberlappenden Messungen — Daten sammeln sich)")

    print("\n=== Kommen wir an? Empfangsberichte ueber unser eigenes Signal ===")
    zeilen = con.execute(
        "select case substr(upper(rx_grid),1,2) "
        "         when 'FN' then 'Nordamerika Ost' when 'EN' then 'Nordamerika Ost' "
        "         when 'EM' then 'Nordamerika Ost' when 'FM' then 'Nordamerika Ost' "
        "         when 'DM' then 'Nordamerika West' when 'CN' then 'Nordamerika West' "
        "         when 'DN' then 'Nordamerika West' "
        "         when 'JO' then 'Europa' when 'JN' then 'Europa' when 'IO' then 'Europa' "
        "         when 'IN' then 'Europa' when 'KO' then 'Europa' when 'KN' then 'Europa' "
        "         when 'KP' then 'Skandinavien' when 'JP' then 'Skandinavien' "
        "         else 'uebrige Welt' end as region, "
        "  count(*), count(distinct rx_call), round(avg(snr_db),1), min(snr_db) "
        "from psk_reporter_in where rx_grid is not null and ts > datetime('now',?) "
        "group by 1 order by 2 desc", (seit,)
    ).fetchall()
    if zeilen:
        tabelle(zeilen, ("Region", "Berichte", "Stationen", "SNR im Mittel", "bester"))
        print("    Das sind Meldungen ANDERER ueber unser Signal — die einzige")
        print("    direkte Evidenz, dass wir irgendwo ankommen. Bei eigenen")
        print("    Decodes kennt man die Sendeleistung der Gegenseite nicht.")
    else:
        print("    (noch keine Empfangsberichte gespeichert)")

    print("\n=== Ausbreitung oder Konkurrenz? ===")
    zeilen = con.execute(
        "with gehoert as ("
        "  select strftime('%H', ts) as h, count(*) as berichte"
        "  from psk_reporter_in"
        "  where substr(upper(rx_grid),1,2) in ('FN','EN','EM','DM','CN','DN','FM')"
        "    and ts > datetime('now',?) group by 1),"
        "gerufen as ("
        "  select strftime('%H', ts) as h, count(*) as anrufe,"
        "         sum(outcome='completed') as qsos"
        "  from pick_attempt where pick_kind='cq' and continent='NA'"
        "    and ts > datetime('now',?) group by 1)"
        " select g.h, ifnull(h.berichte,0), g.anrufe, g.qsos,"
        "        round(100.0*g.qsos/g.anrufe,0)||' %'"
        " from gerufen g left join gehoert h on h.h=g.h"
        " where g.anrufe >= 3 order by g.h", (seit, seit)
    ).fetchall()
    if zeilen:
        tabelle(zeilen, ("Stunde UTC", "hoeren uns", "Anrufe NA", "QSOs", "Quote"))
        print("    Hohe Berichtszahl bei niedriger Quote heisst: Der Weg steht,")
        print("    aber wir setzen uns im Pile-Up nicht durch. Dagegen hilft")
        print("    keine Ausbreitungsvorhersage, sondern Zielauswahl.")
    else:
        print("    (zu wenige Daten)")

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
                         headers={"Authorization": f"Bearer {_tok}"})
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
            headers={"Authorization": f"Bearer {_token()}"},
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
            print("    Verdaechtige. Eine ohne Treffer ist NICHT automatisch")
            print("    Ballast — sie kann an einem Schalter haengen oder absicht-")
            print("    lich inaktiv sein. Die Tageshistorie unten trennt das.")
        else:
            print("    (noch keine Verwerfungen seit dem letzten Neustart)")
    except Exception as e:
        print(f"    (Station nicht erreichbar: {e})")

    print("\n=== Filterstufen ueber die Tage: greift eine Stufe ueberhaupt? ===")
    # Der Live-Zaehler oben gilt nur fuer den laufenden Tag. Ohne diese
    # Historie war nicht zu unterscheiden, ob eine Stufe grundsaetzlich nie
    # greift oder ob es nur an diesem Tag so war (Lücke gefunden 2026-09-12).
    zeilen = con.execute(
        "select stufe, count(distinct tag), sum(anzahl), max(anzahl) "
        "from filter_drop_daily where tag >= date('now', ?) "
        "group by stufe order by sum(anzahl) desc",
        (f"-{args.tage} day",),
    ).fetchall()
    if zeilen:
        tabelle([(z[0], z[1], z[2], z[3]) for z in zeilen],
                ("Stufe", "Tage mit Daten", "verworfen gesamt", "bester Tag"))
        bekannt = {z[0] for z in zeilen}
        stumm = sorted(ALLE_FILTERSTUFEN - bekannt)
        if stumm:
            print("    Ohne einen einzigen Treffer im ganzen Zeitraum:")
            print("      " + ", ".join(stumm))
            print("    Das heisst nicht 'ueberfluessig' — nachsehen, ob die Stufe")
            print("    an einem abgeschalteten Schalter haengt (hunt_skip_worked)")
            print("    oder hinter einer Bedingung sitzt, die nie eintritt.")
    else:
        print("    (noch keine Tageszeilen — die Historie laeuft seit v0.132.0)")

    print("\n=== Umentscheiden: lohnt der Wechsel zu einem anderen Ziel? ===")
    # Gemessen 2026-09-12 ueber 448 Faelle: Der Wechsel ging genauso oft zu
    # einem schlechteren wie zu einem besseren Ziel, das neue schloss zu
    # 17,0 % ab (Basis 15,8 %), und das alte rief in 65,6 % danach noch CQ.
    # Seit v0.135.0 ist die Sperre nach 'picked_another' auf 60 s gekappt.
    # Ob das wirkt, zeigt sich hier: an der Quote der Wiederanrufe binnen
    # 15 Minuten (vorher 31 %, n=32) und daran, wie oft das alte Ziel
    # ueberhaupt noch einmal versucht wird.
    r = con.execute(
        "select count(*), sum(x>0), sum(y='completed') from ("
        "  select p.id,"
        "   (select count(*) from decode d where d.call_from=p.target_call and d.call_to is null"
        "      and d.ts>p.ts and d.ts<datetime(p.ts,'+5 minutes')) x,"
        "   (select q.outcome from pick_attempt q where q.ts>p.ts and q.pick_kind='cq' order by q.ts limit 1) y"
        "  from pick_attempt p where p.bail_reason='picked_another' and p.ts > datetime('now',?))",
        (seit,)).fetchone()
    wieder = con.execute(
        "select count(*), sum(p.outcome='completed') from pick_attempt p"
        " join pick_attempt q on q.target_call=p.target_call and q.ts<p.ts and q.bail_reason='picked_another'"
        " where p.pick_kind='cq' and (julianday(p.ts)-julianday(q.ts))*1440 < 15 and p.ts > datetime('now',?)"
        " and q.ts=(select max(ts) from pick_attempt where target_call=p.target_call and ts<p.ts)",
        (seit,)).fetchone()
    if r and r[0]:
        tabelle([
            ("Wechsel gesamt", r[0], "-"),
            ("altes Ziel rief danach noch CQ", r[1] or 0, f"{100.0*(r[1] or 0)/r[0]:.1f} %"),
            ("neues Ziel abgeschlossen", r[2] or 0, f"{100.0*(r[2] or 0)/r[0]:.1f} %"),
            ("Wiederanruf des alten binnen 15 min", wieder[0] or 0,
             f"{100.0*(wieder[1] or 0)/wieder[0]:.1f} %" if wieder and wieder[0] else "-"),
        ], ("Was", "n", "Quote"))
    else:
        print("    (keine Wechsel im Zeitraum)")

    print("\n=== Ersatz fuer den fehlenden Empfangsbeleg ===")
    # Der eigene Beleg ("diese Station hat uns gehoert") ist der beste
    # bekannte Hinweis, fehlt aber bei zwei Dritteln der Ziele. Die Frage:
    # Taugt es als Ersatz, wenn uns *irgendjemand* aus dem Gebiet des Ziels
    # hoert? Am 2026-09-12 trennte das ohne eigenen Beleg 22,2 % von 14,0 %
    # (z = +2,61) — staerker belegt als der eigene Beleg selbst.
    zeilen = con.execute(
        "with ziel as ("
        "  select p.ts, p.outcome, p.psk_heard_us,"
        "         (select substr(upper(d.grid),1,2) from decode d"
        "           where d.call_from = p.target_call and d.ts <= p.ts"
        "             and d.grid is not null order by d.ts desc limit 1) feld"
        "  from pick_attempt p where p.ts > datetime('now',?)),"
        "mit as ("
        "  select z.*, (select count(*) from psk_reporter_in r"
        # 0,0208 Tage = eine halbe Stunde. Kuerzer waere sauberer, aber die
        # Berichte kommen in Schueben — ein zu enges Fenster verliert sie.
        "      where substr(upper(r.rx_grid),1,2) = z.feld"
        "        and abs(julianday(r.ts) - julianday(z.ts)) < 0.0208) > 0 nachbar"
        "  from ziel z where z.feld is not null)"
        " select case when psk_heard_us=1 then 'eigener Beleg' else 'ohne eigenen' end,"
        "        case when nachbar then 'Nachbar hoert uns' else 'kein Nachbar' end,"
        "        count(*), round(100.0*sum(outcome='completed')/count(*),1)||' %',"
        "        sum(outcome='completed')"
        " from mit group by 1, 2 order by 1 desc, 2",
        (seit,),
    ).fetchall()
    if zeilen:
        tabelle([(z[0], z[1], z[2], z[3]) for z in zeilen],
                ("eigener Beleg", "Nachbarschaft", "Versuche", "Abschluss"))
        # Das Urteil gehoert nur zu den Faellen OHNE eigenen Beleg — dort
        # ist die Frage offen, mit eigenem Beleg braucht es keinen Ersatz.
        ohne = {z[1]: z for z in zeilen if z[0] == "ohne eigenen"}
        ja, nein = ohne.get("Nachbar hoert uns"), ohne.get("kein Nachbar")
        if ja and nein:
            print(f"    Ohne eigenen Beleg: {urteil(ja[4], ja[2], nein[4], nein[2])}")
            print("    Traegt das, ist es ein Hinweis fuer genau die Ziele, bei")
            print("    denen bisher gar keiner vorlag — also fuer die Mehrheit.")

    print("\n=== Umgebung: erklaert sie, wann es laeuft und wann nicht? ===")
    # Drei Messreihen liefen bis zum 2026-09-12 ohne einen einzigen Leser:
    # band_noise, solar_log und swr_log wurden geschrieben, neunzig Tage
    # aufgehoben und geloescht. Jede hat eine gute Frage hinter sich — nur
    # hat sie nie jemand gestellt. Hier stehen die Fragen.
    zeilen = []

    # 1. Rauschflur gegen Abschlussquote, je Stunde. Bei hohem Rauschen sind
    #    schwache Signale chancenlos, und die sind die Mehrheit unserer Ziele.
    rausch = con.execute(
        "select strftime('%Y-%m-%d %H', ts), min(rx_audio_dbfs) "
        "from band_noise where rx_audio_dbfs is not null and ts > datetime('now',?) "
        "group by 1", (seit,),
    ).fetchall()
    quoten = dict(con.execute(
        "select strftime('%Y-%m-%d %H', ts), "
        "  round(100.0*sum(outcome='completed')/count(*),1) "
        "from pick_attempt where ts > datetime('now',?) group by 1 "
        "having count(*) >= 3", (seit,),
    ).fetchall())
    paare = [(p, quoten[st]) for st, p in rausch if st in quoten]
    if len(paare) >= 8:
        import statistics as _st
        r = _st.correlation([p[0] for p in paare], [p[1] for p in paare])
        zeilen.append(("Rauschflur gegen Abschlussquote", len(paare),
                       f"Korrelation {r:+.2f}",
                       "Zusammenhang" if abs(r) > 0.4 else "keiner erkennbar"))
    else:
        zeilen.append(("Rauschflur gegen Abschlussquote", len(paare), "-",
                       "zu wenige Stunden (mind. 8)"))

    # 2. Sonnenindizes gegen Decode-Rate. Ob die Vorhersagewerte hier ueber-
    #    haupt etwas erklaeren, ist offen — bisher hat es niemand geprueft.
    solar = con.execute(
        "select count(*), min(ts), max(ts) from solar_log where ts > datetime('now',?)",
        (seit,),
    ).fetchone()
    zeilen.append(("Sonnenindizes aufgezeichnet", solar[0] if solar else 0, "-",
                   "ab ~48 Messwerten auswertbar"))

    # 3. SWR-Verlauf. Solange er flach bleibt, ist das die Aussage; ein
    #    Anstieg waere die Fruehwarnung, fuer die die Reihe gedacht ist.
    swr = con.execute(
        "select count(*), min(swr), max(swr), avg(swr) from swr_log "
        "where ts > datetime('now',?)", (seit,),
    ).fetchone()
    if swr and swr[0]:
        spanne = (swr[2] or 0) - (swr[1] or 0)
        zeilen.append(("SWR-Verlauf", swr[0], f"{swr[1]:.2f} bis {swr[2]:.2f}",
                       "unauffaellig" if spanne < 0.3 else "ANSTIEG PRUEFEN"))
    else:
        zeilen.append(("SWR-Verlauf", 0, "-", "keine Messungen"))

    tabelle(zeilen, ("Frage", "Datenpunkte", "Ergebnis", "Urteil"))
    print("    Eine Reihe ohne Abnehmer ist Ballast. Steht hier dauerhaft")
    print("    'zu wenige' oder 'keiner erkennbar', gehoert die Frage")
    print("    verworfen und die Aufzeichnung eingestellt.")

    print("\n=== Wunschliste: gesehen und versucht? ===")
    tabelle(con.execute(
        "select w.call, "
        "  (select count(*) from decode d where d.call_from=w.call and d.ts > datetime('now',?)), "
        "  (select count(*) from pick_attempt p where p.target_call=w.call and p.ts > datetime('now',?)), "
        "  case when exists(select 1 from qso q where q.call=w.call) then 'im Log' else '-' end "
        "from watchlist w order by 2 desc", (seit, seit)
    ).fetchall(), ("Call", "Decodes", "Versuche", "QSO"))

    print("\n=== Bandrand: sitzt hunt_audio_freq_min_hz an der richtigen Stelle? ===")
    # Die Audio-Frequenz steht nicht am Anruf, sondern am Decode davor —
    # deshalb der Unterabfrage-Umweg ueber das letzte Signal der Gegenstation.
    roh = con.execute(
        "with v as ("
        "  select p.outcome,"
        "    (select d.freq_offset_hz from decode d"
        "      where d.call_from = p.target_call and d.ts <= p.ts"
        "      order by d.ts desc limit 1) as hz"
        "  from pick_attempt p)"
        " select case when hz < 500 then '400-499 Hz'"
        "             when hz < 700 then '500-699 Hz'"
        "             when hz < 1000 then '700-999 Hz'"
        "             when hz < 1500 then '1000-1499 Hz'"
        "             when hz < 2000 then '1500-1999 Hz'"
        "             else '2000+ Hz' end,"
        "        count(*), sum(outcome='completed'), min(hz)"
        " from v where hz is not null group by 1 order by 4"
    ).fetchall()
    n_alle = sum(r[1] for r in roh)
    k_alle = sum(r[2] or 0 for r in roh)
    zeilen = []
    for bereich, n, k, _sort in roh:
        k = k or 0
        # Jeder Bereich gegen alle uebrigen zusammen, nicht gegen sich selbst.
        zeilen.append((
            bereich, n, f"{100.0 * k / n:.1f} %",
            urteil(k, n, k_alle - k, n_alle - n),
        ))
    tabelle(zeilen, ("Bereich", "Anrufe", "abgeschlossen", "gegen den Rest"))
    if n_alle:
        unten = next((r for r in roh if r[0] == "400-499 Hz"), None)
        if unten and unten[1]:
            quote_unten = (unten[2] or 0) / unten[1]
            quote_rest = (k_alle - (unten[2] or 0)) / max(1, n_alle - unten[1])
            noetig = n_fuer_nachweis(quote_unten, quote_rest)
            if noetig and unten[1] < noetig:
                print(f"    Fuer einen Nachweis braeuchte der unterste Bereich "
                      f"rund {noetig} Anrufe, vorhanden sind {unten[1]}.")

    con.close()
    if not args.db:
        shutil.rmtree(db.parent, ignore_errors=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
