"""Ein abgeschlossener Partner, der weiter wartet, bekommt sein RR73.

Wiederholt die Gegenstation nach unserem Abschluss ihren R-Report, hat sie
unser RR73 nicht decodiert: Fuer sie ist das QSO offen, bei uns steht es im
Log. Gemessen ueber sieben Tage: sechs von 131 QSOs (4,6 %) blieben so
einseitig — RV6F wiederholte seinen Report sechsmal, TF1FT viermal, jeweils
ueber mehr als eine Minute.

Der bestehende QSO_GRACE-Pfad deckt das nicht ab. Er wartet genau einen
Slot und reagiert nur auf ein wiederholtes *RR73* (``_find_closing``), nicht
auf einen wiederholten Report.

Begrenzt auf drei Wiederholungen je Partner: Wer uns gar nicht hoert, soll
keine Endlosschleife ausloesen.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext, State


def _hw_ok() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )


def _hw_gesperrt() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=9.9, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )


def _r_report(call: str, an: str = "DK9XR", snr: int = -12) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to=an, grid=None,
        message=f"{an} {call} R{snr:+03d}", snr_db=-11, dt_s=0.2,
        freq_offset_hz=1500, band="20m",
    )


def _sm_nach_abschluss(call: str = "RV6F", freq: int = 1234) -> StateMachine:
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    sm.ctx.auto_answer = True
    sm.state = State.IDLE
    sm.ctx.recent_logged[call] = (
        datetime.now(UTC).timestamp() + sm.RR73_NACHKLANG_S, freq, 0,
    )
    return sm


def _rr73_aktionen(sm: StateMachine) -> list:
    return [a for a in sm._pending
            if a.kind == "TX_MESSAGE" and "RR73" in a.payload.get("message", "")]


# ------------------------------------------------------------- der Kernfall
def test_wiederholter_report_loest_rr73_aus():
    """Der Fall RV6F: sechs Wiederholungen ins Leere."""
    sm = _sm_nach_abschluss("RV6F")

    sm.on_decodes(_hw_ok(), [_r_report("RV6F")])

    aktionen = _rr73_aktionen(sm)
    assert len(aktionen) == 1
    assert aktionen[0].payload["message"] == "RV6F DK9XR RR73"


def test_antwort_geht_auf_die_frequenz_des_qsos():
    """Nicht auf den CQ-Default — er hoert dort, wo das QSO lief."""
    sm = _sm_nach_abschluss("TF1FT", freq=2100)

    sm.on_decodes(_hw_ok(), [_r_report("TF1FT")])

    assert _rr73_aktionen(sm)[0].payload["freq_offset_hz"] == 2100


def test_hoechstens_drei_wiederholungen():
    """Wer uns nicht hoert, darf keine Endlosschleife ausloesen."""
    sm = _sm_nach_abschluss("RZ4AZ")

    for _ in range(6):
        sm.on_decodes(_hw_ok(), [_r_report("RZ4AZ")])

    assert len(_rr73_aktionen(sm)) == sm.RR73_NACHKLANG_MAX == 3


# --------------------------------------------------------------- Abgrenzung
def test_fremde_station_loest_nichts_aus():
    sm = _sm_nach_abschluss("RV6F")

    sm.on_decodes(_hw_ok(), [_r_report("DL9ZZZ")])

    assert _rr73_aktionen(sm) == []


def test_abgelaufener_eintrag_wird_entsorgt():
    sm = _sm_nach_abschluss("RV6F")
    ablauf, freq, n = sm.ctx.recent_logged["RV6F"]
    sm.ctx.recent_logged["RV6F"] = (datetime.now(UTC).timestamp() - 1, freq, n)

    sm.on_decodes(_hw_ok(), [_r_report("RV6F")])

    assert _rr73_aktionen(sm) == []
    assert "RV6F" not in sm.ctx.recent_logged


def test_laufendes_qso_hat_vorrang():
    """Mitten in einem anderen QSO wird nichts nachgeschickt."""
    from ft8_appliance.statemachine.states import QsoContext
    sm = _sm_nach_abschluss("RV6F")
    sm.state = State.QSO_RESPOND
    sm.qso = QsoContext(their_call="EA5QS", their_grid="IM99", band="20m",
                        freq_offset_hz=1500, their_snr=-10)

    sm.on_decodes(_hw_ok(), [_r_report("RV6F")])

    assert _rr73_aktionen(sm) == []


def test_gesperrte_hardware_sendet_nicht():
    sm = _sm_nach_abschluss("RV6F")

    sm.on_decodes(_hw_gesperrt(), [_r_report("RV6F")])

    assert _rr73_aktionen(sm) == []


def test_ein_normales_rr73_bleibt_unberuehrt():
    """Der bestehende Grace-Pfad fuer wiederholtes RR73 aendert sich nicht."""
    sm = _sm_nach_abschluss("RV6F")
    closing = DecodedMsg(
        ts=datetime.now(UTC), call_from="RV6F", call_to="DK9XR", grid=None,
        message="DK9XR RV6F RR73", snr_db=-11, dt_s=0.2,
        freq_offset_hz=1500, band="20m",
    )

    sm.on_decodes(_hw_ok(), [closing])

    assert _rr73_aktionen(sm) == [], "ein RR73 ist kein wartender Report"
