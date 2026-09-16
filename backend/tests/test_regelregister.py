"""Regelregister: Code, Register und Bilanz muessen dieselben Stufen kennen,
und Chancen-Regeln verfallen ohne frischen Beleg."""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from ft8_appliance.analyse.regelregister import REGELN, Regel, stufen, ueberfaellige

WURZEL = Path(__file__).resolve().parents[2]


def _gebuchte_stufen_im_code() -> set[str]:
    """Jede Stufe, die im Picker eine Verwerfung protokolliert.

    Seit v0.162.0 gibt es dafuer zwei Wege: `_buche_filter` wendet an und
    bucht, `_gate` tut dasselbe, protokolliert im Kontrollarm aber nur.
    Die beiden Abbruch-Gates notieren im Kontrollarm direkt in
    `_haette_verworfen` — auch das ist eine gebuchte Stufe. Wer hier
    einen Weg vergisst, erklaert eine lebende Regel fuer tot.
    """
    quelle = (WURZEL / "backend" / "ft8_appliance" / "statemachine" / "machine.py").read_text()
    namen = set(re.findall(r'_buche_filter\("([a-z_]+)"', quelle))
    namen |= set(re.findall(r'self\._gate\(\s*\n?\s*"([a-z_]+)"', quelle))
    namen |= set(re.findall(r'_haette_verworfen\.setdefault\([^,]+,\s*"([a-z_]+)"', quelle))
    return namen - {"schwach_zurueckgenommen"}


def test_register_deckt_jede_gebuchte_stufe() -> None:
    """Eine Stufe im Code ohne Registereintrag hat keinen Beleg und kein
    Verfallsdatum — genau der Zustand, den das Register beenden soll."""
    fehlt = _gebuchte_stufen_im_code() - stufen()
    assert not fehlt, f"im Register fehlen: {sorted(fehlt)}"


def test_register_nennt_keine_tote_stufe() -> None:
    tot = stufen() - _gebuchte_stufen_im_code()
    assert not tot, f"im Code nicht mehr gebucht: {sorted(tot)}"


def test_bilanz_liest_die_stufen_aus_dem_register() -> None:
    """Zwei Listen driften. Die Bilanz darf keine eigene fuehren."""
    quelle = (WURZEL / "scripts" / "qso_bilanz.py").read_text()
    assert "ALLE_FILTERSTUFEN = _register_stufen()" in quelle
    assert not re.search(r"ALLE_FILTERSTUFEN = \{", quelle)


def test_bilanz_rechnet_mit_derselben_statistik() -> None:
    quelle = (WURZEL / "scripts" / "qso_bilanz.py").read_text()
    assert "from ft8_appliance.analyse.stochastik import" in quelle
    assert not re.search(r"^def urteil\(", quelle, re.M)
    assert not re.search(r"^def urteil_rate\(", quelle, re.M)


def test_jede_regel_hat_art_und_pruefung() -> None:
    for r in REGELN:
        assert r.art in ("chance", "technisch", "sperre"), r.stufe
        assert r.behauptung and r.beleg and r.pruefung, r.stufe


def test_chancenregel_verfaellt_nach_90_tagen() -> None:
    r = Regel("x", "chance", "b", "beleg n=10", date(2026, 1, 1), "p")
    assert not r.ueberfaellig(date(2026, 3, 31))
    assert r.ueberfaellig(date(2026, 4, 2))


def test_ohne_beleg_sofort_ueberfaellig() -> None:
    assert Regel("x", "chance", "b", "keiner", None, "p").ueberfaellig(date(2026, 1, 1))


def test_technische_und_sperren_verfallen_nie() -> None:
    alt = date(2020, 1, 1)
    assert not Regel("x", "technisch", "b", "k", alt, "p").ueberfaellig(date(2026, 9, 14))
    assert not Regel("x", "sperre", "b", "k", None, "p").ueberfaellig(date(2026, 9, 14))


def test_stand_am_14_09_2026() -> None:
    """Dokumentiert den Startzustand: drei Chancen-Regeln ohne gueltigen
    Beleg. Wer eine belegt, aendert diesen Test bewusst mit."""
    assert sorted(r.stufe for r in ueberfaellige(date(2026, 9, 14))) == \
        ["pile_up", "snr_floor", "strict_modus"]


def test_alle_chancenregeln_verfallen_spaetestens_in_90_tagen() -> None:
    """Kein Eintrag darf sich ein laengeres Verfallsdatum geben."""
    for r in REGELN:
        if r.art == "chance" and r.beleg_datum:
            assert r.ueberfaellig(r.beleg_datum + timedelta(days=91)), r.stufe
