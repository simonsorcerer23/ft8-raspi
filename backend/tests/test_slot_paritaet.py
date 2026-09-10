"""Gegen welchen Slot die Paritaet geprueft werden muss.

Bis v0.89.0 pruefte der Picker die Sende-Paritaet einer Station gegen
``ctx.current_slot_parity`` — die Paritaet des Slots, dessen Decodes gerade
verarbeitet werden. Gesendet wird aber im *folgenden* Slot.

Die Folge war die Umkehrung der Absicht: Wer in Slot N sendet, hoert in
Slot N+1, und genau dort antworten wir — er waere das ideale Ziel. Statt
dessen fiel er heraus, waehrend Stationen, die wir zufaellig ausserhalb
ihrer Standard-Paritaet hoerten (typisch: sie stecken gerade in einem QSO),
als Kandidaten stehen blieben.

Der Effekt waechst mit der Laufzeit, weil ``op_slot_parity`` erst nach drei
Decodes einer Station greift und bei jedem Neustart leer beginnt.

Zur Ehrlichkeit: Aufgefallen ist die Sache bei der Suche nach dem Grund,
warum Vielrufer wie MI0JZZ (71 CQs am 2026-09-10) nie angerufen wurden.
Dieser Grund war ein anderer — im Auto-CQ-Betrieb pickt die Maschine gar
nicht. Die Umkehrung hier ist davon unabhaengig falsch und wird unten
Fall fuer Fall geprueft; ihre Wirkung entfaltet sie in den Hunting-Phasen.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import (
    StateMachine, _sendet_wenn_wir_senden, _tier_not_his_tx_slot, _unser_tx_slot,
)
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext, State


def _hw_ok() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )


def _cq(call: str, snr: int = -11) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to=None, grid="IO65",
        message=f"CQ {call} IO65", snr_db=snr, dt_s=0.3,
        freq_offset_hz=1500, band="20m",
    )


def _sm(current: str, gelernt: dict[str, str]) -> StateMachine:
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    sm.ctx.auto_answer = True
    sm.ctx.current_slot_parity = current
    sm.ctx.op_slot_parity = dict(gelernt)
    sm.state = State.IDLE
    return sm


# ------------------------------------------------------- die Grundrechnung
def test_unser_sende_slot_ist_die_gegenparitaet():
    ctx = MachineContext(callsign="DK9XR", my_grid="JN58")
    ctx.current_slot_parity = "even"
    assert _unser_tx_slot(ctx) == "odd"
    ctx.current_slot_parity = "odd"
    assert _unser_tx_slot(ctx) == "even"
    ctx.current_slot_parity = ""
    assert _unser_tx_slot(ctx) is None


# ----------------------------------------------------------- der Kernfall
def test_wer_gerade_sendet_ist_das_ideale_ziel():
    """Der Fall MI0JZZ: sendet in 'even', wir hoeren ihn in 'even'.

    Unsere Antwort geht in den 'odd'-Slot — genau dann hoert er.
    """
    sm = _sm(current="even", gelernt={"MI0JZZ": "even"})
    d = _cq("MI0JZZ")

    assert not _sendet_wenn_wir_senden(d, sm.ctx)
    assert _tier_not_his_tx_slot(d, sm.ctx) == 1, "darf nicht abgestraft werden"

    sm.on_decodes(_hw_ok(), [d])
    assert sm.state is State.QSO_RESPOND
    assert sm.qso is not None and sm.qso.their_call == "MI0JZZ"


def test_wer_in_unserem_sende_slot_sendet_wird_gemieden():
    """Gegenprobe: seine gelernte TX-Paritaet ist unser Sende-Slot."""
    sm = _sm(current="even", gelernt={"DL9BUSY": "odd"})
    d = _cq("DL9BUSY")

    assert _sendet_wenn_wir_senden(d, sm.ctx)
    assert _tier_not_his_tx_slot(d, sm.ctx) == 0

    sm.on_decodes(_hw_ok(), [d])
    assert sm.state is State.IDLE, "er saesse im eigenen Sendedurchgang"


def test_gilt_in_beide_richtungen():
    sm = _sm(current="odd", gelernt={"OH2XO": "odd"})
    assert not _sendet_wenn_wir_senden(_cq("OH2XO"), sm.ctx)

    sm = _sm(current="odd", gelernt={"OH2XO": "even"})
    assert _sendet_wenn_wir_senden(_cq("OH2XO"), sm.ctx)


# ------------------------------------------------------- kein Urteil ohne Wissen
def test_ungelernte_station_bleibt_kandidat():
    sm = _sm(current="even", gelernt={})
    d = _cq("NEU1ABC")

    assert not _sendet_wenn_wir_senden(d, sm.ctx)
    assert _tier_not_his_tx_slot(d, sm.ctx) == 1
    sm.on_decodes(_hw_ok(), [d])
    assert sm.state is State.QSO_RESPOND


def test_ohne_slot_information_kein_filter():
    sm = _sm(current="", gelernt={"MI0JZZ": "even"})
    assert not _sendet_wenn_wir_senden(_cq("MI0JZZ"), sm.ctx)
    assert _tier_not_his_tx_slot(_cq("MI0JZZ"), sm.ctx) == 1


def test_leerer_paritaets_eintrag_zaehlt_als_unbekannt():
    sm = _sm(current="even", gelernt={"MI0JZZ": ""})
    assert not _sendet_wenn_wir_senden(_cq("MI0JZZ"), sm.ctx)


# --------------------------------------------- das Gate wirkt im Picker selbst
def test_picker_bevorzugt_den_erreichbaren_von_zweien():
    """Beide gleich stark — nur die Paritaet unterscheidet sie."""
    sm = _sm(current="even", gelernt={"ERREICHBAR": "even", "BESETZT": "odd"})

    ziel = sm._pick_hunt_target([_cq("BESETZT"), _cq("ERREICHBAR")])

    assert ziel is not None and ziel.call_from == "ERREICHBAR"
