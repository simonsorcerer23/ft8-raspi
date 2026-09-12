"""Die Wunschliste wurde vom Cooldown ausgehebelt.

Am 2026-09-06 rief V51WH (Namibia, auf der Wunschliste) um 21:02 CQ. Wir
riefen an, brachen ab — und als er um 21:19 erneut CQ rief, sass er noch in
der Sperrfrist. Ergebnis: 13 Decodes, ein einziger Anruf, kein QSO.

Der Pile-Up-Filter und das Fernziel-Gate nehmen Wunschlisten-Calls seit
jeher aus. Der Cooldown tat es als einziger nicht.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ft8_appliance.statemachine.machine import StateMachine


def _maschine(wunschliste: set[str]):
    ctx = SimpleNamespace(
        recent_until={},
        failed_attempt_counts={},
        watchlist_calls=set(wunschliste),
    )
    return SimpleNamespace(
        ctx=ctx,
        # Die Nachbarschritte des Abbruchs interessieren hier nicht —
        # geprueft wird allein die Laenge der Sperrfrist.
        _stamp_outcome_meta=lambda *a, **kw: None,
        _remember_unfinished_qso=lambda *a, **kw: None,
        _record_hunt_outcome=lambda *a, **kw: None,
        qso_failed_cooldown_s=900.0,          # 15 min
        qso_failed_cooldown_went_silent_multiplier=2.0,
        qso_failed_cooldown_repeat_multiplier=1.7,
        qso_failed_cooldown_max_s=3600.0,
        _WATCHLIST_COOLDOWN_TEILER=StateMachine._WATCHLIST_COOLDOWN_TEILER,
        _WATCHLIST_COOLDOWN_MIN_S=StateMachine._WATCHLIST_COOLDOWN_MIN_S,
    )


def _sperre(m, call: str, grund: str = "went_silent") -> float:
    """Sperrfrist in Sekunden, wie sie der Abbruchpfad setzen wuerde."""
    dauer = m.qso_failed_cooldown_s
    if grund == "went_silent":
        dauer *= m.qso_failed_cooldown_went_silent_multiplier
    dauer = min(dauer, m.qso_failed_cooldown_max_s)
    return StateMachine._cooldown_mit_wunschliste(m, call, dauer)


def test_ohne_wunschliste_volle_sperre():
    m = _maschine(set())
    dauer = _sperre(m, "DL1ABC")
    assert 1700 < dauer <= 1800, dauer          # 15 min x 2 = 30 min


def test_wunschliste_wird_deutlich_kuerzer_gesperrt():
    """Der Fall V51WH: nach 17 Minuten rief er erneut — da muss die
    Sperre vorbei sein."""
    m = _maschine({"V51WH"})
    dauer = _sperre(m, "V51WH")
    assert dauer < 17 * 60, f"nach 17 min noch gesperrt ({dauer:.0f} s)"
    assert dauer >= StateMachine._WATCHLIST_COOLDOWN_MIN_S


def test_kuerzung_faellt_nicht_unter_die_untergrenze():
    """Sonst rufen wir dieselbe Station in jedem Slot desselben Pile-Ups
    erneut. Die Untergrenze begrenzt die Kuerzung — sie hebt eine ohnehin
    kurze Frist aber nicht an (siehe test_kuerzung_verlaengert_nie)."""
    m = _maschine({"V51WH"})
    m.qso_failed_cooldown_s = 300.0            # 5 min, /4 waeren 75 s
    m.qso_failed_cooldown_went_silent_multiplier = 1.0
    dauer = _sperre(m, "V51WH")
    assert dauer == StateMachine._WATCHLIST_COOLDOWN_MIN_S, dauer
    assert dauer > 300.0 / StateMachine._WATCHLIST_COOLDOWN_TEILER


def test_kuerzung_verlaengert_nie():
    """Ist die regulaere Frist schon kuerzer als die Untergrenze, darf die
    Wunschliste sie nicht nach oben ziehen."""
    m_normal = _maschine(set())
    m_normal.qso_failed_cooldown_s = 30.0
    m_normal.qso_failed_cooldown_went_silent_multiplier = 1.0
    normal = _sperre(m_normal, "DL1ABC")

    m_wunsch = _maschine({"DL1ABC"})
    m_wunsch.qso_failed_cooldown_s = 30.0
    m_wunsch.qso_failed_cooldown_went_silent_multiplier = 1.0
    wunsch = _sperre(m_wunsch, "DL1ABC")
    assert wunsch <= normal + 1, (wunsch, normal)


@pytest.mark.parametrize("call", ["V51WH", "v51wh", "V51WH/P"])
def test_schreibweisen_und_suffixe(call):
    """Die Wunschliste fuehrt "V51WH"; ein Portabel-Suffix darf die
    Kuerzung nicht aushebeln."""
    m = _maschine({"V51WH"})
    dauer = _sperre(m, call)
    assert dauer < 17 * 60, (call, dauer)
