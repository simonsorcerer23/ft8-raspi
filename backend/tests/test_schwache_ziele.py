"""Der Filter fuer schwache Ziele: adaptiv und wertabhaengig.

Zwei Aenderungen vom 2026-09-14, beide auf Sebastians Nachfragen hin:

1. *Adaptiv.* Der Filter greift nur, wenn danach ein Kandidat uebrig
   bleibt. Sonst lautet die Entscheidung nicht "schwaches Ziel oder
   starkes", sondern "schwaches Ziel oder gar keins". Gemessen: Mit
   Auswahl bringt ein starkes Ziel 31,3 %, ein schwaches 9,7 % — da ist
   der Filter richtig. Ohne Auswahl bringt das schwache 8,4 % gegen null.
   85 % aller Anrufe an schwache Ziele waren alternativlos.

2. *Wertabhaengig.* Ein neues DXCC oder eine seltene Station darf 4 dB
   tiefer liegen. Begruendung ist NICHT eine hoehere Trefferquote — die
   Daten geben das nicht her —, sondern der hoehere Wert des QSOs.
"""
from __future__ import annotations

import pytest

from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import MachineContext


class _D:
    def __init__(self, call, snr, grid=None):
        self.call_from = call
        self.snr_db = snr
        self.grid = grid


def _sm(**kw):
    ctx = MachineContext(callsign="DK9XR", my_grid="JN58")
    for k, v in kw.items():
        setattr(ctx, k, v)
    sm = object.__new__(StateMachine)
    sm.ctx = ctx
    return sm


def test_gewoehnliches_ziel_behaelt_die_schwelle() -> None:
    sm = _sm(new_dxcc_calls=set(), rarity_scores={})
    assert sm._schwach_schwelle(_D("DL1ABC", -14), -13) == -13


def test_neues_dxcc_darf_tiefer_liegen() -> None:
    sm = _sm(new_dxcc_calls={"FT8WW"}, rarity_scores={})
    assert sm._schwach_schwelle(_D("FT8WW", -16), -13) == -17


def test_seltene_station_darf_tiefer_liegen() -> None:
    sm = _sm(new_dxcc_calls=set(), rarity_scores={"3Y0J": 60})
    assert sm._schwach_schwelle(_D("3Y0J", -16), -13) == -17


def test_knapp_unter_der_seltenheitsgrenze_zaehlt_nicht() -> None:
    """Ohne feste Grenze waere jede Station irgendwie 'selten'."""
    sm = _sm(new_dxcc_calls=set(), rarity_scores={"EA1XYZ": 39})
    assert sm._schwach_schwelle(_D("EA1XYZ", -16), -13) == -13


def test_ohne_rufzeichen_keine_absenkung() -> None:
    sm = _sm(new_dxcc_calls=set(), rarity_scores={})
    assert sm._schwach_schwelle(_D(None, -16), -13) == -13


def test_bonus_ist_nur_eine_stufe() -> None:
    """Neues DXCC UND selten senkt nicht doppelt — das waere mit diesen
    Fallzahlen nicht mehr zu belegen."""
    sm = _sm(new_dxcc_calls={"FT8WW"}, rarity_scores={"FT8WW": 95})
    assert sm._schwach_schwelle(_D("FT8WW", -20), -13) == -17


def test_bonus_hat_eine_begruendete_groesse() -> None:
    """4 dB, weil die Quote zwischen -17 und -13 dB flach bei rund 9,5 %
    liegt und erst darunter auf 4,4 % faellt."""
    assert StateMachine._SCHWACH_BONUS_DB == 4
