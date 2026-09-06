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

from ft8_appliance.statemachine.guards import (
    GuardLimits,
    HardwareState,
    alc_guard,
    battery_guard,
)
from ft8_appliance.statemachine.machine import State, StateMachine
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


# ------------------------------------------------ ALC / Akku (Audit M1)
#
# Drei Guards konnten nie feuern, weil der Orchestrator ihre Eingangswerte
# hartkodiert auf "alles gut" setzte. Beim ALC ist das Scharfschalten nicht
# trivial: der ALC-Closed-Loop regelt bewusst auf alc_target_pct (15), eine
# Null-Toleranz-Schwelle wuerde also den bestimmungsgemaessen Betrieb
# sperren — und der Lock ist sticky.

def test_alc_guard_is_off_when_alc_max_is_zero() -> None:
    """Bestandsconfigs (inkl. der auf dem Pi) stehen auf alc_max=0. Wuerde
    das "Null-Toleranz" heissen, sperrte der erste Burst mit ALC 15 % den
    Sender — bei bestimmungsgemaesser Regelung."""
    res = alc_guard(HardwareState(alc_pct=35), GuardLimits(alc_max=0))
    assert res.ok is True


def test_alc_guard_fires_above_a_configured_cap() -> None:
    res = alc_guard(HardwareState(alc_pct=60), GuardLimits(alc_max=50))
    assert res.ok is False
    assert res.code == "guard.alc"


def test_alc_guard_passes_the_regulated_target_window() -> None:
    """alc_target_pct=15, Fenster 5..25 — das darf nie sperren."""
    for pct in (5, 15, 25, 40):
        assert alc_guard(HardwareState(alc_pct=pct), GuardLimits(alc_max=50)).ok


def test_default_alc_cap_sits_above_the_gain_watchdog() -> None:
    """Der harte Lock darf erst greifen, wenn der automatische
    Gain-Watchdog (alc_safety_threshold) es nicht mehr einfaengt."""
    from ft8_appliance.config.models import OperatingConfig
    op = OperatingConfig()
    assert op.alc_max > op.alc_safety_threshold
    assert op.alc_max > op.alc_warn


def test_battery_guard_stays_quiet_without_a_sensor() -> None:
    """IC-7300 liefert kein VOLTSEN → None → Netzbetrieb, kein Check."""
    assert battery_guard(HardwareState(battery_v=None), GuardLimits()).ok is True


def test_battery_guard_fires_on_a_flat_pack() -> None:
    # Limit explizit: seit 2026-09-06 ist der Default 0 = aus (der alte
    # feste Wert 12,0 V passte nicht zum 7,4-V-Akku des IC-705).
    res = battery_guard(HardwareState(battery_v=10.8), GuardLimits(battery_min_v=12.0))
    assert res.ok is False
    assert res.code == "guard.battery"
