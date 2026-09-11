"""Wiederholt der Partner seinen Report, braucht er unseren R-Report.

Nach einem Timeout in QSO_REPORT steht die Station wieder in IDLE. Meldet
sich der Partner dann mit einem *einfachen* Report (``DK9XR EA5QS -17``,
also Tx3 ohne R), heisst das: Er hat unseren R-Report nie decodiert und
wartet weiter. Fuer ihn ist das QSO offen.

Bis 2026-09-11 fiel das durch: Der Nachklang suchte nur nach R-Reports
(``R-xx``) und nach Abschluessen. Gemessen ueber drei Tage: 16 Abbrueche mit
``report_never_closed``, und in fast allen sendete die Station danach weiter
an uns — KG4OJT und K3ZK je fuenfmal, EA5QS und OZ1KZX je dreimal.

Wichtig ist, was *nicht* passiert: Das QSO wird nicht geloggt. Unser Rapport
ist bei ihm nie angekommen, ein Logeintrag waere einseitig. Stattdessen wird
das QSO wieder aufgenommen und der R-Report erneut gesendet; den Abschluss
macht der normale Pfad.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext, QsoContext, State


def _hw_ok() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )


def _nachricht(call: str, text: str, snr: int = -12) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to="DK9XR", grid=None,
        message=text, snr_db=snr, dt_s=0.2, freq_offset_hz=1500, band="20m",
    )


def _sm_nach_timeout(call: str = "EA5QS") -> StateMachine:
    """Zustand nach report_never_closed: QSO im Nachklang, wir in IDLE."""
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    sm.ctx.auto_answer = True
    sm.state = State.QSO_REPORT
    sm.qso = QsoContext(their_call=call, their_grid="IM99", band="20m",
                        freq_offset_hz=1500, their_snr=-11, their_snr_at_us=-12)
    sm.qso.our_snr_received = -17
    sm.qso.stale_slots = 99
    sm.on_slot_tick(_hw_ok(), None)        # Timeout -> IDLE + Nachklang
    assert sm.state is State.IDLE
    assert call in sm.ctx.recent_qso_ctx
    sm._pending.clear()
    return sm


def _gesendet(sm: StateMachine) -> list[str]:
    return [a.payload["message"] for a in sm._pending if a.kind == "TX_MESSAGE"]


# --------------------------------------------------------------- der Kernfall
def test_wiederholter_report_loest_r_report_aus():
    """Der Fall EA5QS: dreimal 'DK9XR EA5QS -17' nach unserem Abbruch."""
    sm = _sm_nach_timeout("EA5QS")

    sm.on_decodes(_hw_ok(), [_nachricht("EA5QS", "DK9XR EA5QS -17")])

    assert sm.state is State.QSO_REPORT, "QSO muss wieder aufgenommen werden"
    assert any(m.startswith("EA5QS DK9XR R") for m in _gesendet(sm))


def test_es_wird_nicht_geloggt():
    """Unser Rapport kam bei ihm nie an — ein Logeintrag waere einseitig."""
    sm = _sm_nach_timeout("K3ZK")

    sm.on_decodes(_hw_ok(), [_nachricht("K3ZK", "DK9XR K3ZK -15")])

    assert not any(a.kind == "LOG_QSO" for a in sm._pending)


def test_sein_rapport_wird_uebernommen():
    sm = _sm_nach_timeout("OZ1KZX")

    sm.on_decodes(_hw_ok(), [_nachricht("OZ1KZX", "DK9XR OZ1KZX -11")])

    assert sm.qso is not None
    assert sm.qso.our_snr_received == -11


# ------------------------------------------------------------- Begrenzung
def test_hoechstens_zwei_wiederaufnahmen():
    """Wer uns dauerhaft nicht hoert, darf keine Schleife ausloesen."""
    sm = _sm_nach_timeout("KG4OJT")
    for _ in range(5):
        sm.on_decodes(_hw_ok(), [_nachricht("KG4OJT", "DK9XR KG4OJT -21")])
        # jedes Mal zurueck in den Nachklang-Zustand
        if sm.qso is not None:
            sm.qso.stale_slots = 99
            sm.on_slot_tick(_hw_ok(), None)

    assert sm.ctx.resume_zaehler["KG4OJT"] <= sm.RESUME_MAX == 2


# ------------------------------------------------------------- Abgrenzung
def test_r_report_bleibt_der_abschlusspfad():
    """Ein R-Report heisst: er hat unseren gehoert — dann wird geloggt."""
    sm = _sm_nach_timeout("RV6F")

    sm.on_decodes(_hw_ok(), [_nachricht("RV6F", "DK9XR RV6F R-11")])

    assert any(a.kind == "LOG_QSO" for a in sm._pending)


def test_abschluss_bleibt_abschluss():
    sm = _sm_nach_timeout("UT7UJ")

    sm.on_decodes(_hw_ok(), [_nachricht("UT7UJ", "DK9XR UT7UJ RR73")])

    assert any(a.kind == "LOG_QSO" for a in sm._pending)


def test_fremde_station_weckt_den_nachklang_nicht():
    """DL9ZZZ spricht uns direkt an — darauf antwortet die Station ohnehin
    (eingehender Anruf). Der Nachklang von EA5QS darf davon unberuehrt
    bleiben, und sein Wiederaufnahme-Zaehler darf nicht steigen."""
    sm = _sm_nach_timeout("EA5QS")

    sm.on_decodes(_hw_ok(), [_nachricht("DL9ZZZ", "DK9XR DL9ZZZ -17")])

    assert "EA5QS" in sm.ctx.recent_qso_ctx, "fremder Anruf raeumt den Nachklang nicht ab"
    assert "EA5QS" not in sm.ctx.resume_zaehler


def test_gesperrte_hardware_sendet_nicht():
    sm = _sm_nach_timeout("EA5QS")
    hw_gesperrt = HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=9.9, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )

    sm.on_decodes(hw_gesperrt, [_nachricht("EA5QS", "DK9XR EA5QS -17")])

    assert _gesendet(sm) == []
