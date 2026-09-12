"""Drei Messreihen, die es bis 2026-09-12 nicht gab.

Alle drei betreffen dieselbe offene Frage: Zwei Drittel unserer Anrufe
bekommen nie eine Antwort. Ob das an der Ausbreitung liegt, am Stoerpegel
oder an geomagnetischen Bedingungen, liess sich nicht pruefen — die Daten
wurden abgerufen und weggeworfen oder gar nicht erst erhoben.

* **Sonnendaten** (SFI, A, K) wurden seit jeher von hamqsl.com geholt und
  nur angezeigt. Der K-Index misst geomagnetische Stoerungen, die
  besonders Nordpolarpfade treffen — also genau die
  Nordamerika-Verbindungen mit ihren 3,7 % Abschlussquote.
* **Rauschflur**: Die Zahl der Decodes misst die Aktivitaet im Band, nicht
  die Stoerung. Bei hohem Rauschen sind schwache Signale chancenlos.
* **Pfad-Vorhersage** (MUF/LUF): Die Zahl der Decodes zeigt, wer *uns*
  erreicht — nicht, ob *wir* ankommen.
"""

from __future__ import annotations

import inspect

from ft8_appliance.db import repository
from ft8_appliance.db.models import BandNoise, PathPrediction, SolarLog
from ft8_appliance.runtime import orchestrator as orch_mod


# ----------------------------------------------------------- Sonnendaten
def test_sonnendaten_werden_gespeichert():
    q = inspect.getsource(orch_mod.Orchestrator._solar_refresh_loop)
    assert "_persist_solar" in q


def test_sonnendaten_schreibfehler_kostet_nicht_den_abruf():
    q = inspect.getsource(orch_mod.Orchestrator._persist_solar)
    assert "except Exception" in q


def test_k_index_steht_im_log():
    """Sonst muesste man fuer den haeufigsten Verdaechtigen in die
    Datenbank schauen."""
    q = inspect.getsource(orch_mod.Orchestrator._solar_refresh_loop)
    assert "k_index" in q


# ------------------------------------------------------------ Rauschflur
def test_rauschen_nur_im_empfang():
    """Waehrend der eigenen Aussendung zeigt das S-Meter nichts
    Brauchbares."""
    q = inspect.getsource(orch_mod.Orchestrator._buche_rauschen)
    assert "_tx_burst_active" in q


def test_rauschen_speichert_das_minimum():
    """Das S-Meter steigt mit jedem einlaufenden Signal — der Rauschflur
    ist der untere Rand. Ein Momentwert misst den Zufall, ob gerade jemand
    sendet."""
    q = inspect.getsource(orch_mod.Orchestrator._buche_rauschen)
    assert "min(proben)" in q


def test_rauschen_wird_gedrosselt():
    """Der Rig-Poll laeuft jede Sekunde."""
    q = inspect.getsource(orch_mod.Orchestrator._buche_rauschen)
    assert "60.0" in q


# ------------------------------------------------------ Pfad-Vorhersage
def test_pfad_vorhersage_ist_schonend():
    """prop.kc2g.com wird kostenlos betrieben — eine Handvoll Richtungen,
    mit Pausen dazwischen, statt je Anruf."""
    q = inspect.getsource(orch_mod.Orchestrator._pfad_vorhersage_loop)
    assert "asyncio.sleep(5)" in q, "Pause zwischen den Richtungen"
    assert len(orch_mod.Orchestrator._PFAD_REFERENZEN) <= 10


def test_abruf_folgt_dem_stundenlauf_des_dienstes():
    """Die Vorhersage wird stuendlich gerechnet — latest_run.json nennt
    eine run_id und 25 Karten im Stundenraster. Haeufiger abzufragen
    liefert dieselben Zahlen; ein Viertelstundentakt haette viermal je
    Stunde umsonst angeklopft.

    Deshalb zuerst die winzige run_id-Abfrage, und die acht Richtungen nur
    bei einem neuen Lauf."""
    q = inspect.getsource(orch_mod.Orchestrator._pfad_vorhersage_loop)
    assert "_hole_lauf_id" in q
    assert "letzter_lauf" in q
    # Die Richtungsabfragen haengen an der Pruefung, nicht am Takt
    vor_pruefung = q.split("if lauf is not None and lauf != letzter_lauf:", 1)[0]
    assert "_hole_pfad_vorhersage" not in vor_pruefung


def test_pfad_vorhersage_nennt_sich_beim_namen():
    """Ein fremder Dienst soll erkennen koennen, wer da fragt."""
    q = inspect.getsource(orch_mod.Orchestrator._hole_pfad_vorhersage)
    assert "User-Agent" in q


def test_pfad_vorhersage_blockiert_den_slot_nicht():
    """urllib ist blockierend — das gehoert in einen Thread, sonst steht
    der Decoder still."""
    q = inspect.getsource(orch_mod.Orchestrator._pfad_vorhersage_loop)
    assert "to_thread" in q


def test_ausfall_des_dienstes_kostet_nur_die_beobachtung():
    q = inspect.getsource(orch_mod.Orchestrator._hole_pfad_vorhersage)
    assert "except Exception" in q
    assert "return None" in q


# ------------------------------------------------------------ Aufraeumen
def test_alle_drei_werden_mit_gepruned():
    """Telemetrie-Tabellen wachsen sonst unbegrenzt — SD voll, DB kaputt,
    QSOs weg. Die qso-Tabelle ist bewusst nicht dabei."""
    tabellen = {t.__tablename__ for t, _ in repository._TELEMETRY_TABLES}
    assert {"solar_log", "band_noise", "path_prediction"} <= tabellen
    assert "qso" not in tabellen
