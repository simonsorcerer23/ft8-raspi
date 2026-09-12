"""Nach 'picked_another' keine volle Sperre.

Bis 2026-09-12 bekam ein Ziel, das WIR fuer ein anderes verlassen hatten,
dieselben 15 Minuten Sperre wie eines, das uns nicht gehoert hat. Gemessen
ueber 448 Faelle: 65,6 % riefen danach noch CQ, das neue Ziel brachte nichts
(17,0 % gegen 15,8 % Basis), und ein Wiederanruf binnen 15 Minuten schloss
zu 31 % ab.
"""
from types import SimpleNamespace
from ft8_appliance.statemachine.machine import StateMachine

def _m():
    return SimpleNamespace(qso_failed_cooldown_s=900.0,
                           qso_failed_cooldown_went_silent_multiplier=2.0,
                           _PICKED_ANOTHER_COOLDOWN_S=StateMachine._PICKED_ANOTHER_COOLDOWN_S)

def test_went_silent_bleibt_lang():
    assert StateMachine._cooldown_fuer_grund(_m(), "went_silent") == 1800.0

def test_picked_another_wird_gekappt():
    d = StateMachine._cooldown_fuer_grund(_m(), "picked_another")
    assert d <= 60.0, d
    assert d >= 30.0, "nicht null — sonst pendeln wir im selben Pile-Up"

def test_andere_gruende_unveraendert():
    for g in ("max_resends", "report_never_closed", "unbekannt"):
        assert StateMachine._cooldown_fuer_grund(_m(), g) == 900.0, g

def test_kappung_verlaengert_nie():
    m = _m(); m.qso_failed_cooldown_s = 20.0
    assert StateMachine._cooldown_fuer_grund(m, "picked_another") == 20.0
