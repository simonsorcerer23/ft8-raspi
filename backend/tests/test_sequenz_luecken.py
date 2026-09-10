"""Die vier Sequenz-Loecher vom 2026-09-10.

Gemeinsamer Nenner: Eine Nachricht, die eine Verbindung *fortsetzt*, wurde
nur in genau einem Zustand erwartet. Traf sie einen Schritt zu frueh oder zu
spaet ein, fiel sie durch — und tarnte sich als "Gegenstation verstummt",
weil in Wahrheit wir geschwiegen haben.

Gefunden beim Abgleich der Zustandsmaschine gegen die vollstaendige
FT8-Sequenz, nachdem der R-Report-Fall (v0.85.0) dasselbe Muster zeigte.
Datenlage ueber sieben Tage: 16 Stationen schickten uns R-Reports ohne dass
je ein QSO daraus wurde, EA3GXK wiederholte 186-mal, CT1BFP 55-mal.

  A  R-Report in IDLE/CQ_CALLING  — verspaetete Fortsetzung nach Timeout
  B  Abschluss in QSO_RESPOND     — Partner ueberspringt den R-Report
  C  Abschluss in IDLE/CQ_CALLING — sein RR73 kommt nach unserem Timeout
  D  R-Report in QSO_REPORT       — er wiederholt, beide haben bestaetigt
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


def _sm() -> StateMachine:
    return StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))


def _decode(message: str, call_from: str, call_to: str = "DK9XR") -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call_from, call_to=call_to, grid=None,
        message=message, snr_db=-14, dt_s=0.2, freq_offset_hz=1500, band="20m",
    )


def _laufendes_qso(sm: StateMachine, call: str = "EA3GXK") -> None:
    sm.qso = QsoContext(
        their_call=call, their_grid="JN11", band="20m", freq_offset_hz=1500,
        their_snr=-13, their_snr_at_us=-15,
    )


# --------------------------------------------------------------- Loch B
def test_b_partner_schliesst_ohne_r_report_ab():
    """In QSO_RESPOND: er ueberspringt den R-Report und sendet RR73."""
    sm = _sm()
    sm.state = State.QSO_RESPOND
    _laufendes_qso(sm, "F4IVG")

    sm.on_decodes(_hw_ok(), [_decode("DK9XR F4IVG RR73", "F4IVG")])

    assert any(a.kind == "LOG_QSO" for a in sm._pending), "QSO muss geloggt werden"
    assert sm.state is State.QSO_GRACE


# --------------------------------------------------------------- Loch D
def test_d_partner_wiederholt_r_report_in_qso_report():
    """In QSO_REPORT: er hat unseren R-Report nicht gehoert und wiederholt seinen.

    Beide Seiten haben damit bestaetigt — einer muss abschliessen.
    """
    sm = _sm()
    sm.state = State.QSO_REPORT
    _laufendes_qso(sm, "CT1BFP")
    sm.qso.our_snr_received = -9

    sm.on_decodes(_hw_ok(), [_decode("DK9XR CT1BFP R-09", "CT1BFP")])

    log = next(a for a in sm._pending if a.kind == "LOG_QSO")
    assert log.payload["call"] == "CT1BFP"
    assert log.payload["rst_rcvd"] == -9
    assert sm.state is State.QSO_GRACE


def test_d_ohne_r_report_bleibt_der_alte_pfad():
    """Gegenprobe: ein Report ohne R fuehrt weiter zum Resend, nicht zum Log."""
    sm = _sm()
    sm.state = State.QSO_REPORT
    _laufendes_qso(sm, "CT1BFP")
    sm.qso.our_snr_received = -9

    sm.on_decodes(_hw_ok(), [_decode("DK9XR CT1BFP -09", "CT1BFP")])

    assert not any(a.kind == "LOG_QSO" for a in sm._pending)
    assert sm.state is State.QSO_REPORT


# ------------------------------------------------------------- Loch A / C
def test_a_r_report_nach_timeout_schliesst_qso_ab():
    """Der Fall EA3GXK: 186 Wiederholungen ins Leere.

    Timeout in QSO_RESPOND, wir stehen wieder in IDLE — dann meldet er sich
    doch noch mit seinem R-Report.
    """
    sm = _sm()
    sm.ctx.auto_answer = True
    sm.state = State.QSO_RESPOND
    _laufendes_qso(sm, "EA3GXK")
    sm.qso.stale_slots = 99
    sm.on_slot_tick(_hw_ok(), None)          # Timeout -> IDLE
    assert sm.state is State.IDLE
    assert "EA3GXK" in sm.ctx.recent_qso_ctx, "das QSO muss abrufbar bleiben"
    sm._pending.clear()

    sm.on_decodes(_hw_ok(), [_decode("DK9XR EA3GXK R-11", "EA3GXK")])

    log = next(a for a in sm._pending if a.kind == "LOG_QSO")
    assert log.payload["call"] == "EA3GXK"
    assert log.payload["rst_sent"] == -13, "unser gesendeter Rapport aus dem Nachklang"
    assert log.payload["rst_rcvd"] == -11, "sein Rapport aus dem R-Report"
    assert any(
        a.kind == "TX_MESSAGE" and a.payload["message"] == "EA3GXK DK9XR RR73"
        for a in sm._pending
    )
    assert "EA3GXK" not in sm.ctx.recent_qso_ctx, "danach aufgebraucht"


def test_c_abschluss_nach_timeout_wird_noch_geloggt():
    """Sein RR73 kommt erst nach unserem Timeout — das QSO steht in seinem Log."""
    sm = _sm()
    sm.state = State.QSO_REPORT
    _laufendes_qso(sm, "UA3PAB")
    sm.qso.our_snr_received = -7
    sm.qso.stale_slots = 99
    sm.on_slot_tick(_hw_ok(), None)
    assert sm.state is State.IDLE
    sm.state = State.CQ_CALLING          # wir rufen inzwischen wieder CQ
    sm.ctx.auto_cq = True
    sm._pending.clear()

    sm.on_decodes(_hw_ok(), [_decode("DK9XR UA3PAB RR73", "UA3PAB")])

    log = next(a for a in sm._pending if a.kind == "LOG_QSO")
    assert log.payload["call"] == "UA3PAB"
    assert log.payload["rst_rcvd"] == -7


def test_nachklang_laeuft_ab():
    """Nach der Frist wird nichts mehr wiederbelebt — dann waere es geraten."""
    sm = _sm()
    sm.ctx.auto_answer = True
    sm.state = State.QSO_RESPOND
    _laufendes_qso(sm, "VE1WT")
    sm.qso.stale_slots = 99
    sm.on_slot_tick(_hw_ok(), None)
    # Ablauf in die Vergangenheit schieben
    ablauf, daten = sm.ctx.recent_qso_ctx["VE1WT"]
    sm.ctx.recent_qso_ctx["VE1WT"] = (datetime.now(UTC).timestamp() - 1, daten)
    sm.state = State.IDLE
    sm._pending.clear()

    sm.on_decodes(_hw_ok(), [_decode("DK9XR VE1WT R-11", "VE1WT")])

    assert not any(a.kind == "LOG_QSO" for a in sm._pending)
    assert "VE1WT" not in sm.ctx.recent_qso_ctx, "abgelaufener Eintrag wird entsorgt"


def test_ohne_gesendeten_report_kein_nachklang():
    """Ohne eigenen Rapport gaebe es keinen gueltigen Logeintrag."""
    sm = _sm()
    sm.ctx.auto_answer = True
    sm.state = State.QSO_RESPOND
    sm.qso = QsoContext(
        their_call="OH3MB", their_grid="KP20", band="20m",
        freq_offset_hz=1500, their_snr=None,   # wir haben noch nichts gesendet
    )
    sm.qso.stale_slots = 99

    sm.on_slot_tick(_hw_ok(), None)

    assert "OH3MB" not in sm.ctx.recent_qso_ctx


def test_fremde_station_weckt_den_nachklang_nicht():
    sm = _sm()
    sm.ctx.auto_answer = True
    sm.state = State.QSO_RESPOND
    _laufendes_qso(sm, "EA3GXK")
    sm.qso.stale_slots = 99
    sm.on_slot_tick(_hw_ok(), None)
    sm._pending.clear()

    sm.on_decodes(_hw_ok(), [
        _decode("DK9XR DL9ZZZ R-11", "DL9ZZZ"),        # anderer Absender
        _decode("OK1ABC EA3GXK R-11", "EA3GXK", "OK1ABC"),  # an jemand anderen
    ])

    assert not any(a.kind == "LOG_QSO" for a in sm._pending)
    assert "EA3GXK" in sm.ctx.recent_qso_ctx, "bleibt fuer den echten Fall liegen"


def test_qso_closing_zustand_ist_entfernt():
    """Toter Zustand: definiert, nie erreicht, nie ausgewertet."""
    assert not hasattr(State, "QSO_CLOSING")
    assert [s.name for s in State] == [
        "IDLE", "CQ_CALLING", "QSO_RESPOND", "QSO_REPORT",
        "QSO_LOG", "QSO_GRACE", "TX_LOCKED",
    ]
