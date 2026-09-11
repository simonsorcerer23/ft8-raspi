"""Die Station darf nicht dauerhaft gesperrt stehen bleiben.

``TX_LOCKED`` loest sich nicht von selbst. Fuer einen echten
Hardware-Fehler ist das richtig — bei einem Stehwellen-Weglauf soll ein
Mensch nachsehen, bevor weitergesendet wird. Falsch ist es, wenn der Grund
laengst weg ist: Am 2026-09-11 sperrte der Zeit-Waechter gegen den
Startwert von 99 s, und die Station funkte zwanzig Minuten nicht mehr, bei
tatsaechlich mikrosekundengenauer Uhr. Aufgefallen ist das nur zufaellig,
weil jemand hinsah.

Der Waechter greift erst nach einer Wartezeit — kurze Sperren im
Normalbetrieb sind kein Fall fuer ihn — und unterscheidet zwei Lagen:

* Alle Bedingungen wieder erfuellt → Sperre aufheben. Gegen Dauerpendeln
  zaehlt er die Selbstheilungen; ab der vierten je Stunde greift er nicht
  mehr ein, sondern meldet nur noch.
* Ein Grund besteht weiter → Push aufs Handy. Die Station steht still,
  das soll niemand erst Stunden spaeter merken.
"""

from __future__ import annotations

import inspect

from ft8_appliance.runtime import orchestrator as orch_mod


def _quelle() -> str:
    return inspect.getsource(orch_mod.Orchestrator._sperr_waechter_loop)


def test_waechter_wird_beim_start_mitgestartet():
    q = inspect.getsource(orch_mod.Orchestrator)
    assert 'name="sperr-waechter"' in q


def test_greift_erst_nach_einer_wartezeit():
    """Kurze Sperren im Normalbetrieb sind kein Fall fuer den Waechter."""
    assert orch_mod.Orchestrator._LOCK_GEDULD_S >= 300.0
    assert "_LOCK_GEDULD_S" in _quelle()


def test_hebt_nur_auf_wenn_alle_bedingungen_erfuellt_sind():
    q = _quelle()
    assert "first_failure" in q
    nach_pruefung = q.split("if offen is None:", 1)
    assert len(nach_pruefung) == 2, "die Pruefung muss das Aufheben umschliessen"
    assert "on_user_reset_lock" in nach_pruefung[1]


def test_pendelschutz_begrenzt_die_selbstheilung():
    """Wer viermal je Stunde heilt, hat kein haengendes Banner, sondern ein
    echtes Problem — dann soll niemand mehr automatisch eingreifen."""
    q = _quelle()
    assert "_LOCK_HEILUNG_MAX_PRO_H" in q
    assert orch_mod.Orchestrator._LOCK_HEILUNG_MAX_PRO_H <= 5
    assert "3600" in q, "das Zeitfenster der Zaehlung"


def test_meldet_wenn_der_grund_weiterbesteht():
    assert "_melde_haengende_sperre" in _quelle()


def test_push_wird_gedrosselt():
    """Eine stehende Station meldet sich sonst im Minutentakt."""
    q = inspect.getsource(orch_mod.Orchestrator._melde_haengende_sperre)
    assert "_lock_push_at" in q
    assert "3600" in q


def test_demo_betrieb_meldet_nicht():
    q = inspect.getsource(orch_mod.Orchestrator._melde_haengende_sperre)
    assert "demo_mode" in q


def test_schleife_ueberlebt_einen_fehler():
    q = _quelle()
    assert "except asyncio.CancelledError" in q
    assert "_log_loop_exc" in q


def test_zaehler_wird_zurueckgesetzt_wenn_die_sperre_faellt():
    """Sonst wuerde eine spaetere, unabhaengige Sperre sofort als lang
    stehend gelten."""
    q = _quelle()
    assert "self._lock_seit = None" in q


# ------------------------------------------------ die Schleife im Betrieb
import asyncio

import pytest

from ft8_appliance.statemachine.guards import GuardLimits, HardwareState
from ft8_appliance.statemachine.states import State


def _hw(swr=1.1):
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.02, swr=swr, alc_pct=0,
        battery_v=12.6, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
        band_allowed_for_license=True, dial_on_configured_freq=True,
        rig_freq_hz=14_074_000, rig_link_age_s=1.0,
    )


class _SM:
    def __init__(self):
        self.state = State.TX_LOCKED
        self.limits = GuardLimits(swr_max=2.0)
        self.entsperrt = 0
        self.ctx = type("C", (), {"last_lock_reason": "time_guard: Test"})()

    def on_user_reset_lock(self):
        self.entsperrt += 1
        self.state = State.IDLE


def _waechter(hw, *, runden=3):
    """Die Schleife n-mal durchlaufen lassen, ohne echte Wartezeit."""
    o = type("O", (), {})()
    o.state_machine = _SM()
    o._hardware_state = hw
    o._lock_seit = None
    o._lock_heilungen = []
    o._lock_push_at = 0.0
    o._LOCK_GEDULD_S = 0.0          # Geduld im Test abkürzen
    o._LOCK_HEILUNG_MAX_PRO_H = orch_mod.Orchestrator._LOCK_HEILUNG_MAX_PRO_H
    o.integrations = type("I", (), {"ntfy": None})()
    o.config = type("C", (), {"demo_mode": False})()
    o.meldungen = []

    async def _melde(dauer_min, grund, *, pendelt):
        o.meldungen.append((round(dauer_min), grund, pendelt))
    o._melde_haengende_sperre = _melde

    n = {"i": 0}

    async def _schlaf(_s):
        n["i"] += 1
        if n["i"] > runden:
            raise asyncio.CancelledError

    echt = orch_mod.asyncio.sleep
    orch_mod.asyncio.sleep = _schlaf
    try:
        asyncio.run(_lauf(o))
    finally:
        orch_mod.asyncio.sleep = echt
    return o


async def _lauf(o):
    try:
        await orch_mod.Orchestrator._sperr_waechter_loop(o)
    except asyncio.CancelledError:
        pass


def test_haengende_sperre_wird_aufgehoben():
    o = _waechter(_hw(), runden=3)

    assert o.state_machine.entsperrt == 1
    assert o.state_machine.state is State.IDLE


def test_echter_grund_wird_gemeldet_statt_aufgehoben():
    """SWR 9,9 heisst: Da stimmt etwas, und ein Mensch soll nachsehen."""
    o = _waechter(_hw(swr=9.9), runden=3)

    assert o.state_machine.entsperrt == 0
    assert o.state_machine.state is State.TX_LOCKED
    assert o.meldungen and o.meldungen[0][2] is False


def test_pendeln_wird_nach_drei_heilungen_gestoppt():
    """Ab der vierten Heilung je Stunde greift niemand mehr ein."""
    o = type("O", (), {})()
    o.state_machine = _SM()
    o._hardware_state = _hw()
    o._lock_seit = None
    o._lock_heilungen = []
    o._lock_push_at = 0.0
    o._LOCK_GEDULD_S = 0.0
    o._LOCK_HEILUNG_MAX_PRO_H = 3
    o.integrations = type("I", (), {"ntfy": None})()
    o.config = type("C", (), {"demo_mode": False})()
    o.meldungen = []

    async def _melde(d, g, *, pendelt):
        o.meldungen.append(pendelt)
    o._melde_haengende_sperre = _melde

    n = {"i": 0}

    async def _schlaf(_s):
        n["i"] += 1
        # Der Fehler kommt sofort zurueck: Nach jeder Heilung sperrt es
        # erneut. Genau dieses Pendeln soll der Zaehler stoppen.
        o.state_machine.state = State.TX_LOCKED
        if n["i"] > 14:
            raise asyncio.CancelledError

    echt = orch_mod.asyncio.sleep
    orch_mod.asyncio.sleep = _schlaf
    try:
        asyncio.run(_lauf(o))
    finally:
        orch_mod.asyncio.sleep = echt

    assert o.state_machine.entsperrt == 3, "nach drei Heilungen ist Schluss"
    assert True in o.meldungen, "danach wird gemeldet statt geheilt"
