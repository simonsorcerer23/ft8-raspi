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
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# Statistik und Regelregister kommen aus dem Backend-Paket — eine Formel,
# ein Register, kein zweiter Namensraum. Nur Standardbibliothek dort.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ft8_appliance.analyse.regelregister import REGELN, stufen as _register_stufen, ueberfaellige  # noqa: E402
from ft8_appliance.analyse.messplan import MESSPLAN, faellige, nach_status  # noqa: E402
from ft8_appliance.analyse.stochastik import (  # noqa: E402
    bereinige_tagesgang, mantel_haenszel, n_fuer_nachweis, spearman, urteil,
    urteil_korrelation, urteil_rate,
)

# SSH-Ziel der Station. Der Tailscale-Name genuegt; abweichende
# Installationen setzen FT8_PI_SSH (etwa "pi@192.168.1.50").
PI = os.environ.get("FT8_PI_SSH", "ft8-pi5")
PI_HTTP = os.environ.get("FT8_PI", "http://ft8-pi5:8000").rstrip("/")
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


def spalte_da(con, tabelle_name: str, spalte: str) -> bool:
    """Eine juengere Spalte darf auf einer aelteren DB-Kopie nicht stuerzen."""
    if not hat_tabelle(con, tabelle_name):
        return False
    return spalte in {r[1] for r in con.execute(
        f"pragma table_info({tabelle_name})").fetchall()}


SONNE_MIN_BLOECKE = 20
SONNE_MIN_TAGE = 10


def sonnen_zeilen(con, seit: str) -> list[tuple]:
    """K-Index und Sonnenfluss gegen Empfangsberichte und Decodes."""
    from statistics import fmean

    if not hat_tabelle(con, "solar_log"):
        return [("Sonnenindizes", 0, "-", "keine Aufzeichnung")]
    bis = "strftime('%Y-%m-%d %H:00:00','now')"   # laufende Stunde ist unvollstaendig
    sonne = {st: (k, sfi) for st, k, sfi in con.execute(
        "select strftime('%Y-%m-%d %H', ts), avg(k_index), avg(sfi) from solar_log "
        f"where ts > datetime('now',?) and ts < {bis} group by 1", (seit,),
    ).fetchall()}
    reihen = {}
    if hat_tabelle(con, "psk_reporter_in"):
        reihen["Empfangsberichte"] = dict(con.execute(
            "select strftime('%Y-%m-%d %H', ts), count(distinct rx_call) from psk_reporter_in "
            f"where ts > datetime('now',?) and ts < {bis} group by 1", (seit,),
        ).fetchall())
    reihen["Decodes"] = dict(con.execute(
        "select strftime('%Y-%m-%d %H', ts), count(*) from decode "
        f"where ts > datetime('now',?) and ts < {bis} group by 1", (seit,),
    ).fetchall())

    zeilen = []
    for name, roh in reihen.items():
        rein = bereinige_tagesgang(roh)

        bloecke: dict[str, list[tuple[float, float]]] = {}
        for st, v in rein.items():
            k = sonne.get(st, (None, None))[0]
            if k is not None:
                bloecke.setdefault(f"{st[:10]}/{int(st[11:13]) // 3}", []).append((v, k))
        paare = [(fmean(v for v, _ in l), fmean(k for _, k in l))
                 for l in bloecke.values() if len(l) >= 2]
        r = spearman([p[1] for p in paare], [p[0] for p in paare])
        zeilen.append((f"K-Index gegen {name}", f"{len(paare)} Bloecke",
                       f"r={r:+.2f}" if r is not None else "-",
                       urteil_korrelation(r, len(paare), mindest_n=SONNE_MIN_BLOECKE)))

        tage: dict[str, list[tuple[float, float]]] = {}
        for st, v in rein.items():
            sfi = sonne.get(st, (None, None))[1]
            if sfi is not None:
                tage.setdefault(st[:10], []).append((v, sfi))
        # Nur fast volle Tage: ein halber Tag mit guten Abendstunden waere
        # sonst ein "guter Tag", egal was die Sonne tat.
        paare = [(fmean(v for v, _ in l), fmean(s for _, s in l))
                 for l in tage.values() if len(l) >= 20]
        if len(paare) < SONNE_MIN_TAGE:
            zeilen.append((f"Sonnenfluss gegen {name}", f"{len(paare)} Tage", "-",
                           f"{len(paare)} von {SONNE_MIN_TAGE} Tagen"))
        else:
            r = spearman([p[1] for p in paare], [p[0] for p in paare])
            zeilen.append((f"Sonnenfluss gegen {name}", f"{len(paare)} Tage",
                           f"r={r:+.2f}" if r is not None else "-",
                           urteil_korrelation(r, len(paare), mindest_n=SONNE_MIN_TAGE)))
    return zeilen


_STUFEN_NAME = {1: "1 schnell", 2: "2 spaeter Pass", 3: "3 jt9"}


def spaete_decodes_abschnitt(con, seit: str) -> None:
    if not hat_spalte(con, "decode", "stufe"):
        print("    (Datenbank kennt die Spalte noch nicht — Kennzeichnung seit 17.09.)")
        return
    werte: dict[int, list[float]] = {}
    for stufe, eingang in con.execute(
        "select stufe, eingang_s from decode "
        "where stufe is not null and ts > datetime('now', ?)", (seit,),
    ):
        werte.setdefault(int(stufe), []).append(eingang)
    if not werte:
        print("    (keine gekennzeichneten Decodes im Zeitraum)")
        return

    def quantil(v: list, q: float) -> str:
        v = sorted(x for x in v if x is not None)
        return f"{v[min(len(v) - 1, int(q * len(v)))]:.1f} s" if v else "-"

    tabelle([(_STUFEN_NAME.get(s, str(s)), len(v), quantil(v, 0.5), quantil(v, 0.9))
             for s, v in sorted(werte.items())],
            ("Stufe", "Decodes", "Eingang Median", "90 %"))
    print("    Eingang = Sekunden nach der Slotgrenze. Was spaeter als rund")
    print("    1,5 s ankommt, kann im selben Slot keine Antwort mehr ausloesen.")

    if hat_spalte(con, "pick_attempt", "ziel_stufe"):
        zeilen = con.execute(
            "select ziel_stufe, count(*), sum(outcome='completed') from pick_attempt "
            "where ziel_stufe is not null and ts > datetime('now', ?) "
            "group by ziel_stufe order by ziel_stufe", (seit,),
        ).fetchall()
        if zeilen:
            print()
            tabelle([(_STUFEN_NAME.get(s, str(s)), n, k, f"{100.0 * k / n:.1f} %")
                     for s, n, k in zeilen],
                    ("Ziel aus Stufe", "Versuche", "fertig", "Quote"))
            je = {s: (n, k) for s, n, k in zeilen}
            if 1 in je and 3 in je:
                print(f"    jt9-Ziele gegen schnelle Ziele: "
                      f"{urteil(je[3][1], je[3][0], je[1][1], je[1][0])}")

    erste = con.execute("select min(ts) from decode where stufe is not null").fetchone()[0]
    qsos = con.execute(
        "select call, coalesce(station_callsign, user_callsign), qso_start, qso_end "
        "from qso where qso_start >= ? and qso_start > datetime('now', ?)",
        (erste, seit),
    ).fetchall()
    mit_jt9 = 0
    for call, eigen, start, ende in qsos:
        treffer = con.execute(
            "select 1 from decode where call_from = ? and call_to = ? and stufe = 3 "
            "and ts between datetime(?, '-60 seconds') and datetime(?, '+60 seconds') limit 1",
            (call, eigen, start, ende),
        ).fetchone()
        mit_jt9 += 1 if treffer else 0
    print(f"    QSOs seit der Kennzeichnung: {len(qsos)} — davon mit mindestens einem")
    print(f"    Schritt der Gegenstation, den nur jt9 empfangen hat: {mit_jt9}")
    print("    Die ausgefallenen Aussendungen stehen nur im Journal:")
    print("    journalctl -u ft8-controller | grep -c 'Burst entfaellt\\|verworfen: ein Burst'")


def _quote_gruppen(con, seit: str, ausdruck: str, *, wo: str = "1=1",
                   sortierung: str = "1") -> list[tuple]:
    """(Gruppe, Versuche, fertig, Quote) fuer einen beliebigen Gruppenausdruck."""
    return con.execute(
        f"select {ausdruck}, count(*), sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %' "
        f"from pick_attempt where {wo} and ts > datetime('now',?) "
        f"group by 1 order by {sortierung}", (seit,)
    ).fetchall()


def _zwei_gruppen_urteil(con, seit: str, spalte: str, *, wo: str = "1=1") -> str:
    """urteil() fuer eine Ja/Nein-Spalte: Gruppe 1 gegen Gruppe 0."""
    je = {int(g): (n, k) for g, n, k in con.execute(
        f"select {spalte}, count(*), sum(outcome='completed') from pick_attempt "
        f"where {spalte} is not null and {wo} and ts > datetime('now',?) group by 1",
        (seit,)).fetchall()}
    if 0 not in je or 1 not in je:
        return "zu wenig"
    return urteil(je[1][1], je[1][0], je[0][1], je[0][0])


def _spearman_gegen_abschluss(con, seit: str, spalte: str, *, wo: str = "1=1") -> str:
    """Rangkorrelation einer Messgroesse mit dem Abschluss, mit Urteil."""
    paare = con.execute(
        f"select {spalte}, case when outcome='completed' then 1 else 0 end "
        f"from pick_attempt where {spalte} is not null and {wo} "
        "and ts > datetime('now',?)", (seit,)).fetchall()
    if len(paare) < 10:
        return "zu wenig"
    r = spearman([float(x) for x, _ in paare], [float(y) for _, y in paare])
    if r is None:
        return "zu wenig"
    return f"r={r:+.2f} {urteil_korrelation(r, len(paare))}"


def ziel_eigenschaften_abschnitt(con, seit: str) -> None:
    """dupe_anrufe, neu_dxcc, entfernung, prioritaeten — vier Fragen aus den
    Modellkommentaren, die seit v0.31.0 bzw. v0.64.0 Daten sammelten, ohne
    dass sie jemand las."""
    print("\n=== Wen anrufen? Was die Ziel-Eigenschaften zum Abschluss beitragen ===")
    print("    Schon gearbeitet? (hunt_skip_worked steht auf aus)")
    tabelle([("ja" if int(g) else "nein", n, k, q)
             for g, n, k, q in _quote_gruppen(con, seit, "was_worked",
                                              wo="was_worked is not null")],
            ("schon gearbeitet", "Versuche", "fertig", "Quote"))
    print(f"    Gearbeitet gegen neu: {_zwei_gruppen_urteil(con, seit, 'was_worked')}")

    print("\n    Neues DXCC-Gebiet?")
    tabelle([("ja" if int(g) else "nein", n, k, q)
             for g, n, k, q in _quote_gruppen(con, seit, "was_new_dxcc",
                                              wo="was_new_dxcc is not null")],
            ("neues DXCC", "Versuche", "fertig", "Quote"))
    print(f"    Neues DXCC gegen bekanntes: {_zwei_gruppen_urteil(con, seit, 'was_new_dxcc')}")

    print("\n    Entfernung zum Ziel")
    tabelle(_quote_gruppen(
        con, seit,
        "case when distance_km < 1000 then '1 unter 1000 km' "
        "     when distance_km < 2500 then '2 1000-2500 km' "
        "     when distance_km < 4000 then '3 2500-4000 km' "
        "     else '4 ueber 4000 km' end",
        wo="distance_km is not null"),
        ("Entfernung", "Versuche", "fertig", "Quote"))
    print(f"    Entfernung gegen Abschluss: {_spearman_gegen_abschluss(con, seit, 'distance_km')}")
    nur_eu = _spearman_gegen_abschluss(con, seit, "distance_km", wo="continent='EU'")
    print("    Nur innerhalb Europas (trennt Entfernung vom Kontinent):")
    print(f"      {nur_eu}")
    print("    Die Tiefenpruefung am 12.09. fand: Entfernung wirkt nur ueber den")
    print("    Kontinent. Bleibt die Korrelation innerhalb Europas aus, ist die")
    print("    Entfernung als eigenes Picker-Signal erledigt.")

    print("\n    Welche Prioritaetsregel gab den Ausschlag?")
    tabelle([(g or "(keine)", n, k, q) for g, n, k, q in _quote_gruppen(
        con, seit, "winning_tier", wo="winning_tier is not null",
        sortierung="2 desc")][:12],
        ("Tier", "Versuche", "fertig", "Quote"))
    print("    'sole' heisst: es gab nur einen Kandidaten. Dann entscheidet keine")
    print("    Regel, sondern die Lage. Ein Tier taugt nur als Signal, wenn es")
    print("    oft genug vorkommt UND eine andere Quote hat als der Rest.")
    print(f"    Tail-End-Ziele gegen den Rest: {_zwei_gruppen_urteil(con, seit, 'was_tailend')}")


def versuch_verlauf_abschnitt(con, seit: str) -> None:
    """veraltete_picks, bandbelegung, qso_dauer."""
    print("\n=== Woran ein Versuch scheitert: Alter, Bandbelegung, Dauer ===")
    print("    Alter des Decodes beim Pick — 'went_silent' koennte heissen, dass")
    print("    wir eine Station anrufen, die laengst weiter ist.")
    tabelle(con.execute(
        "select case when pick_age_s < 3 then '1 unter 3 s' "
        "            when pick_age_s < 8 then '2 3-8 s' "
        "            when pick_age_s < 20 then '3 8-20 s' "
        "            else '4 ueber 20 s' end, count(*), "
        "  sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %', "
        "  round(100.0*sum(bail_reason='went_silent')/count(*),1)||' %' "
        "from pick_attempt where pick_age_s is not null and ts > datetime('now',?) "
        "group by 1 order by 1", (seit,)).fetchall(),
        ("Alter beim Pick", "Versuche", "fertig", "Quote", "davon stumm"))
    print(f"    Alter gegen Abschluss: {_spearman_gegen_abschluss(con, seit, 'pick_age_s')}")

    print("\n    Bandbelegung: Decodes im Slot, in dem gepickt wurde")
    tabelle(con.execute(
        "select case when n_decodes < 5 then '1 unter 5' "
        "            when n_decodes < 15 then '2 5-14' "
        "            when n_decodes < 30 then '3 15-29' "
        "            else '4 ab 30' end, count(*), sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %' "
        "from pick_attempt where n_decodes is not null and ts > datetime('now',?) "
        "group by 1 order by 1", (seit,)).fetchall(),
        ("Decodes im Slot", "Versuche", "fertig", "Quote"))
    print(f"    Belegung gegen Abschluss: {_spearman_gegen_abschluss(con, seit, 'n_decodes')}")
    print("    Stoergroesse, kein Schalter: Sie sagt, ob ein Vergleich zwischen")
    print("    ruhigen und vollen Baendern ueberhaupt zulaessig ist.")

    print("\n    Dauer eines Versuchs bis zum Ausgang")
    tabelle(con.execute(
        "select coalesce(bail_reason, 'abgeschlossen'), count(*), "
        "  round(avg(qso_duration_s)) || ' s', "
        "  round(max(qso_duration_s)) || ' s' "
        "from pick_attempt where qso_duration_s is not null "
        "and ts > datetime('now',?) group by 1 order by 2 desc", (seit,)).fetchall(),
        ("Ausgang", "Faelle", "Dauer im Mittel", "laengster"))
    print("    Ein Abbruchgrund, der im Mittel lange dauert, blockiert die Station.")


def dranbleiben_abschnitt(con, seit: str) -> None:
    """wiederholungen, laut_genug."""
    print("\n=== Dranbleiben oder aufgeben: Wiederholungen und unsere Lautstaerke ===")
    print("    Wiederholte Aussendungen im selben Versuch")
    tabelle(con.execute(
        "select n_resends, count(*), sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %' "
        "from pick_attempt where n_resends is not null and ts > datetime('now',?) "
        "group by 1 order by 1", (seit,)).fetchall(),
        ("Wiederholungen", "Versuche", "fertig", "Quote"))
    gesamt, spaet = con.execute(
        "select sum(outcome='completed'), "
        "  sum(outcome='completed' and n_resends > 0) from pick_attempt "
        "where n_resends is not null and ts > datetime('now',?)", (seit,)).fetchone()
    if gesamt:
        print(f"    Von {gesamt} Abschluessen kamen {spaet} erst nach mindestens einer")
        print(f"    Wiederholung zustande ({100.0 * spaet / gesamt:.1f} %).")
    print("    Nie geantwortet gegen engagiert, dann verloren:")
    tabelle(con.execute(
        "select case when bail_reason='went_silent' then 'nie geantwortet' "
        "            when bail_reason in ('report_never_closed','max_resends') "
        "                 then 'engagiert, dann verloren' "
        "            when outcome='completed' then 'abgeschlossen' "
        "            else 'anderer Ausgang' end, count(*), "
        "  round(avg(n_resends),1), round(avg(stale_slots),1) "
        "from pick_attempt where ts > datetime('now',?) group by 1 order by 2 desc",
        (seit,)).fetchall(),
        ("Verlauf", "Faelle", "Wiederholungen", "leere Slots"))

    print("\n    Sind wir laut genug? Unser eigenes Signal bei der Gegenstation")
    tabelle(con.execute(
        "select case when our_snr_received >= -5 then '1 ab -5 dB' "
        "            when our_snr_received >= -12 then '2 -6..-12 dB' "
        "            when our_snr_received >= -18 then '3 -13..-18 dB' "
        "            else '4 unter -18 dB' end, count(*), "
        "  sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %', "
        "  round(100.0*sum(bail_reason='went_silent')/count(*),1)||' %' "
        "from pick_attempt where our_snr_received is not null "
        "and ts > datetime('now',?) group by 1 order by 1", (seit,)).fetchall(),
        ("unser SNR dort", "Versuche", "fertig", "Quote", "davon stumm"))
    print(f"    Unser SNR gegen Abschluss: "
          f"{_spearman_gegen_abschluss(con, seit, 'our_snr_received')}")
    print("    Die Spalte steht nur, wenn die Gegenstation uns einen Rapport")
    print("    geschickt hat — also nie fuer die Faelle, die gleich stumm blieben.")
    print("    Deshalb daneben der Empfangsbeleg von PSK Reporter, den es auch")
    print("    ohne Antwort gibt:")
    tabelle(con.execute(
        "select case when psk_snr >= -5 then '1 ab -5 dB' "
        "            when psk_snr >= -12 then '2 -6..-12 dB' "
        "            when psk_snr >= -18 then '3 -13..-18 dB' "
        "            else '4 unter -18 dB' end, count(*), "
        "  sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %', "
        "  round(100.0*sum(bail_reason='went_silent')/count(*),1)||' %' "
        "from pick_attempt where psk_snr is not null and ts > datetime('now',?) "
        "group by 1 order by 1", (seit,)).fetchall(),
        ("PSK-Beleg", "Versuche", "fertig", "Quote", "davon stumm"))
    print(f"    PSK-Beleg gegen Abschluss: {_spearman_gegen_abschluss(con, seit, 'psk_snr')}")
    leistung = con.execute(
        "select count(distinct tx_power_w) from pick_attempt "
        "where tx_power_w is not null and ts > datetime('now',?)", (seit,)).fetchone()[0]
    if leistung and leistung < 2:
        print("    Sendeleistung: im Zeitraum unveraendert — als Erklaerung nicht")
        print("    pruefbar, die Spalte taugt hier nur als Beleg dafuer.")
    else:
        tabelle(_quote_gruppen(con, seit, "tx_power_w || ' W'",
                               wo="tx_power_w is not null", sortierung="2 desc"),
                ("Sendeleistung", "Versuche", "fertig", "Quote"))


def messplan_abschnitt() -> None:
    from datetime import UTC as _U, datetime as _D

    heute = _D.now(_U).date()
    faellig = faellige(heute)
    if faellig:
        tabelle([(m.schluessel, f"{(heute - m.lesen_ab).days} d", m.auswertung or "-")
                 for m in sorted(faellig, key=lambda m: m.lesen_ab)],
                ("FAELLIG", "seit", "Abschnitt"))
        for m in faellig:
            print(f"    {m.schluessel}: {m.entscheidungsregel}")
            if m.danach:
                print(f"      danach: {m.danach}")
    else:
        print("    Nichts faellig.")
    bald = [m for m in nach_status("laufend") if m not in faellig and m.lesen_ab]
    if bald:
        print("    Demnaechst: " + ", ".join(
            f"{m.schluessel} ab {m.lesen_ab:%d.%m.}" for m in sorted(bald, key=lambda m: m.lesen_ab)))

    # Entschieden, aber der A/B-Schalter steht noch an? Das liest die
    # laufende Konfiguration, nicht die Datenbank.
    try:
        import json as _json
        import urllib.request as _u
        req = _u.Request(f"{PI_HTTP}/api/config",
                         headers={"Authorization": f"Bearer {_token()}"})
        with _u.urlopen(req, timeout=10) as r:
            operating = (_json.load(r) or {}).get("operating") or {}
        noch_an = [(m.schluessel, s) for m in nach_status("entschieden")
                   for s in m.schalter if operating.get(s)]
        for schluessel, s in noch_an:
            print(f"    ENTSCHIEDEN, Schalter noch an: {s} ({schluessel})")
    except Exception as e:
        print(f"    (Schalterstand nicht lesbar: {e})")

    ohne = nach_status("ohne_auswertung")
    if ohne:
        groessen = sorted({g for m in ohne for g in m.messgroessen})
        print(f"    Ohne Auswertung ({len(ohne)} Fragen, {len(groessen)} Spalten): "
              "Frage steht fest, aber niemand wertet aus —")
        print("    eine Auswertung bauen oder das Schreiben einstellen:")
        for m in ohne:
            print(f"      {m.schluessel:16s} {m.frage}")


def hat_tabelle(con, tabelle_name: str) -> bool:
    """Eine juengere Tabelle darf auf einer aelteren DB-Kopie nicht stuerzen."""
    return con.execute(
        "select count(*) from sqlite_master where type='table' and name=?",
        (tabelle_name,),
    ).fetchone()[0] > 0


def hat_spalte(con, tabelle_name: str, spalte: str) -> bool:
    """Kennt diese Datenbank die Spalte schon?

    Die Bilanz laeuft oft auf einer Kopie, die vor dem letzten Ausrollen
    gezogen wurde. Eine Abfrage auf eine frisch migrierte Spalte riss dann
    den ganzen Lauf ab, statt nur ihren Abschnitt leer zu lassen — die
    siebzehn Abschnitte davor waren mit umsonst gerechnet.
    """
    return any(
        r[1] == spalte
        for r in con.execute(f"pragma table_info({tabelle_name})")
    )


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
ALLE_FILTERSTUFEN = _register_stufen()
# Zaehler, die ueber _buche_filter laufen, aber nichts verwerfen: Hier hat
# eine Stufe NACHGEGEBEN, weil sonst kein einziges Ziel uebrig geblieben
# waere. Am 17.09. stand schwach_zurueckgenommen mit 66 % "verworfen" ganz
# oben in der Tabelle — direkt ueber dem Satz, die dominierende Stufe sei
# der erste Verdaechtige.
NACHGEGEBEN = {
    "schwach_zurueckgenommen":
        ("Schwach-Filter hat nachgegeben — sonst waere kein Ziel uebrig "
         "geblieben. Die Zahl sagt, wie oft nur schwache Ziele da waren."),
}


def trenne_nachgegeben(zaehler: dict[str, int]) -> tuple[dict[str, int], dict[str, int]]:
    """(verworfen, nachgegeben) — nur das Erste gehoert in eine Verwurfstabelle."""
    return ({k: v for k, v in zaehler.items() if k not in NACHGEGEBEN},
            {k: v for k, v in zaehler.items() if k in NACHGEGEBEN})


def zeige_nachgegeben(nachgegeben: dict[str, int]) -> None:
    if not nachgegeben:
        return
    print("    Nicht mitgezaehlt, weil dort nichts verworfen wurde:")
    for k, v in sorted(nachgegeben.items(), key=lambda kv: -kv[1]):
        text = " ".join(NACHGEGEBEN[k].split())
        print(f"      {k} = {v}: {text}")
# Nur Chancen-Regeln schaetzen die Erfolgsaussicht und laufen deshalb im
# Kontrollarm nicht mit. Technische Gates und Sperren gelten in beiden
# Armen — sie koennen im Schattenprotokoll gar nicht auftauchen.
_CHANCEN_STUFEN = {r.stufe for r in REGELN if r.art == "chance"}


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
    # Je Bericht genau EINE Vorhersage. Frueher stand hier eine korrelierte
    # Unterabfrage ueber die ganze Vorhersage-Tabelle: 58 000 Berichte mal
    # 12 000 Vorhersagen, gemessen 2026-09-21 knapp sieben Minuten fuer die
    # Bilanz. Stattdessen erst ein Raster (Grid-Feld, Viertelstunde) mit
    # Index, dann je Bericht der Mittelwert seiner drei Nachbarzellen —
    # dieselbe Glaettung wie vorher, nur nicht mehr quadratisch.
    con.execute("drop table if exists temp.muf_raster")
    con.execute(
        "create temp table muf_raster as "
        "select substr(upper(ziel_grid),1,2) feld, "
        "       cast(julianday(ts)*96 as int) viertel, "
        "       avg(max(muf_sp, muf_lp)) muf "
        "from path_prediction where muf_sp is not null and ts > datetime('now',?) "
        "group by 1,2", (seit,))
    con.execute("create index temp.ix_muf_raster on muf_raster(feld, viertel)")
    zeilen = con.execute(
        "with paare as ("
        "  select r.snr_db,"
        "         14.074 / nullif((select avg(x.muf) from muf_raster x"
        "             where x.feld = substr(upper(r.rx_grid),1,2)"
        "               and x.viertel between cast(julianday(r.ts)*96 as int) - 1"
        "                                 and cast(julianday(r.ts)*96 as int) + 1), 0)"
        "         as ueber_muf"
        "  from psk_reporter_in r"
        "  where r.ts > datetime('now',?) and r.snr_db is not null)"
        # Berichte ohne passende Vorhersage im Fenster liefern NULL. Ohne
        # diesen Zweig fallen sie in der Fallunterscheidung in den
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
    zeilen = [] if not hat_spalte(con, "pick_attempt", "fern_gate") else con.execute(
        "select case when fern_gate=1 then 'Gate an' else 'Gate aus' end, "
        "  count(*), sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %', "
        "  sum(pick_kind<>'cq') "
        "from pick_attempt where fern_gate is not null and ts > datetime('now',?) "
        "group by 1 order by 1", (seit,)
    ).fetchall()
    if zeilen:
        tabelle(zeilen, ("Arm", "Versuche", "fertig", "Quote", "eingehend"))
        je_arm = {z[0]: z[2] or 0 for z in zeilen}
        a, b = je_arm.get("Gate aus", 0), je_arm.get("Gate an", 0)
        if a + b:
            import math as _m
            z = (a - b) / _m.sqrt(a + b)
            urt = ("zu wenig" if min(a, b) < 40 else
                   "echt" if abs(z) >= 1.96 else "Rauschen")
            print(f"    QSOs Gate aus gegen Gate an: z = {z:+.2f} → {urt} "
                  "(Messplan: ab 40 QSOs je Arm, |z| ≥ 1,96)")
        print("    Beide Arme haben gleich viele Slots — die QSO-Zahlen sind")
        print("    direkt vergleichbar. 'eingehend' zeigt, ob die frei")
        print("    gewordene Zeit als Rufer zurueckkommt.")
    else:
        print("    (keine Daten — hunt_sole_dx_gate ist aus)")

    print("\n=== Antwortfrequenz: Rufer-Frequenz oder ruhiger Bin? (A/B) ===")
    # Laeuft seit 2026-09-07; bis 17.09. gab es keinen Bilanz-Abschnitt dafuer,
    # das letzte Ergebnis (p = 0,059) war eine einmalige Rechnung in flags.md.
    zeilen = [] if not hat_spalte(con, "pick_attempt", "reply_kind") else con.execute(
        "select reply_kind, "
        "  case when snr_db >= -10 then 1 when snr_db >= -15 then 2 "
        "       when snr_db >= -20 then 3 else 4 end, "
        "  count(*), sum(outcome='completed') "
        "from pick_attempt where reply_kind in ('on_freq','quiet') and snr_db is not null "
        "  and ts > datetime('now',?) group by 1, 2", (seit,)
    ).fetchall()
    if zeilen:
        summe: dict[str, list[int]] = {}
        schichten: dict[int, dict[str, tuple[int, int]]] = {}
        for art, klasse, n, k in zeilen:
            s = summe.setdefault(art, [0, 0]); s[0] += n; s[1] += k
            schichten.setdefault(klasse, {})[art] = (k, n)
        tabelle([(art, n, k, f"{100.0 * k / n:.1f} %") for art, (n, k) in sorted(summe.items())],
                ("Antwort", "Versuche", "fertig", "Quote"))
        quote, z, p = mantel_haenszel([
            (sch.get("on_freq", (0, 0))[0], sch.get("on_freq", (0, 0))[1],
             sch.get("quiet", (0, 0))[0], sch.get("quiet", (0, 0))[1])
            for sch in schichten.values()])
        if z is None:
            print("    (zu wenig fuer den Schichtvergleich)")
        else:
            urt = "echt" if p < 0.05 else "Rauschen"
            print(f"    Mantel-Haenszel ueber SNR-Klassen: Odds Ratio "
                  f"{quote:.2f}, z = {z:+.2f}, p = {p:.3f} → {urt}")
            print("    OR > 1: die Rufer-Frequenz schliesst oefter ab. Regel im Messplan:")
            print("    p < 0,05 → Gewinner einstellen, A/B aus; ab 2000 Anrufen je Arm")
            print("    ohne Befund → Nicht-Effekt eintragen, A/B aus.")
    else:
        print("    (keine Daten — hunt_reply_ab_test ist aus)")

    print("\n=== Schwache Ziele ohne Empfangsbeleg: lohnt der Anruf? (A/B) ===")
    if not hat_spalte(con, "pick_attempt", "schwach_arm"):
        print("    (keine Daten — der Test laeuft noch nicht, oder die Kopie")
        print("     stammt von vor dem Ausrollen der Spalte)")
    else:
        zeilen = con.execute(
            "select case when schwach_arm=1 then 'Filter an' else 'Filter aus' end, "
            "  count(*), sum(outcome='completed'), "
            "  round(100.0*sum(outcome='completed')/count(*),1)||' %' "
            "from pick_attempt where schwach_arm is not null and pick_kind='cq' "
            "  and ts > datetime('now',?) group by 1 order by 1", (seit,)
        ).fetchall()
        if not zeilen:
            print("    (noch keine Anrufe mit gesetztem Arm)")
        else:
            tabelle(zeilen, ("Arm", "Anrufe", "QSOs", "Quote"))
            print("    Die entscheidende Spalte ist QSOs, NICHT die Quote.")
            print("    Beide Arme bekommen gleich viele Zeitbloecke, also ist die")
            print("    QSO-Zahl unmittelbar die Ausbeute pro Zeit. Die Quote steigt")
            print("    zwangslaeufig, wenn weniger angerufen wird — und genau das")
            print("    tut der Filter. Sie zu vergleichen misst seine Nebenwirkung,")
            print("    nicht seinen Nutzen.")
            if len(zeilen) == 2:
                an, aus = zeilen
                mehr = int(aus[2]) - int(an[2])
                print(f"    Ohne Filter {mehr:+d} QSOs bei {int(aus[1]) - int(an[1]):+d} Anrufen.")
                print("    Signifikanz der QSO-Zahl:",
                      urteil(int(aus[2]), int(aus[1]) + int(an[1]),
                             int(an[2]), int(aus[1]) + int(an[1])))

    print("\n=== Stunden-Tier: Zellen-Quote gegen die alte Stundenliste (A/B) ===")
    zeilen = [] if not hat_spalte(con, "pick_attempt", "zellen_arm") else con.execute(
        "select case when zellen_arm=1 then 'Zellen-Quote' else 'Stundenliste' end, "
        "  count(*), sum(outcome='completed'), "
        "  round(100.0*sum(outcome='completed')/count(*),1)||' %' "
        "from pick_attempt where zellen_arm is not null and pick_kind='cq' "
        "  and ts > datetime('now',?) group by 1 order by 1", (seit,)
    ).fetchall()
    if zeilen:
        tabelle(zeilen, ("Quelle", "Anrufe", "fertig", "Quote"))
        if len(zeilen) == 2:
            a, b = zeilen
            print("    Zellen-Quote gegen Stundenliste:",
                  urteil(int(b[2]), int(b[1]), int(a[2]), int(a[1])))
        print("    Die alte Liste zaehlt, wann wir QSOs hatten — ihr Nenner sind")
        print("    Erfolge. Die Zellen-Quote setzt Abschluesse ins Verhaeltnis zu")
        print("    Anrufen derselben Stunde und desselben Kontinents.")
    else:
        print("    (keine Daten — hunt_zellen_prior ist aus, oder die Kopie")
        print("     stammt von vor dem Ausrollen der Spalte)")

    print("\n=== Vorab-Decode: was der fruehere Durchgang bringt (A/B) ===")
    zeilen = [] if not hat_spalte(con, "pick_attempt", "pre_decode") else con.execute(
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

    print("\n=== Spaete Decodes: was bringt die jt9-Stufe, was kostet sie? ===")
    # Seit 2026-09-17. Die jt9-Stufe liefert ihre Decodes 3 bis 7 s nach der
    # Slotgrenze. Vom 13. bis 17.09. folgte JEDE der 549 ausgefallenen
    # Aussendungen auf so einen Decode. Ob die Stufe trotzdem QSOs bringt —
    # ein weit entferntes Ziel, das schwache RR73 am Ende —, war ohne die
    # Kennzeichnung nicht zu sagen: decode.ts ist der Slotbeginn.
    spaete_decodes_abschnitt(con, seit)

    ziel_eigenschaften_abschnitt(con, seit)
    versuch_verlauf_abschnitt(con, seit)
    dranbleiben_abschnitt(con, seit)

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
        req = _u.Request(f"{PI_HTTP}/api/status",
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
            f"{PI_HTTP}/api/status",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        with _u.urlopen(req, timeout=10) as r:
            drops = (_json.load(r) or {}).get("filter_drops") or {}
        drops, nachgegeben = trenne_nachgegeben(drops)
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
        zeige_nachgegeben(nachgegeben)
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
    nachgegeben = {z[0]: z[2] for z in zeilen if z[0] in NACHGEGEBEN}
    zeilen = [z for z in zeilen if z[0] not in NACHGEGEBEN]
    if zeilen:
        tabelle([(z[0], z[1], z[2], z[3]) for z in zeilen],
                ("Stufe", "Tage mit Daten", "verworfen gesamt", "bester Tag"))
        zeige_nachgegeben(nachgegeben)
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

    from datetime import UTC as _UTC_Z, datetime as _dtm_Z
    heute = _dtm_Z.now(_UTC_Z).date()

    print("\n=== Zeit je Zustand: QSOs je Stunde, nicht je Anruf ===")
    # Die Quote je Anruf ist die falsche Zielgroesse: Ein Filter, der die
    # Haelfte der Anrufe verhindert, hebt sie zwangslaeufig — und senkt
    # trotzdem die Ausbeute, wenn die gesparte Zeit im Leerlauf verstreicht.
    # Erst mit den Sekunden je Zustand laesst sich rechnen, was ein Tag
    # Betrieb bringt. Seit v0.147.0 (2026-09-14).
    if hat_tabelle(con, "state_time_daily"):
        tage = con.execute(
            "select tag, sum(sekunden) from state_time_daily "
            "where tag >= date('now', ?) and zustand not like 'ARM\\_%' escape '\\' "
            "group by tag order by tag",
            (f"-{args.tage} day",),
        ).fetchall()
        if tage:
            zeilen = []
            for tag, gesamt in tage:
                je = dict(con.execute(
                    "select zustand, sekunden from state_time_daily where tag=?", (tag,)
                ).fetchall())
                qsos = con.execute(
                    "select count(*) from qso where date(qso_start)=?", (tag,)
                ).fetchone()[0]
                std = gesamt / 3600.0
                anteil = lambda z: f"{100.0 * je.get(z, 0.0) / gesamt:4.0f} %" if gesamt else "   -"
                # Welche Tagesstunden deckt die Zeile ueberhaupt ab? Ohne das
                # laedt die Tabelle zum Fehlschluss ein: Am 15.09. standen
                # dort 2,09 QSOs/Std gegen 3,89 am Vortag — nur waren im
                # einen Fall Nacht- und Morgenstunden erfasst, im anderen
                # die ertragreichen Mittagsstunden. Quelle sind die Decodes,
                # weil sie am zuverlaessigsten sagen, wann die Station lief.
                spanne = con.execute(
                    "select min(strftime('%H', ts)), max(strftime('%H', ts)) "
                    "from decode where date(ts) = ?", (tag,)
                ).fetchone()
                stunden = f"{spanne[0]}-{spanne[1]}" if spanne and spanne[0] else "  -  "
                laeuft = " *" if tag == heute.isoformat() else ""
                # Wie viel des Tages fehlt ganz? Die Spanne allein genuegt
                # nicht: Am 14. und 15.09. stand bei beiden "00-23", der
                # eine Tag war aber nur 15,9 Stunden erfasst und der
                # andere 23,9. Wer dann QSOs/Std vergleicht, vergleicht
                # vor allem, wie viel tote Nachtzeit im Nenner steckt.
                jetzt_z = _dtm_Z.now(_UTC_Z)
                soll = 24.0 if tag != heute.isoformat() else max(
                    1.0, jetzt_z.hour + jetzt_z.minute / 60.0)
                luecke = max(0.0, soll - std)
                zeilen.append((
                    tag + laeuft, stunden, f"{std:5.1f}",
                    f"{luecke:4.1f}" if luecke >= 0.2 else "   -",
                    qsos,
                    f"{qsos / std:4.2f}" if std >= 0.5 else "  -",
                    anteil("IDLE"), anteil("CQ_CALLING"),
                    f"{100.0 * sum(v for k, v in je.items() if k.startswith('QSO_')) / gesamt:4.0f} %" if gesamt else "   -",
                    anteil("TX_LOCKED"),
                ))
            tabelle(zeilen, ("Tag", "Std UTC", "Std gemessen", "fehlt", "QSOs",
                             "QSOs/Std", "Leerlauf", "CQ", "im QSO", "gesperrt"))
            # Weichen die Messzeiten stark ab, ist der Spaltenvergleich wertlos.
            gemessen = [float(z[2]) for z in zeilen if not z[0].endswith(" *")]
            if len(gemessen) >= 2 and max(gemessen) - min(gemessen) > 4.0:
                print(f"    ACHTUNG: Die Messzeiten liegen zwischen {min(gemessen):.1f}"
                      f" und {max(gemessen):.1f} Stunden auseinander.")
                print("    QSOs/Std ist dann NICHT zwischen diesen Tagen vergleichbar:")
                print("    der laengere Tag hat mehr ertragslose Nachtstunden im")
                print("    Nenner. Dasselbe gilt fuer jeden Arm-Vergleich, der ueber")
                print("    solche Tage hinweg mittelt.")
            if any(z[0].endswith(" *") for z in zeilen):
                print("    * laufender Tag — noch unvollstaendig. 'Std UTC' nennt die")
                print("    abgedeckten Tagesstunden: Zeilen mit verschiedenen Spannen")
                print("    sind NICHT vergleichbar, die Ausbeute haengt stark an der")
                print("    Tageszeit.")
            print("    'Std gemessen' ist die Zeit, in der der Dienst lief,")
            print("    'fehlt' der Rest des Tages. QSOs/Std ist die Zielgroesse")
            print("    fuer jeden Filter-Vergleich — aber nur zwischen Tagen mit")
            print("    aehnlicher Messzeit.")
        else:
            print("    (noch keine Tageszeilen)")
    else:
        print("    (Tabelle state_time_daily fehlt — DB-Kopie aelter als v0.147.0)")

    print("\n=== Wartezeit: wie lange schweigt ein Partner, der doch noch antwortet? ===")
    # qso_max_stale_slots (6) begrenzt, wie viele Slots wir auf den Partner
    # warten. Kuerzer spart Zeit an Geistern, verliert aber jeden Erfolg,
    # dessen laengste Pause die Grenze uebersteigt. Beides steht hier, aus
    # den gespeicherten Decodes rekonstruiert: fuer jeden ausgehenden
    # Anruf alle Decodes des Partners AN UNS, daraus die laengste Luecke.
    # Gemessen am 2026-09-14 (305 Erfolge): Grenze 3 haette 30 % der
    # Erfolge gekostet, Grenze 6 kostet 4,6 %. Partner antworten in
    # Vielfachen von zwei Slots — der ungerade Slot gehoert der anderen
    # Paritaet. Seit v0.147.0.
    from collections import Counter
    from datetime import datetime as _dt, timedelta as _td
    anrufe = con.execute(
        "select ts, target_call, user_callsign, outcome from pick_attempt "
        "where ts > datetime('now', ?) and (pick_kind in ('cq', 'available') "
        "or pick_kind is null) and user_callsign is not null",
        (seit,),
    ).fetchall()
    laengste = {"completed": Counter(), "bailed": Counter()}
    for ts, call, me, outc in anrufe:
        if outc not in laengste:
            continue
        try:
            t0 = _dt.fromisoformat(str(ts))
        except ValueError:
            continue
        rows = con.execute(
            "select ts from decode where call_from=? and call_to=? and ts>=? and ts<=? order by ts",
            (call, me, str(ts), (t0 + _td(minutes=12)).isoformat(sep=" ")),
        ).fetchall()
        if not rows:
            laengste[outc]["nie"] += 1
            continue
        zeiten = [t0] + [_dt.fromisoformat(str(r[0])) for r in rows]
        luecken = [round((b - a).total_seconds() / 15) for a, b in zip(zeiten, zeiten[1:])]
        laengste[outc][min(max(luecken), 10)] += 1
    n_erfolg = sum(laengste["completed"].values())
    if n_erfolg >= 30:
        stale_jetzt = 6
        zeilen = []
        for grenze in (2, 3, 4, 6, 8):
            verloren = sum(v for k, v in laengste["completed"].items()
                           if isinstance(k, int) and k > grenze)
            zeilen.append((grenze, verloren, f"{100.0 * verloren / n_erfolg:4.1f} %",
                           "<- aktuell" if grenze == stale_jetzt else ""))
        tabelle(zeilen, ("Grenze (Slots)", "verlorene Erfolge", "Anteil", ""))
        geister = laengste["bailed"].get("nie", 0)
        print(f"    Erfolge ausgewertet: {n_erfolg}; Anrufe ohne jede Antwort: {geister}")
        print("    Jeder Slot weniger spart 15 s je Geist — aber nur, wenn die")
        print("    gesparte Zeit einen Anruf traegt (84 % der Slots haben genau")
        print("    einen Kandidaten). Vor dem Kuerzen die Spalte 'Anteil' lesen.")
    else:
        print(f"    (erst {n_erfolg} Erfolge im Zeitraum — unter 30 keine Aussage)")

    print("\n=== Kontrollarm: was die Lohnt-sich-Gates insgesamt bringen ===")
    # Seit v0.148.0 laufen ~10 % der Zeitbloecke ohne die Gates, die die
    # Erfolgschance schaetzen (SNR-Floor, Kontinent, Schwach-Gate, Pile-
    # Up, Einzelkandidat, Fernziel, Strict). Verglichen wird die Ausbeute
    # je Stunde — die Quote je Anruf waere in der Kontrolle zwangslaeufig
    # schlechter, weil sie mehr anruft. Die Stunden je Arm kommen aus dem
    # Zeitprotokoll (ARM_REGEL / ARM_KONTROLLE), die QSOs aus den Anrufen.
    if hat_spalte(con, "pick_attempt", "kontroll_arm") and hat_tabelle(con, "state_time_daily"):
        std = dict(con.execute(
            "select zustand, sum(sekunden) / 3600.0 from state_time_daily "
            "where tag >= date('now', ?) and zustand like 'ARM\\_%' escape '\\' "
            "group by zustand", (f"-{args.tage} day",),
        ).fetchall())
        ew_da = hat_spalte(con, "pick_attempt", "ew_arm")
        # Drei Spalten je Zeile — dict() davon stuerzte (bis v0.149.0), und der
        # Rauchtest sah es nicht, weil seine Anrufe keinen Arm trugen.
        qsos = {r[0]: (r[1], r[2]) for r in con.execute(
            "select case when kontroll_arm then 'ARM_KONTROLLE' "
            + ("when ew_arm then 'ARM_EW' " if ew_da else "")
            + "else 'ARM_REGEL' end, "
            "sum(outcome = 'completed'), count(*) from pick_attempt "
            "where ts > datetime('now', ?) and kontroll_arm is not null group by 1", (seit,),
        ).fetchall()}
        anrufe = {k: v[1] for k, v in qsos.items()}
        erfolge = {k: v[0] for k, v in qsos.items()}
        # Laeuft ein Arm ueberhaupt noch? Der EW-Arm ist seit v0.158.0
        # abgeschaltet, seine Zeilen stammen aus der Zeit davor. Ohne
        # Kennzeichnung liest sich die Tabelle so, als liefe der Versuch
        # weiter — und der Vergleich unten mittelt einen toten Arm gegen
        # einen lebenden. Als "laeuft noch" gilt, wer am juengsten Tag
        # mit Armdaten Zeit bekommen hat.
        letzter_tag = con.execute(
            "select max(tag) from state_time_daily where substr(zustand,1,4)='ARM_'"
        ).fetchone()[0]
        aktiv = {r[0] for r in con.execute(
            "select zustand from state_time_daily where tag=? "
            "and substr(zustand,1,4)='ARM_' and sekunden > 0", (letzter_tag,),
        ).fetchall()} if letzter_tag else set()
        zeilen = []
        for arm, name in (("ARM_REGEL", "Regel"), ("ARM_EW", "EW-Modell"), ("ARM_KONTROLLE", "Kontrolle")):
            h = std.get(arm, 0.0)
            if h <= 0.0 and arm not in aktiv:
                continue
            marke = "" if arm in aktiv else "  (aus)"
            zeilen.append((name + marke, f"{h:6.1f}", anrufe.get(arm, 0), erfolge.get(arm, 0),
                           f"{erfolge.get(arm, 0) / h:5.2f}" if h >= 1.0 else "    -",
                           f"{anrufe.get(arm, 0) / h:5.1f}" if h >= 1.0 else "    -"))
        tabelle(zeilen, ("Arm", "Stunden", "Anrufe", "QSOs", "QSOs/Std", "Anrufe/Std"))
        tot = [n for a, n in (("ARM_REGEL", "Regel"), ("ARM_EW", "EW-Modell"),
                              ("ARM_KONTROLLE", "Kontrolle"))
               if std.get(a, 0.0) > 0 and a not in aktiv]
        if tot:
            print(f"    (aus) = bekam am {letzter_tag} keine Zeit mehr:"
                  f" {', '.join(tot)}. Die Zahlen sind")
            print("    Nachlese aus der Zeit davor, kein laufender Versuch.")
        # Warnung vor zu kurzer Messzeit. Die Arme werden in 15-Minuten-
        # Bloecken zugeteilt, und bei wenigen Bloecken schwankt die
        # Verteilung erheblich: Am 15.09.2026 bekam der Regelarm 16 %
        # statt 45 % der Zeit, die Kontrolle 40 % statt 10 %. Die
        # Hash-Funktion ist sauber, das Fenster war nur klein — genau
        # deshalb gehoert der Hinweis hierher und nicht in den Code.
        # Auf 10 % genau wird die Zuteilung erst bei rund 120 Bloecken,
        # also etwa 30 Stunden je Arm.
        knapp = [name for arm, name in (("ARM_REGEL", "Regel"), ("ARM_EW", "EW-Modell"),
                                        ("ARM_KONTROLLE", "Kontrolle"))
                 if 0 < std.get(arm, 0.0) < 20.0 and arm in aktiv]
        if knapp:
            print(f"    ACHTUNG: {', '.join(knapp)} unter 20 Stunden Messzeit. Die Zuteilung")
            print("    laeuft in 15-Minuten-Bloecken und schwankt darunter stark — die")
            print("    Zeilen oben koennen die Arme deutlich ungleich getroffen haben.")
            print("    Belastbar wird der Vergleich ab etwa 30 Stunden je Arm.")
        u = urteil_rate(erfolge.get("ARM_REGEL", 0), std.get("ARM_REGEL", 0.0),
                        erfolge.get("ARM_KONTROLLE", 0), std.get("ARM_KONTROLLE", 0.0))
        print(f"    Regel gegen Kontrolle, QSOs je Stunde: {u}")
        if "ARM_EW" in aktiv:
            u2 = urteil_rate(erfolge.get("ARM_EW", 0), std.get("ARM_EW", 0.0),
                             erfolge.get("ARM_REGEL", 0), std.get("ARM_REGEL", 0.0))
            print(f"    EW-Modell gegen Regel, QSOs je Stunde: {u2}")
        print("    Liegt die Kontrolle vorn, kosten die Gates zusammen mehr als")
        print("    sie bringen. Welche das sind, sagt der naechste Abschnitt.")
        print("    Unter fuenf QSOs je Arm sagt die Zeile nichts.")
    else:
        print("    (Spalte kontroll_arm oder Tabelle state_time_daily fehlt — vor v0.148.0)")

    print("\n=== Jede Gate-Stufe einzeln: was haette sie verhindert? ===")
    # Das ist die Antwort auf "welche Regel traegt", und die einzige, die
    # ohne Nachbau der Filterlogik auskommt. Seit v0.162.0 rechnet der
    # Kontrollarm jedes Lohnt-sich-Gate weiter, wendet es aber nicht an,
    # und schreibt in haette_verworfen, welche Stufe gegriffen HAETTE.
    # Jede Zeile hier ist damit ein Feldversuch: Diese Ziele haette die
    # Regel verhindert — so oft kamen sie trotzdem durch.
    #
    # Die Vergleichszahl ist die Abschlussquote der Kandidaten, die im
    # selben Arm alle Gates passiert haben. Liegt eine Stufe darueber,
    # wirft sie die Falschen weg.
    if spalte_da(con, "pick_candidate", "haette_verworfen"):
        # Ausgang je Kandidat: angerufen wurde, wer 'gewaehlt' trug; ob es
        # klappte, steht in pick_attempt (gleicher Call, gleicher Slot).
        zeilen_g = []
        basis = con.execute(
            "select count(*), sum(a.outcome = 'completed') from pick_candidate c "
            "join pick_attempt a on a.target_call = c.call "
            "  and abs(strftime('%s', a.ts) - strftime('%s', c.slot_ts)) < 120 "
            "where c.slot_ts > datetime('now', ?) and c.kontroll_arm = 1 "
            "  and c.gewaehlt = 1 and c.haette_verworfen is null", (seit,),
        ).fetchone()
        basis_n, basis_ok = (basis[0] or 0), (basis[1] or 0)
        for stufe, n, ok in con.execute(
            "select c.haette_verworfen, count(*), sum(a.outcome = 'completed') "
            "from pick_candidate c "
            "join pick_attempt a on a.target_call = c.call "
            "  and abs(strftime('%s', a.ts) - strftime('%s', c.slot_ts)) < 120 "
            "where c.slot_ts > datetime('now', ?) and c.kontroll_arm = 1 "
            "  and c.gewaehlt = 1 and c.haette_verworfen is not null "
            "group by 1 order by 2 desc", (seit,),
        ).fetchall():
            ok = ok or 0
            quote = 100.0 * ok / n if n else 0.0
            zeilen_g.append((
                stufe, n, ok, f"{quote:5.1f} %",
                urteil(ok, n, basis_ok, basis_n) if n >= 5 and basis_n >= 5
                else "zu wenig",
            ))
        if zeilen_g:
            tabelle(zeilen_g, ("Stufe", "angerufen", "QSOs", "Quote", "gegen den Rest"))
            basis_q = 100.0 * basis_ok / basis_n if basis_n else 0.0
            print(f"    Vergleich: die {basis_n} Kontroll-Anrufe, die KEIN Gate")
            print(f"    verhindert haette, schlossen zu {basis_q:.1f} % ab.")
            print("    Eine Stufe mit hoeherer Quote wirft die Falschen weg. Eine")
            print("    mit deutlich niedrigerer hat ihren Beleg — dann beleg_datum")
            print("    in ft8_appliance/analyse/regelregister.py setzen.")
            fehlt_g = sorted(s for s in ALLE_FILTERSTUFEN
                             if s in _CHANCEN_STUFEN
                             and s not in {z[0] for z in zeilen_g})
            if fehlt_g:
                print("    Ohne Fall im Zeitraum — die Stufe kam im Kontrollarm nie")
                print("    zum Zug, nicht dass sie nichts taete:")
                zeile = "     "
                for s_ in fehlt_g:
                    if len(zeile) + len(s_) > 72:
                        print(zeile); zeile = "     "
                    zeile += " " + s_
                if zeile.strip():
                    print(zeile)
        else:
            print("    (noch keine Kontroll-Anrufe mit Schattenprotokoll — die Spalte")
            print("    fuellt sich erst seit v0.162.0, und nur in Kontrollbloecken)")
    else:
        print("    (Spalte haette_verworfen fehlt — DB-Kopie aelter als v0.162.0)")

    print("\n=== Erwartungswert-Modell: stimmt die Wahrscheinlichkeitstabelle? ===")
    # Kalibrierung: Was das Modell fuer die angerufenen Kandidaten vorher-
    # gesagt hat (p_erfolg im Kandidatenprotokoll) gegen das, was eintrat
    # (pick_attempt.outcome, derselbe Call binnen 20 s). Sagt es 10 % und
    # es treffen 10 % ein, stimmt die Tabelle — egal ob die Faktoren gut
    # gewaehlt sind. Weicht es systematisch ab, ist die Schrumpfung oder
    # die Klasseneinteilung falsch. Seit v0.150.0.
    if hat_spalte(con, "pick_candidate", "p_erfolg"):
        zeilen = con.execute(
            "select k.p_erfolg, p.outcome from pick_candidate k join pick_attempt p "
            "on p.target_call = k.call and abs(strftime('%s', p.ts) - strftime('%s', k.slot_ts)) <= 20 "
            "where k.gewaehlt = 1 and k.p_erfolg is not null and k.slot_ts > datetime('now', ?)",
            (seit,),
        ).fetchall()
        if len(zeilen) >= 30:
            stufen = [(0.0, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 1.01)]
            aus = []
            for lo, hi in stufen:
                grp = [(p, o) for p, o in zeilen if lo <= p < hi]
                if not grp:
                    continue
                n = len(grp); k = sum(1 for _, o in grp if o == "completed")
                vorher = sum(p for p, _ in grp) / n
                aus.append((f"{100*lo:3.0f}–{100*hi if hi <= 1 else 100:3.0f} %", n,
                            f"{100*vorher:5.1f} %", f"{100*k/n:5.1f} %",
                            urteil(k, n, round(vorher * n), n)))
            tabelle(aus, ("vorhergesagt", "Anrufe", "Mittel P", "eingetreten", "Abweichung"))
            print("    'Abweichung' prueft eingetreten gegen vorhergesagt. Rauschen in")
            print("    jeder Zeile heisst: die Tabelle ist kalibriert.")
        else:
            print(f"    (erst {len(zeilen)} angerufene Kandidaten mit Vorhersage — unter 30 keine Aussage)")
    else:
        print("    (Spalte p_erfolg fehlt — vor v0.150.0)")

    print("\n=== Regelregister: welche Regel braucht einen frischen Beleg? ===")
    # Jede Chancen-Regel traegt den Beleg, mit dem sie eingefuehrt wurde,
    # und ein Verfallsdatum (90 Tage). Danach gilt sie als unbelegt, bis
    # jemand nachgemessen und das Datum gesetzt hat. Das ist der Zwang,
    # der fehlte: Der SNR-Floor stammt vom Mai, das Pile-Up-Gate hat nie
    # einen Beleg gehabt. Technische Gates und Sperren verfallen nicht.
    from datetime import UTC as _UTC, datetime as _dtm
    heute = _dtm.now(_UTC).date()
    zeilen = []
    for r in sorted(REGELN, key=lambda r: (r.art != "chance", r.stufe)):
        alter = f"{(heute - r.beleg_datum).days} d" if r.beleg_datum else "nie belegt"
        status = ("UEBERFAELLIG" if r.ueberfaellig(heute)
                  else ("verfaellt nicht" if r.art != "chance" else "gueltig"))
        zeilen.append((r.stufe, r.art, str(r.beleg_datum or "-"), alter, status))
    tabelle(zeilen, ("Stufe", "Art", "Beleg vom", "Alter", "Status"))
    faellig = ueberfaellige(heute)
    if faellig:
        print(f"    {len(faellig)} Regel(n) ohne gueltigen Beleg. Nachmessen heisst:")
        for r in faellig:
            print(f"      {r.stufe}: {r.pruefung}")
        print("    Danach beleg_datum im Register setzen (ft8_appliance/analyse/regelregister.py).")

    print("\n=== Messplan: was ist faellig, was laeuft ohne Frage? ===")
    # Seit 2026-09-17, ft8_appliance/analyse/messplan.py. Die Frage hinter
    # einer Messreihe stand bis dahin bestenfalls im Modellkommentar, Regel
    # und Lesetermin nur in privaten Notizen.
    messplan_abschnitt()

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

    # 2. Sonnenindizes. Der K-Index (Unruhe des Erdmagnetfelds) wechselt
    #    alle drei Stunden, der Sonnenfluss einmal am Tag. Verglichen wird
    #    deshalb je Drei-Stunden-Block bzw. je Tag, nicht je Stunde: Stunden
    #    hintereinander sind nicht unabhaengig, und eine Korrelation ueber
    #    hundert Stunden mit sechs Tageswerten waere Scheingenauigkeit.
    #    Vorher faellt der Tagesgang heraus, sonst misst man nur, dass mittags
    #    mehr los ist als nachts. Empfangsberichte zaehlen nur in Stunden mit
    #    Berichten — ohne Sendung gibt es keine, mit Ausbreitung hat das
    #    nichts zu tun. Bis 2026-09-17 stand hier nur die Zahl der Messwerte.
    zeilen.extend(sonnen_zeilen(con, seit))

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
