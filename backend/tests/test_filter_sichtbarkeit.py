"""Jede Filterstufe des Pickers zaehlt mit, was sie wegnimmt.

Drei der fuenf Fehler vom 2026-09-10/11 waren Gates, deren Begruendung eine
spaetere Aenderung nicht ueberlebt hatte — der Randfrequenz-Filter, die
Slot-Paritaets-Pruefung und die Cooldown-Verkuerzung. Alle drei haben
monatelang still gefiltert, weil niemand sehen konnte, wie viel sie
wegnehmen.

``StateMachine.filter_drops`` summiert das jetzt pro Stufe und wird im
Status ausgegeben. Ein Gate, das plötzlich den Grossteil der Kandidaten
frisst, faellt damit auf, bevor es Wochen kostet; eines, das nie zaehlt,
ist Ballast.

Die Zaehlung darf das Ergebnis des Pickers nicht veraendern — das prueft
der letzte Test.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext


def _cq(call: str, snr: int = -5, dt: float = 0.2, hz: int = 1500) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to=None, grid="JN11",
        message=f"CQ {call} JN11", snr_db=snr, dt_s=dt,
        freq_offset_hz=hz, band="20m",
    )


def _sm() -> StateMachine:
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    sm.ctx.auto_answer = True
    return sm


def test_snr_floor_wird_gezaehlt():
    sm = _sm()
    sm.ctx.hunt_snr_floor_db = -20

    sm._pick_hunt_target([_cq("STARK", snr=-5), _cq("ZUSCHWACH", snr=-25)])

    assert sm.filter_drops.get("snr_floor") == 1


def test_dt_fenster_wird_gezaehlt():
    sm = _sm()

    sm._pick_hunt_target([_cq("PUENKTLICH", dt=0.2), _cq("ZUSPAET", dt=3.4)])

    assert sm.filter_drops.get("dt_fenster") == 1


def test_soft_blacklist_wird_gezaehlt():
    sm = _sm()
    sm.ctx.soft_blacklist = {"GESPERRT"}

    sm._pick_hunt_target([_cq("FREI"), _cq("GESPERRT")])

    assert sm.filter_drops.get("soft_blacklist") == 1


def test_zaehler_summieren_ueber_mehrere_slots():
    sm = _sm()
    sm.ctx.hunt_snr_floor_db = -20

    for _ in range(3):
        sm._pick_hunt_target([_cq("STARK", snr=-5), _cq("SCHWACH", snr=-25)])

    assert sm.filter_drops["snr_floor"] == 3


def test_stufen_die_nichts_wegnehmen_tauchen_nicht_auf():
    """Ein Gate ohne Wirkung soll die Anzeige nicht zumuellen."""
    sm = _sm()
    sm.ctx.hunt_snr_floor_db = -20

    sm._pick_hunt_target([_cq("STARK", snr=-5)])

    assert "snr_floor" not in sm.filter_drops
    assert "soft_blacklist" not in sm.filter_drops


def test_messung_veraendert_die_auswahl_nicht():
    """Dieselben Decodes, dieselbe Entscheidung — die Zaehlung ist passiv."""
    decodes = [
        _cq("SCHWACH", snr=-25), _cq("ZUSPAET", dt=3.4),
        _cq("GUT", snr=-6), _cq("AUCHGUT", snr=-8),
    ]
    sm = _sm()
    sm.ctx.hunt_snr_floor_db = -20

    erste = sm._pick_hunt_target(decodes)
    zweite = sm._pick_hunt_target(decodes)

    assert erste is not None
    assert erste.call_from == zweite.call_from, "Zaehlstand darf nicht durchschlagen"
    assert sm.filter_drops["snr_floor"] == 2, "beide Durchlaeufe gebucht"
