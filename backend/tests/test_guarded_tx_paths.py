"""Audit 2026-07-30: zwei TX-Pfade liefen an den Guards vorbei.

Beide sind keine Randfaelle, sondern der Normalbetrieb:

* das RR73 am QSO-Ende (jedes abgeschlossene QSO),
* das Folge-CQ nach dem Grace-Fenster im auto_cq-Modus (jedes QSO im
  Dauerbetrieb).

Ein Guard, der im TX-Slot greift, aber diese beiden Aussendungen nicht
verhindert, schuetzt genau dann nicht, wenn es darauf ankommt — z.B. bei
SWR-Spike, verlorenem Zeitsync oder gesperrtem Band.
"""

from __future__ import annotations

from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import StateMachine, State
from ft8_appliance.statemachine.states import MachineContext, QsoContext


def _ok() -> HardwareState:
    """Defaults von HardwareState sind bewusst alle gruen."""
    return HardwareState()


def _blocked() -> HardwareState:
    """SWR-Spike — genau das Szenario, gegen das die Guards schuetzen
    sollen, und mit swr_max=2.0 eindeutig ueber der Grenze."""
    return HardwareState(swr=5.0)


def _sm(*, auto_cq: bool = False) -> StateMachine:
    return StateMachine(
        ctx=MachineContext(callsign="DK9XR", my_grid="JN58ch", auto_cq=auto_cq)
    )


def _with_qso(sm: StateMachine) -> StateMachine:
    sm.qso = QsoContext(
        their_call="W1AW", their_grid="FN31", band="20m",
        freq_offset_hz=1500, their_snr=-10, our_snr_received=-15,
    )
    return sm


def _kinds(sm: StateMachine) -> list[str]:
    return [a.kind for a in sm.drain_actions()]


# --------------------------------------------------------------- RR73 (H1)


def test_rr73_goes_out_when_guards_are_green() -> None:
    sm = _with_qso(_sm())
    sm._emit_log_qso(_ok())
    kinds = _kinds(sm)
    assert "TX_MESSAGE" in kinds
    assert "LOG_QSO" in kinds
    assert sm.state is State.QSO_GRACE


def test_rr73_is_suppressed_when_a_guard_blocks() -> None:
    sm = _with_qso(_sm())
    sm._emit_log_qso(_blocked())
    assert "TX_MESSAGE" not in _kinds(sm)


def test_qso_is_still_logged_when_a_guard_blocks() -> None:
    """Fuer die Gegenstation ist das QSO komplett. Es wegen eines lokalen
    Hardware-Problems nicht zu loggen waere ein echter QSO-Verlust."""
    sm = _with_qso(_sm())
    sm._emit_log_qso(_blocked())
    actions = sm.drain_actions()
    logged = [a for a in actions if a.kind == "LOG_QSO"]
    assert len(logged) == 1
    assert logged[0].payload["call"] == "W1AW"


def test_blocked_rr73_locks_tx_and_skips_grace() -> None:
    """Kein Grace-Fenster wenn gesperrt — sein einziger Zweck waere ein
    weiteres TX (das 73)."""
    sm = _with_qso(_sm())
    sm._emit_log_qso(_blocked())
    assert sm.state is State.TX_LOCKED
    assert "TX_LOCKED" in _kinds(sm)
    assert sm._grace_ticks_remaining == 0


# ------------------------------------------------------- Auto-CQ nach Grace (H2)


def test_auto_cq_after_grace_when_guards_are_green() -> None:
    sm = _sm(auto_cq=True)
    sm._exit_grace(_ok())
    assert sm.state is State.CQ_CALLING
    assert "TX_MESSAGE" in _kinds(sm)


def test_auto_cq_after_grace_is_blocked_by_guard() -> None:
    sm = _sm(auto_cq=True)
    sm._exit_grace(_blocked())
    assert "TX_MESSAGE" not in _kinds(sm)
    assert sm.state is State.TX_LOCKED


def test_grace_exit_without_auto_cq_needs_no_guard() -> None:
    """Ohne auto_cq sendet _exit_grace gar nichts — dann darf ein
    Guard-Fehler auch nicht in TX_LOCKED fuehren, sonst quittiert
    Sebastian Locks fuer Aussendungen, die es nie gab."""
    sm = _sm(auto_cq=False)
    sm._exit_grace(_blocked())
    assert sm.state is State.IDLE
    assert _kinds(sm) == []


def test_slot_tick_exiting_grace_respects_guards() -> None:
    """Der eigentliche Produktionspfad: das Grace-Fenster laeuft im
    Slot-Tick ab, nicht durch einen Direktaufruf."""
    sm = _sm(auto_cq=True)
    sm.state = State.QSO_GRACE
    sm._grace_partner_call = "W1AW"
    sm._grace_ticks_remaining = 1
    sm.on_slot_tick(_blocked())
    assert "TX_MESSAGE" not in _kinds(sm)
    assert sm.state is State.TX_LOCKED
