"""Ein fehlender SWR-Wert ist "unbekannt", nicht "perfekt".

Der Orchestrator setzte fuer ``hw.swr`` den Ersatzwert 1,0 ein, wenn das
Rig nichts lieferte. Der Guard urteilte damit ueber eine Messung, die nie
stattgefunden hat — und 1,0 heisst gerade "bestens angepasst", also das
Gegenteil einer Sperre.

Das ist derselbe Fehlertyp, der beim Zeit-Guard (GPS-Fix statt Uhrzeit)
und beim ALC-Guard (hartkodierte 0) schon einmal zuschlug. Bei SWR faellt
er nicht auf, weil ein gut angepasster Aufbau tatsaechlich 1,0 misst: Der
Ersatzwert ist von einer echten Messung nicht zu unterscheiden.

Sperren soll der Guard bei ``None`` trotzdem nicht — ein Rig, das gar
nicht mehr antwortet, faengt der rig_link_guard eine Ebene hoeher ueber
das Alter des Snapshots ab. Uebrig bleibt der Fall, dass rigctld lebt und
nur dieses eine Level fehlt; dann ist Weiterfunken richtig, denn das Rig
hat seinen eigenen Schutz. Aber es soll auffallen.
"""

from __future__ import annotations

import inspect

from ft8_appliance.runtime import orchestrator as orch_mod
from ft8_appliance.statemachine.guards import GuardLimits, HardwareState, swr_guard


def _hw(**kw) -> HardwareState:
    vorgabe = dict(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )
    vorgabe.update(kw)
    return HardwareState(**vorgabe)


def test_kein_messwert_sperrt_nicht():
    """Sonst stuende die Station still, sobald ein Level einmal fehlt."""
    ergebnis = swr_guard(_hw(swr=None), GuardLimits(swr_max=2.0))

    assert ergebnis.ok is True


def test_kein_messwert_laesst_den_guard_nicht_abstuerzen():
    """Vor dem Fix waere hier ein Vergleich None > float gelaufen."""
    swr_guard(_hw(swr=None), GuardLimits(swr_max=2.0))


def test_hoher_wert_sperrt_weiterhin():
    ergebnis = swr_guard(_hw(swr=3.1), GuardLimits(swr_max=2.0))

    assert ergebnis.ok is False
    assert ergebnis.name == "swr_guard"


def test_guter_wert_laesst_durch():
    assert swr_guard(_hw(swr=1.1), GuardLimits(swr_max=2.0)).ok is True


def test_orchestrator_erfindet_keinen_ersatzwert():
    """Der Kern: 1,0 waere eine Messung, die nie stattgefunden hat."""
    q = inspect.getsource(orch_mod.Orchestrator._refresh_hardware_state)
    assert "swr=rig.swr," in q
    assert "else 1.0" not in q


def test_fehlender_wert_wird_gemeldet():
    """Ein Waechter, der nie anschlaegt, sieht aus wie einer, der
    funktioniert — deshalb muss das Ausbleiben ins Log."""
    q = inspect.getsource(orch_mod.Orchestrator._check_swr_warn)
    assert "_melde_fehlenden_swr" in q


def test_meldung_nur_waehrend_des_sendens():
    """Zwischen den Aussendungen ist ein fehlender Wert normal."""
    q = inspect.getsource(orch_mod.Orchestrator._melde_fehlenden_swr)
    assert "_tx_burst_active" in q


def test_meldung_kommt_nur_einmal():
    q = inspect.getsource(orch_mod.Orchestrator._melde_fehlenden_swr)
    assert "_swr_fehlt_gemeldet" in q
