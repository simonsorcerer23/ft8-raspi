"""Messplan und Code muessen dieselben Messreihen kennen.

Am 17.09. wertete keine Bilanz 20 von 36 Spalten in pick_attempt aus, und
drei A/B-Tests liefen ohne festgelegtes Ende. Die Frage dahinter stand
bestenfalls im Modellkommentar, Regel und Lesetermin nur in privaten
Notizen. Dieser Test macht aus "man sollte eintragen" ein "man muss":
Eine neue Messspalte, ein neuer A/B-Schalter oder eine neue Telemetrie-
Tabelle ohne Eintrag im Messplan ist ein roter Test.
"""
from __future__ import annotations

import re
from pathlib import Path

from ft8_appliance.analyse.messplan import (
    KERN_PICK_ATTEMPT, MESSPLAN, STATUS, TELEMETRIE_TABELLEN, abgedeckte_groessen,
    abgedeckte_schalter,
)
from ft8_appliance.config.models import OperatingConfig
from ft8_appliance.db import models as m

WURZEL = Path(__file__).resolve().parents[2]
BILANZ = (WURZEL / "scripts" / "qso_bilanz.py").read_text()


def _spalten(tabelle: str) -> set[str]:
    return {c.name for c in m.Base.metadata.tables[tabelle].columns}


def _ab_schalter() -> set[str]:
    return {n for n in OperatingConfig.model_fields
            if re.search(r"(_ab|_ab_test|kontrollarm_anteil)$", n)}


def test_jede_messspalte_der_anrufversuche_hat_eine_frage() -> None:
    abgedeckt = {g.split(".", 1)[1] for g in abgedeckte_groessen()
                 if g.startswith("pick_attempt.")}
    fehlt = _spalten("pick_attempt") - KERN_PICK_ATTEMPT - abgedeckt
    assert not fehlt, ("Spalten ohne Eintrag im Messplan — was soll mit ihnen "
                       f"geprueft werden? {sorted(fehlt)}")


def test_anrufversuche_nicht_als_ganze_tabelle_abgehakt() -> None:
    """Sonst deckte ein einziger Eintrag 'pick_attempt' jede neue Spalte ab."""
    assert "pick_attempt" not in abgedeckte_groessen()


def test_jeder_ab_schalter_steht_im_messplan() -> None:
    fehlt = _ab_schalter() - abgedeckte_schalter()
    assert not fehlt, f"A/B-Schalter ohne Frage, Regel und Ende: {sorted(fehlt)}"


def test_jede_telemetrie_tabelle_steht_im_messplan() -> None:
    groessen = abgedeckte_groessen()
    fehlt = {t for t in TELEMETRIE_TABELLEN
             if not any(g == t or g.startswith(t + ".") for g in groessen)}
    assert not fehlt, f"Telemetrie ohne Frage: {sorted(fehlt)}"


def test_jede_messgroesse_gibt_es_wirklich() -> None:
    """Ein Tippfehler im Plan saehe aus wie eine abgedeckte Spalte."""
    tabellen = m.Base.metadata.tables
    falsch = []
    for messung in MESSPLAN:
        for g in messung.messgroessen:
            if g.startswith("config:"):
                if g.removeprefix("config:") not in OperatingConfig.model_fields:
                    falsch.append(g)
            elif "." in g:
                t, s = g.split(".", 1)
                if t not in tabellen or s not in _spalten(t):
                    falsch.append(g)
            elif g not in tabellen:
                falsch.append(g)
        for s in messung.schalter:
            if s not in OperatingConfig.model_fields:
                falsch.append(f"schalter:{s}")
    assert not falsch, f"gibt es nicht: {falsch}"


def test_jede_genannte_auswertung_steht_in_der_bilanz() -> None:
    fehlt = [(x.schluessel, x.auswertung) for x in MESSPLAN
             if x.auswertung and f"=== {x.auswertung}" not in BILANZ]
    assert not fehlt, f"Abschnitt nicht in scripts/qso_bilanz.py: {fehlt}"


def test_eintraege_sind_vollstaendig() -> None:
    schluessel = [x.schluessel for x in MESSPLAN]
    assert len(schluessel) == len(set(schluessel)), "Schluessel doppelt"
    for x in MESSPLAN:
        assert x.status in STATUS, x.schluessel
        assert x.frage.strip(), x.schluessel
        if x.status == "laufend":
            assert x.auswertung and x.entscheidungsregel and x.lesen_ab and x.danach, (
                f"{x.schluessel}: laufend braucht Auswertung, Regel, Lesetermin und 'danach'")
        if x.status == "dauerhaft":
            assert x.auswertung and x.entscheidungsregel, x.schluessel
        if x.status == "entschieden":
            assert x.ergebnis and x.danach, f"{x.schluessel}: womit entschieden?"
        if x.status == "ohne_auswertung":
            assert x.auswertung is None, (
                f"{x.schluessel}: hat eine Auswertung — dann ist er nicht 'ohne_auswertung'")


def test_ohne_auswertung_heisst_wirklich_ohne_leser() -> None:
    """Liest die Bilanz eine Spalte doch, stimmt der Status nicht mehr."""
    gelesen = []
    for x in MESSPLAN:
        if x.status != "ohne_auswertung":
            continue
        for g in x.messgroessen:
            spalte = g.split(".", 1)[-1]
            if re.search(rf"\b{re.escape(spalte)}\b", BILANZ):
                gelesen.append((x.schluessel, spalte))
    assert not gelesen, f"wird in der Bilanz gelesen: {gelesen}"
