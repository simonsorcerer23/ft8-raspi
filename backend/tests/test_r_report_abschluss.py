"""Ein R-Report des Partners in QSO_RESPOND schliesst das QSO ab.

Der Fall: Wir rufen CQ, jemand ruft uns, wir geben ihm den Report (Tx2) und
stehen in QSO_RESPOND. Er bestaetigt mit einem R-Report (Tx3, z.B.
``DK9XR R7OV R-11``) — damit ist das QSO auf seiner Seite komplett, ihm fehlt
nur noch unser RR73 (Tx4).

Bis v0.84.5 fiel dieser Fall zwischen die Zustaende. ``_find_report_from_them``
schliesst R-Reports ausdruecklich aus, ``_find_answer_with_report_to_us``
ebenso — dort steht sogar der Kommentar "Must NOT be an R-report (those belong
to QSO_RESPOND state)". In QSO_RESPOND behandelte sie aber niemand. Die Box
wartete auf einen Report ohne R, der nach einem R-Report nie mehr kommt, und
lief nach 90 s in den Timeout ("went silent").

Betroffen war der Normalfall einer CQ-rufenden Station. Am 2026-09-09 sichtbar
geworden: 15 gesendete Reports, ein einziges RR73. R7OV wiederholte sein
``R-11`` sogar noch einmal, bevor er aufgab.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import (
    DecodedMsg, MachineContext, QsoContext, State,
)


def _hw_ok() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )


def _sm_in_respond(their_call: str = "R7OV") -> StateMachine:
    """Zustand wie nach unserem Report: wir haben Tx2 gesendet."""
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    sm.state = State.QSO_RESPOND
    sm.qso = QsoContext(
        their_call=their_call, their_grid="KN87", band="20m",
        freq_offset_hz=1500, their_snr=-12, their_snr_at_us=-14,
    )
    return sm


def _decode(message: str, call_from: str = "R7OV", call_to: str = "DK9XR") -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call_from, call_to=call_to, grid=None,
        message=message, snr_db=-14, dt_s=0.2, freq_offset_hz=1500, band="20m",
    )


def test_r_report_fuehrt_zu_rr73_und_log():
    """Der Fall R7OV vom 2026-09-09."""
    sm = _sm_in_respond()

    sm.on_decodes(_hw_ok(), [_decode("DK9XR R7OV R-11")])

    aktionen = [a.kind for a in sm._pending]
    assert "TX_MESSAGE" in aktionen, "es muss ein RR73 rausgehen"
    assert "LOG_QSO" in aktionen, "das QSO muss geloggt werden"
    tx = next(a for a in sm._pending if a.kind == "TX_MESSAGE")
    assert tx.payload["message"] == "R7OV DK9XR RR73"
    log = next(a for a in sm._pending if a.kind == "LOG_QSO")
    assert log.payload["call"] == "R7OV"
    assert log.payload["rst_rcvd"] == -11, "der Report aus dem R-Report gehoert ins Log"
    assert log.payload["rst_sent"] == -12
    assert sm.state is State.QSO_GRACE


def test_r_report_mit_plus_wert():
    sm = _sm_in_respond("EA7Z")
    sm.on_decodes(_hw_ok(), [_decode("DK9XR EA7Z R+03", call_from="EA7Z")])
    log = next(a for a in sm._pending if a.kind == "LOG_QSO")
    assert log.payload["rst_rcvd"] == 3


def test_report_ohne_r_geht_weiter_wie_bisher():
    """Der andere Pfad darf sich nicht aendern: Report ohne R → R-Report."""
    sm = _sm_in_respond()

    sm.on_decodes(_hw_ok(), [_decode("DK9XR R7OV -11")])

    assert sm.state is State.QSO_REPORT
    assert not any(a.kind == "LOG_QSO" for a in sm._pending), (
        "ein Report ohne R schliesst noch kein QSO ab"
    )
    tx = next(a for a in sm._pending if a.kind == "TX_MESSAGE")
    # Im R-Report bekommt der Partner SEINEN Rapport, gemessen bei uns
    # (their_snr_at_us = -14), nicht den, den er uns gegeben hat (-11).
    assert tx.payload["message"] == "R7OV DK9XR R-14", tx.payload["message"]


def test_r_report_einer_fremden_station_wird_ignoriert():
    """Nur der R-Report unseres Partners an uns zaehlt."""
    sm = _sm_in_respond()

    sm.on_decodes(_hw_ok(), [
        _decode("OK1ZJK R7OV R-11", call_to="OK1ZJK"),   # an jemand anderen
        _decode("DK9XR DL9XYZ R-08", call_from="DL9XYZ"),  # von jemand anderem
    ])

    assert not any(a.kind == "LOG_QSO" for a in sm._pending)


def test_r_report_bei_gesperrtem_sender_wird_trotzdem_geloggt():
    """Guard-Fehler darf das QSO nicht verschlucken.

    Fuer die Gegenstation ist es komplett — nur unser RR73 bleibt aus.
    Gleiche Regel wie im bestehenden Abschlusspfad (_emit_log_qso).
    """
    sm = _sm_in_respond()
    schlechtes_hw = HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=9.9, alc_pct=0,   # SWR-Wächter
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )

    sm.on_decodes(schlechtes_hw, [_decode("DK9XR R7OV R-11")])

    assert any(a.kind == "LOG_QSO" for a in sm._pending), "QSO trotzdem loggen"
    assert not any(
        a.kind == "TX_MESSAGE" and a.payload.get("message", "").endswith("RR73")
        for a in sm._pending
    ), "aber kein RR73 bei gesperrtem Sender"
