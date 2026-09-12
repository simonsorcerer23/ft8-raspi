"""Stunden-Tier aus der Anruf-Telemetrie statt aus der QSO-Tabelle.

Die alte Quelle (``active_continent_hours``) zaehlt, wann wir QSOs
*hatten* — der Nenner sind Erfolge, nicht Versuche, und damit misst sie
genau das, was der Tier vorhersagen soll. Die neue Quelle setzt die
Abschluesse ins Verhaeltnis zu den Anrufen derselben Zelle.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ft8_appliance.runtime.orchestrator import (
    _ZELLEN_SHRINK_K,
    _arm_aus_block,
    _utc_stunde,
    _zellen_quoten,
)
from ft8_appliance.statemachine.machine import _tier_active_hour
from ft8_appliance.statemachine.states import MachineContext


class _Decode:
    def __init__(self, call_from: str | None) -> None:
        self.call_from = call_from


# --------------------------------------------------------------- Schrumpfung


def test_kleine_zelle_bleibt_am_kontinentmittel() -> None:
    """Zwei Anrufe, keiner vollendet — das darf die Zelle nicht auf null ziehen."""
    quoten = _zellen_quoten({("NA", 3): [0, 0]}, {"NA": 0.10}, 0.12)
    wert = quoten[("NA", 3)]
    assert wert == pytest.approx((0 + _ZELLEN_SHRINK_K * 0.10) / (2 + _ZELLEN_SHRINK_K))
    # naeher am Kontinentmittel als an der rohen Null
    assert abs(wert - 0.10) < abs(wert - 0.0)


def test_grosse_zelle_setzt_sich_durch() -> None:
    """Bei 400 Anrufen zaehlt die eigene Quote, nicht mehr der Prior."""
    werte = [1] * 200 + [0] * 200          # rohe Quote 50 %
    quoten = _zellen_quoten({("EU", 12): werte}, {"EU": 0.10}, 0.12)
    assert quoten[("EU", 12)] > 0.45


def test_ohne_kontinentquote_zieht_die_gesamtquote() -> None:
    quoten = _zellen_quoten({("OC", 7): [0]}, {}, 0.20)
    assert quoten[("OC", 7)] == pytest.approx(
        (0 + _ZELLEN_SHRINK_K * 0.20) / (1 + _ZELLEN_SHRINK_K)
    )


def test_schrumpfung_ist_wirksam() -> None:
    """Mutationsprobe: ohne Schrumpfung waere die Zelle exakt 0,0 bzw. 1,0."""
    leer = _zellen_quoten({("AF", 0): [0, 0, 0]}, {"AF": 0.08}, 0.12)[("AF", 0)]
    voll = _zellen_quoten({("AF", 1): [1, 1, 1]}, {"AF": 0.08}, 0.12)[("AF", 1)]
    assert leer > 0.0, "ungeschrumpfte Null — die Schrumpfung fehlt"
    assert voll < 1.0, "ungeschrumpfte Eins — die Schrumpfung fehlt"


# ------------------------------------------------------------------- Stunde


def test_naiver_zeitstempel_gilt_als_utc() -> None:
    """SQLite liefert DateTime ohne tzinfo; als Ortszeit gelesen waere die
    Stunde im Sommer um zwei verschoben."""
    assert _utc_stunde(datetime(2026, 9, 12, 14, 30)) == 14  # noqa: DTZ001 — der naive Wert IST der Pruefgegenstand


def test_zeitzone_wird_umgerechnet() -> None:
    from datetime import timedelta, timezone
    mesz = timezone(timedelta(hours=2))
    assert _utc_stunde(datetime(2026, 9, 12, 16, 30, tzinfo=mesz)) == 14


def test_ohne_zeitstempel_keine_zelle() -> None:
    assert _utc_stunde(None) is None


# ---------------------------------------------------------------------- A/B


def test_salz_trennt_die_beiden_tests() -> None:
    """Ohne eigenes Salz bekaemen Fernziel-Gate und Zellen-Tier in jedem
    Block denselben Arm — ihre Effekte waeren nicht zu trennen."""
    bloecke = range(400)
    gleich = sum(
        1 for b in bloecke if _arm_aus_block(b) == _arm_aus_block(b, "zellen")
    )
    assert 150 < gleich < 250, f"Arme laufen nicht unabhaengig ({gleich}/400 gleich)"


def test_ohne_salz_bleibt_der_alte_arm() -> None:
    """Der laufende Fernziel-Test darf seine Zuordnung nicht verlieren."""
    import hashlib
    for b in (0, 1, 7, 12345):
        erwartet = hashlib.sha256(str(b).encode("ascii")).digest()[0] & 1 == 1
        assert _arm_aus_block(b) is erwartet


# ---------------------------------------------------------------------- Tier


def _ctx(**kw) -> MachineContext:
    ctx = MachineContext(callsign="DO3XR", my_grid="JN58")
    ctx.call_to_continent = {"W1AW": "NA"}
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def test_arm_b_nutzt_die_zellenquote() -> None:
    h = datetime.now(UTC).hour
    gut = _ctx(zellen_arm=True, zellen_success={("NA", h): 0.20}, zellen_success_overall=0.12)
    schlecht = _ctx(zellen_arm=True, zellen_success={("NA", h): 0.04}, zellen_success_overall=0.12)
    assert _tier_active_hour(_Decode("W1AW"), gut) == 1
    assert _tier_active_hour(_Decode("W1AW"), schlecht) == 0


def test_arm_a_nutzt_die_alte_stundenliste() -> None:
    h = datetime.now(UTC).hour
    ctx = _ctx(
        zellen_arm=False,
        active_continent_hours={("NA", h)},
        # bewusst gegenlaeufig: die Zellenquote wuerde 0 sagen
        zellen_success={("NA", h): 0.01},
        zellen_success_overall=0.12,
    )
    assert _tier_active_hour(_Decode("W1AW"), ctx) == 1


def test_arm_b_ignoriert_die_alte_liste() -> None:
    """Mutationsprobe: faellt die Weiche weg, schlaegt die alte Liste durch."""
    h = datetime.now(UTC).hour
    ctx = _ctx(
        zellen_arm=True,
        active_continent_hours={("NA", h)},     # alte Quelle sagt "gut"
        zellen_success={("NA", h): 0.01},       # neue Quelle sagt "schlecht"
        zellen_success_overall=0.12,
    )
    assert _tier_active_hour(_Decode("W1AW"), ctx) == 0


def test_unbekannte_zelle_entscheidet_nichts() -> None:
    ctx = _ctx(zellen_arm=True, zellen_success={}, zellen_success_overall=0.12)
    assert _tier_active_hour(_Decode("W1AW"), ctx) == 0


def test_unbekannter_kontinent_entscheidet_nichts() -> None:
    ctx = _ctx(zellen_arm=True, zellen_success={("NA", 0): 0.9}, zellen_success_overall=0.12)
    assert _tier_active_hour(_Decode("XX0XXX"), ctx) == 0
