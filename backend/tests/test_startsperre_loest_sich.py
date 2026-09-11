"""Eine Sperre aus der Startphase loest sich, sobald echte Werte da sind.

Vor der ersten Messung steht ``_hardware_state`` bewusst auf lauter
unmoeglichen Werten (Zeitversatz 99 s, SWR 9,9, Akku 0 V) — kein Guard
soll auf erfundenen Vorgaben gruen sein. Faellt in dieses Fenster eine
Sendeentscheidung, sperrt der Guard also zu Recht.

Nur: ``TX_LOCKED`` loest sich nicht von selbst. Die Station bleibt stumm,
bis jemand von Hand entsperrt — und bei einer Box, die niemand ansieht,
heisst das: bis es jemandem auffaellt.

Am 2026-09-11 traf das einen von siebenundzwanzig Neustarts. Die Zeit
stand auf dem Startwert von 99 s, der time_guard sperrte, und die Station
funkte zwanzig Minuten lang nicht — bei einer Uhr, die laut chrony auf
150 Mikrosekunden genau lief.

Bewusst nur dieser eine Fall: Eine Sperre im laufenden Betrieb muss
haengen bleiben. Ein SWR-Runaway loest sich zwischen zwei Aussendungen von
allein auf (ohne Sendung faellt der gemessene Wert zurueck); eine
Automatik wuerde dann zwischen Sperre und Freigabe pendeln, statt den
Fehler sichtbar zu lassen.
"""

from __future__ import annotations

import inspect

from ft8_appliance.runtime import orchestrator as orch_mod
from ft8_appliance.statemachine.guards import GuardLimits, HardwareState
from ft8_appliance.statemachine.states import State


def _gut() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.02, swr=1.1, alc_pct=0,
        battery_v=12.6, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
        band_allowed_for_license=True, dial_on_configured_freq=True,
        rig_freq_hz=14_074_000, rig_link_age_s=1.0,
    )


def _schlecht() -> HardwareState:
    hw = _gut()
    hw.swr = 9.9
    return hw


class _SM:
    def __init__(self, zustand):
        self.state = zustand
        self.limits = GuardLimits(swr_max=2.0)
        self.entsperrt = False
        self.ctx = type("C", (), {"last_lock_reason": "time_guard: Test"})()

    def on_user_reset_lock(self):
        self.entsperrt = True
        self.state = State.IDLE


def _o(zustand, hw):
    class _O:
        state_machine = _SM(zustand)
        _hardware_state = hw
        _loese_startsperre = orch_mod.Orchestrator._loese_startsperre
    return _O()


def test_sperre_aus_der_startphase_wird_aufgehoben():
    o = _o(State.TX_LOCKED, _gut())

    o._loese_startsperre()

    assert o.state_machine.entsperrt is True
    assert o.state_machine.state is State.IDLE


def test_echter_grund_bleibt_gesperrt():
    """Steht das SWR wirklich bei 9,9, wird nicht entsperrt."""
    o = _o(State.TX_LOCKED, _schlecht())

    o._loese_startsperre()

    assert o.state_machine.entsperrt is False
    assert o.state_machine.state is State.TX_LOCKED


def test_ohne_sperre_passiert_nichts():
    o = _o(State.IDLE, _gut())

    o._loese_startsperre()

    assert o.state_machine.entsperrt is False


def test_nur_einmal_je_start():
    """Im laufenden Betrieb muss eine Sperre haengen bleiben — sonst
    pendelt ein SWR-Runaway zwischen Sperre und Freigabe."""
    q = inspect.getsource(orch_mod.Orchestrator._refresh_hardware_state)
    assert "_erste_hw_messung_offen" in q
    assert "self._erste_hw_messung_offen = False" in q


def test_haengt_an_der_ersten_echten_messung():
    """Nicht am Start selbst — vorher gibt es ja nichts zu pruefen."""
    q = inspect.getsource(orch_mod.Orchestrator._refresh_hardware_state)
    vor_zuweisung = q.split("self._hardware_state = HardwareState", 1)[0]
    assert "_loese_startsperre" not in vor_zuweisung
