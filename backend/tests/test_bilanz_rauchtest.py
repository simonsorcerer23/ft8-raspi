"""Laeuft die Bilanz ueberhaupt noch durch — und liefert jeder Abschnitt etwas?

Die Auswertung traegt jede Entscheidung ueber Filter, Gates und Schwellen,
war aber bis 2026-09-12 ungetestet. Ein Abschnitt fiel dabei monatelang
still aus: Er schickte einen Header, den die API nicht liest, und meldete
"Station nicht erreichbar" — was nach einem Netzproblem aussah, nicht nach
einem Fehler im Skript.

Dieser Test baut eine kleine Datenbank mit bekannten Zeilen und prueft, dass
die Bilanz sie ohne Absturz verarbeitet und keine Abteilung leer laesst, die
Daten haben muesste.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "qso_bilanz.py"


def _baue_db(pfad: Path) -> None:
    """Eine Datenbank mit genau den Tabellen, die die Bilanz anfasst.

    Ueber die ORM-Modelle statt per rohem SQL — so greifen deren Defaults,
    und der Test veraltet nicht, sobald eine Pflichtspalte dazukommt.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from ft8_appliance.db import models as m

    eng = create_engine(f"sqlite:///{pfad}")
    m.Base.metadata.create_all(eng)
    jetzt = datetime.now(UTC)

    def t(minuten: int) -> datetime:
        return jetzt - timedelta(minutes=minuten)

    with Session(eng) as s:
        for i in range(12):
            s.add(m.PickAttempt(
                ts=t(10 + i * 7), target_call=f"K{i}TEST", user_callsign="DK9XR",
                psk_heard_us=bool(i % 2), snr_db=-10 - i, dt_s=0.2, band="20m",
                outcome="completed" if i % 3 == 0 else "bailed",
                bail_reason=None if i % 3 == 0 else "went_silent",
                winning_tier="sole" if i % 2 else "psk_heard_us",
                n_candidates=1 + i % 3, freq_offset_hz=300 + i * 90,
            ))
        # Der Decode muss VOR dem Anruf liegen — die Bilanz ordnet die
        # Audiofrequenz ueber das letzte Signal vor dem Versuch zu.
        for i in range(20):
            s.add(m.Decode(
                ts=t(12 + i * 7), call_from=f"K{i}TEST", call_to=None,
                message=f"CQ K{i}TEST JN58", snr_db=-12 - (i % 9), dt_s=0.1,
                freq_offset_hz=300 + i * 90, band="20m", grid="JN58",
            ))
        for i in range(6):
            s.add(m.Qso(
                call=f"K{i}TEST", band="20m", freq_hz=14_074_000, mode="FT8",
                rst_sent=-12, rst_rcvd=-8, qso_start=t(20 + i * 30),
                qso_end=t(19 + i * 30), my_grid="JN58", user_callsign="DK9XR",
                qrz_uploaded=True, clublog_uploaded=True,
            ))
        for i in range(8):
            s.add(m.PskReporterIn(
                ts=t(4 + i * 6), rx_call=f"W{i}RX", rx_grid="FN20",
                snr_db=-14 - i, band="20m",
            ))
        for i in range(4):
            s.add(m.PathPrediction(
                ts=t(6 + i * 15), ziel_grid="FN20", muf_sp=12.0 + i,
                muf_lp=11.0 + i, luf_sp=3.0, luf_lp=4.0,
            ))
        s.add(m.Watchlist(call="V51WH", added=jetzt))
        # Die drei Umgebungsreihen, die bis 2026-09-12 ohne Leser liefen.
        for i in range(10):
            s.add(m.BandNoise(ts=t(20 + i * 55), band="20m", freq_hz=14_074_000,
                              s_meter_db=-54, rx_audio_dbfs=-30.0 - i))
            s.add(m.SwrLog(ts=t(18 + i * 55), band="20m", freq_hz=14_074_000,
                           swr=1.0 + i * 0.01))
        # Tageshistorie der Filterstufen: zwei Tage, eine Stufe ohne Treffer
        for tage_zurueck in (0, 1):
            tag = (jetzt - timedelta(days=tage_zurueck)).strftime("%Y-%m-%d")
            for stufe, n in (("cooldown", 120 + tage_zurueck),
                             ("schwach_ohne_psk", 80), ("snr_floor", 1)):
                s.add(m.FilterDropDaily(tag=tag, stufe=stufe, anzahl=n))
        s.commit()
    eng.dispose()


def _laufe(db: Path) -> str:
    r = subprocess.run(
        [sys.executable, str(SKRIPT), "--db", str(db), "--tage", "7"],
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


@pytest.fixture(scope="module")
def ausgabe(tmp_path_factory) -> str:
    db = tmp_path_factory.mktemp("bilanz") / "qso.sqlite"
    _baue_db(db)
    return _laufe(db)


def test_laeuft_ohne_absturz(ausgabe):
    assert ausgabe.strip()


# Die Abschnitte, die es geben muss. Nur zu zaehlen reicht nicht — bei
# sechzehn Ueberschriften faellt das Fehlen einer einzelnen durch jede
# Mindestanzahl hindurch (nachgewiesen mit einer Mutationsprobe am
# 2026-09-12). Wer einen Abschnitt bewusst entfernt, streicht ihn hier mit.
ERWARTETE_ABSCHNITTE = (
    "QSOs pro Tag",
    "Anrufversuche nach Weg",
    "Ausgang im Detail",
    "R-Reports an uns ohne QSO",
    "PSK-Datenlage",
    "Hatte der Picker eine Wahl?",
    "FT8-MUF",
    "Kommen wir an?",
    "Ausbreitung oder Konkurrenz?",
    "Fernziel-Gate",
    "Vorab-Decode",
    "Abschluss nach Signalstaerke",
    "Datenbasis",
    "Was die Filterstufen des Pickers wegnehmen",
    "Wunschliste",
    "Bandrand",
    "Filterstufen ueber die Tage",
    "Umgebung",
)


def test_alle_abschnitte_erscheinen(ausgabe):
    kopf = re.findall(r"^=== (.+?) ===$", ausgabe, re.M)
    fehlend = [a for a in ERWARTETE_ABSCHNITTE
               if not any(a in k for k in kopf)]
    assert not fehlend, f"fehlende Abschnitte: {fehlend}"


def test_kein_abschnitt_meldet_einen_fehler(ausgabe):
    for muster in ("Traceback", "nicht erreichbar", "Error", "Exception"):
        assert muster not in ausgabe, muster


def test_abschnitte_mit_daten_bleiben_nicht_leer(ausgabe):
    """Die Tabellen sind gefuellt — wer hier "(keine Daten)" meldet, filtert
    an den Daten vorbei."""
    teile = re.split(r"\n(?==== )", ausgabe)
    leer = [t.split("\n")[0].strip("= ") for t in teile
            if t.startswith("===") and "(keine Daten)" in t]
    # Ein paar Abschnitte duerfen leer sein (A/B laeuft im Test nicht),
    # aber nicht die Mehrheit.
    assert len(leer) <= 5, leer


def test_signifikanzspalte_erscheint(ausgabe):
    """Ohne sie liest man Rauschen als Befund."""
    assert "Rauschen" in ausgabe or "z=" in ausgabe


def test_stufen_ohne_treffer_werden_benannt(ausgabe):
    """Der eigentliche Zweck der Historie: eine Stufe, die im ganzen
    Zeitraum nie greift, faellt sonst gar nicht auf — sie fehlt einfach in
    der Tabelle und sieht aus wie nicht vorhanden."""
    assert "Ohne einen einzigen Treffer" in ausgabe
    for stufe in ("bandrand", "pile_up", "strict_modus"):
        assert stufe in ausgabe, stufe


def test_historie_zeigt_mehrere_tage(ausgabe):
    """Gezielt die Spalte "Tage mit Daten" pruefen, nicht irgendeine Zwei
    im Abschnitt — eine lose Suche laesst eine kaputte Zaehlung durch
    (nachgewiesen mit einer Mutationsprobe)."""
    abschnitt = ausgabe.split("=== Filterstufen ueber die Tage")[1]
    zeile = next((z for z in abschnitt.split("\n")
                  if z.strip().startswith("cooldown")), None)
    assert zeile is not None, abschnitt
    spalten = zeile.split()
    # Stufe, Tage mit Daten, verworfen gesamt, bester Tag
    assert len(spalten) >= 4, spalten
    assert int(spalten[1]) == 2, f"Tage mit Daten falsch: {spalten}"
    assert int(spalten[2]) == 241, f"Summe falsch: {spalten}"  # 120 + 121


def test_umgebungsreihen_haben_einen_abnehmer(ausgabe):
    """band_noise, solar_log und swr_log liefen bis 2026-09-12 ohne einen
    einzigen Leser: geschrieben, neunzig Tage aufgehoben, geloescht. Dieser
    Abschnitt ist ihr Abnehmer — faellt er weg, sammeln sie wieder ins Leere."""
    abschnitt = ausgabe.split("=== Umgebung")[1]
    for frage in ("Rauschflur", "Sonnenindizes", "SWR-Verlauf"):
        assert frage in abschnitt, frage


def test_umgebung_sagt_wann_die_daten_reichen(ausgabe):
    """Ohne diese Angabe liest man aus drei Datenpunkten einen Befund."""
    abschnitt = ausgabe.split("=== Umgebung")[1]
    assert "zu wenige" in abschnitt or "auswertbar" in abschnitt
    assert "Ballast" in abschnitt
