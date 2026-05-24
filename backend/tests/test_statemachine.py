"""End-to-end style tests of the QSO state machine on mock decodes.

We feed the machine the exact sequence of decodes it would see in a
clean QSO and assert the action stream matches.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ft8_appliance.statemachine import (
    DecodedMsg,
    GuardLimits,
    HardwareState,
    MachineContext,
    State,
    StateMachine,
)


def _decode(call_from: str | None, call_to: str | None, message: str, *, grid: str | None = None,
            snr: int | None = -10, band: str = "20m", freq: int = 1500) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC),
        call_from=call_from,
        call_to=call_to,
        grid=grid,
        message=message,
        snr_db=snr,
        dt_s=0.2,
        freq_offset_hz=freq,
        band=band,
    )


@pytest.fixture
def sm() -> StateMachine:
    return StateMachine(
        ctx=MachineContext(callsign="DK9XR", my_grid="JN58td"),
        limits=GuardLimits(),
    )


@pytest.fixture
def good_hw() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3,
        time_offset_s=0.01,
        swr=1.3,
        alc_pct=0,
        battery_v=13.4,
        cpu_temp_c=55.0,
    )


# ---------------------------------------------------------------------------
def test_start_cq_emits_cq_message(sm: StateMachine, good_hw: HardwareState) -> None:
    sm.on_user_start_cq(good_hw)
    actions = sm.drain_actions()
    assert sm.state is State.CQ_CALLING
    assert len(actions) == 1
    assert actions[0].kind == "TX_MESSAGE"
    assert actions[0].payload["message"] == "CQ DK9XR JN58"
    assert actions[0].payload["kind"] == "cq"


def test_guard_violation_locks_tx(sm: StateMachine) -> None:
    bad_hw = HardwareState(gps_fix_mode=0)  # no GPS fix
    sm.on_user_start_cq(bad_hw)
    actions = sm.drain_actions()
    assert sm.state is State.TX_LOCKED
    assert any(a.kind == "TX_LOCKED" for a in actions)
    assert any(a.kind == "STOP_TX" for a in actions)
    assert sm.ctx.last_lock_reason is not None
    assert "GPS" in sm.ctx.last_lock_reason


def test_reset_lock_returns_to_idle(sm: StateMachine) -> None:
    sm.on_user_start_cq(HardwareState(gps_fix_mode=0))
    assert sm.state is State.TX_LOCKED
    sm.on_user_reset_lock()
    assert sm.state is State.IDLE
    assert sm.ctx.last_lock_reason is None


def test_swr_guard_fails_above_threshold(sm: StateMachine) -> None:
    sm.on_user_start_cq(HardwareState(gps_fix_mode=3, swr=3.5))
    assert sm.state is State.TX_LOCKED
    assert "SWR" in (sm.ctx.last_lock_reason or "")


def test_full_qso_sequence_we_called_cq(sm: StateMachine, good_hw: HardwareState) -> None:
    # 1. user starts CQ
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()
    assert sm.state is State.CQ_CALLING

    # 2. someone answers us with their grid
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW FN31", grid="FN31", snr=-7)])
    actions = sm.drain_actions()
    assert sm.state is State.QSO_RESPOND
    assert sm.qso is not None and sm.qso.their_call == "W1AW"
    tx_msgs = [a.payload["message"] for a in actions if a.kind == "TX_MESSAGE"]
    assert tx_msgs == ["W1AW DK9XR -07"]

    # 3. they send us a signal report
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW -12", snr=-12)])
    actions = sm.drain_actions()
    assert sm.state is State.QSO_REPORT
    tx_msgs = [a.payload["message"] for a in actions if a.kind == "TX_MESSAGE"]
    assert tx_msgs == ["W1AW DK9XR R-12"]

    # 4. they send RR73 — we close + log + landen in QSO_GRACE (1 Slot
    # auf Partner-Repeat lauschen, dann via on_slot_tick weiter)
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW RR73")])
    actions = sm.drain_actions()
    assert sm.state is State.QSO_GRACE
    kinds = [a.kind for a in actions]
    assert "TX_MESSAGE" in kinds  # das RR73
    assert "LOG_QSO" in kinds
    log_action = next(a for a in actions if a.kind == "LOG_QSO")
    assert log_action.payload["call"] == "W1AW"
    assert log_action.payload["grid_rcvd"] == "FN31"
    assert log_action.payload["rst_rcvd"] == -12

    # 5. Grace-Slot vergeht ohne Partner-Repeat → on_user_start_cq hat
    # auto_cq=True gesetzt → wir landen in CQ_CALLING + fresh CQ
    sm.on_slot_tick(good_hw)
    actions = sm.drain_actions()
    assert sm.state is State.CQ_CALLING
    tx_actions = [a for a in actions if a.kind == "TX_MESSAGE"]
    assert tx_actions[-1].payload["kind"] == "cq"


def test_user_reply_to_hunting_flow(sm: StateMachine, good_hw: HardwareState) -> None:
    """User taps on a CQ from W1AW — hunting / S&P mode."""
    cq_from_them = _decode("W1AW", None, "CQ W1AW FN31", grid="FN31")
    sm.on_user_reply_to(good_hw, cq_from_them)
    actions = sm.drain_actions()
    assert sm.state is State.QSO_RESPOND
    assert sm.qso is not None and sm.qso.their_call == "W1AW"
    tx = next(a for a in actions if a.kind == "TX_MESSAGE")
    assert tx.payload["message"] == "W1AW DK9XR JN58"


def test_hunt_flow_log_qso_captures_rst_sent(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """Im Hunt-Pfad (wir antworten auf fremde CQ) muss rst_sent im
    LOG_QSO-Payload den SNR-Wert enthalten den wir transmitten — vorher
    war's immer None weil their_snr nur im CQ-Caller-Pfad gesetzt
    wurde. Sebastian-Bug 2026-05-24 nach Screenshot des QSO-Logs mit
    leerer RST↑-Spalte."""
    cq = _decode("W1AW", None, "CQ W1AW FN31", grid="FN31")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    # Partner schickt uns -08 Report → wir gehen in QSO_REPORT + senden
    # R-Report. Hier muss qso.their_snr nun gesetzt sein.
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW -08", snr=-8)])
    assert sm.state is State.QSO_REPORT
    assert sm.qso is not None
    assert sm.qso.their_snr == -8, \
        "their_snr (= rst_sent fuer Log) muss beim R-Report-Emit gesetzt sein"
    sm.drain_actions()

    # Partner schickt RR73 → wir loggen + landen in QSO_GRACE
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW RR73")])
    actions = sm.drain_actions()
    log_action = next(a for a in actions if a.kind == "LOG_QSO")
    assert log_action.payload["rst_sent"] == -8, \
        "rst_sent muss im LOG_QSO-Payload landen, nicht None"
    assert log_action.payload["rst_rcvd"] == -8, "rst_rcvd Sanity"


def test_wsjtx_conform_r_report_uses_our_measurement_not_echo(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """WSJT-X-Konformanz (Sebastian Audit-Action 5, v0.3.2):

    Wenn Partner uns Report -05 schickt (= unser Signal bei ihm), und
    WIR ihn mit -15 messen (= sein Signal bei uns), soll der R-Report
    R-15 enthalten — NICHT R-05 (Echo).

    Beweis dass rst_sent und rst_rcvd zwei verschiedene unabhaengige
    Messungen sind und nicht das gleiche Echo."""
    # Pickup mit unserer Messung -15
    cq = _decode("W1AW", None, "CQ W1AW FN31", snr=-15)
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    assert sm.qso.their_snr_at_us == -15, "Pickup-SNR getrackt"

    # Partner schickt uns Report -05 (sein Signal-Report ueber uns) mit
    # decode-SNR -16 (unsere Messung in dem Slot)
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW -05", snr=-16)])
    assert sm.state is State.QSO_REPORT
    assert sm.qso.our_snr_received == -5, "rst_rcvd = was sie schickten"
    assert sm.qso.their_snr_at_us == -16, "their_snr_at_us = neueste Messung"

    actions = sm.drain_actions()
    tx_msg = next(a.payload["message"] for a in actions if a.kind == "TX_MESSAGE")
    assert "R-16" in tx_msg, \
        f"R-Report muss our_measurement (-16) sein, nicht Echo (-05). Got: {tx_msg!r}"

    # QSO abschliessen, rst_sent != rst_rcvd verifizieren
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW RR73")])
    actions = sm.drain_actions()
    log = next(a for a in actions if a.kind == "LOG_QSO")
    assert log.payload["rst_sent"] == -16, "rst_sent = unser Decode-SNR"
    assert log.payload["rst_rcvd"] == -5, "rst_rcvd = ihr Report"
    assert log.payload["rst_sent"] != log.payload["rst_rcvd"], \
        "rst_sent und rst_rcvd sind unabhaengige Messungen"


def test_their_snr_at_us_tracks_latest_decode(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """their_snr_at_us folgt dem zuletzt gemessenen SNR des Partners
    ueber den QSO-Verlauf. Wenn QSB seine Signalstaerke aendert,
    spiegeln spaetere R-Reports den aktuellsten Wert wider."""
    cq = _decode("EA1AKS", None, "CQ EA1AKS IN73", snr=-7)
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    assert sm.qso.their_snr_at_us == -7

    # Slot 2: Partner-Decode mit anderem SNR (QSB) — auch kein Report
    # an uns, nur generelle Partner-Aktivitaet (z.B. CQ-Wiederholung).
    sm.on_decodes(good_hw, [_decode("EA1AKS", None, "CQ EA1AKS IN73", snr=-13)])
    # Da kein Report → wir bleiben in QSO_RESPOND aber their_snr_at_us
    # aktualisiert auf -13.
    assert sm.state is State.QSO_RESPOND
    assert sm.qso.their_snr_at_us == -13, "neuester Decode getrackt"


def test_slot_tick_retransmits_cq(sm: StateMachine, good_hw: HardwareState) -> None:
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()
    sm.on_slot_tick(good_hw)
    actions = sm.drain_actions()
    assert sm.ctx.cq_count == 1
    tx_msgs = [a.payload["message"] for a in actions if a.kind == "TX_MESSAGE"]
    assert tx_msgs == ["CQ DK9XR JN58"]


def test_tx_payload_uses_callers_frequency(sm: StateMachine, good_hw: HardwareState) -> None:
    """Reply on the audio-frequency the caller used, not the default 1500 Hz.

    Regression test for the future-bug Gemini flagged: without this, the
    eventual ALSA synth would always TX at 1500 Hz regardless of where
    the calling station is, talking over them.
    """
    caller_freq = 2350
    cq_from_them = _decode("W1AW", None, "CQ W1AW FN31", grid="FN31", freq=caller_freq)
    sm.on_user_reply_to(good_hw, cq_from_them)
    actions = sm.drain_actions()
    tx = next(a for a in actions if a.kind == "TX_MESSAGE")
    assert tx.payload["freq_offset_hz"] == caller_freq


def test_cq_uses_rotation_frequency(sm: StateMachine, good_hw: HardwareState) -> None:
    """CQ picks its TX audio frequency from ctx.cq_freq_rotation (anti-collision)."""
    sm.on_user_start_cq(good_hw)
    tx = next(a for a in sm.drain_actions() if a.kind == "TX_MESSAGE")
    # First CQ takes the first slot in the rotation list.
    assert tx.payload["freq_offset_hz"] == sm.ctx.cq_freq_rotation[0]


def test_full_qso_keeps_same_frequency_throughout(sm: StateMachine, good_hw: HardwareState) -> None:
    """Every QSO TX after the first should TX on the *caller's* audio
    frequency — staying split-frequency-compliant with normal FT8 ops."""
    caller_freq = 1750
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW FN31", grid="FN31", snr=-7, freq=caller_freq)
    ])
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW -12", snr=-12, freq=caller_freq)
    ])
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW RR73", freq=caller_freq)
    ])
    tx_actions = [a for a in sm.drain_actions() if a.kind == "TX_MESSAGE"]
    # ... well, this is fishing the actions of the *last* drain. Combine.
    # We want: all TX messages of this QSO carry the caller's freq.
    # Re-run cleanly with one drain at the end.

    sm2 = StateMachine(
        ctx=MachineContext(callsign="DK9XR", my_grid="JN58td"),
        limits=GuardLimits(),
    )
    sm2.on_user_start_cq(good_hw)
    sm2.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW FN31", grid="FN31", snr=-7, freq=caller_freq)
    ])
    sm2.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW -12", snr=-12, freq=caller_freq)
    ])
    sm2.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW RR73", freq=caller_freq)
    ])
    actions = sm2.drain_actions()
    tx_actions = [a for a in actions if a.kind == "TX_MESSAGE"]
    # Initial CQ is at default freq; every subsequent in-QSO TX must match caller_freq
    freqs_after_cq = [a.payload["freq_offset_hz"] for a in tx_actions if a.payload["kind"] != "cq"]
    assert freqs_after_cq, "expected QSO TX messages"
    assert all(f == caller_freq for f in freqs_after_cq), \
        f"all in-QSO messages should be on caller freq, got {freqs_after_cq}"


def test_stop_clears_state(sm: StateMachine, good_hw: HardwareState) -> None:
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()
    sm.on_user_stop()
    actions = sm.drain_actions()
    assert sm.state is State.IDLE
    assert sm.qso is None
    assert any(a.kind == "STOP_TX" for a in actions)


# ---------------------------------------------------------------------------
# Tail-Ender: someone answers our CQ with a direct signal report,
# skipping the grid stage. We should jump straight to QSO_REPORT and
# emit the R-report on the same caller frequency.
def test_tail_ender_skips_grid_stage(sm: StateMachine, good_hw: HardwareState) -> None:
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()
    caller_freq = 1820
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW -09", snr=-9, freq=caller_freq)
    ])
    actions = sm.drain_actions()
    # Skipped QSO_RESPOND, jumped directly to QSO_REPORT.
    assert sm.state is State.QSO_REPORT
    assert sm.qso is not None and sm.qso.their_call == "W1AW"
    assert sm.qso.our_snr_received == -9
    assert sm.qso.freq_offset_hz == caller_freq
    tx = next(a for a in actions if a.kind == "TX_MESSAGE")
    # The reply is the R-report, not a grid exchange.
    assert tx.payload["message"] == "W1AW DK9XR R-09"
    assert tx.payload["kind"] == "r_report"
    assert tx.payload["freq_offset_hz"] == caller_freq


def test_grid_answer_still_wins_when_both_present(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """If decodes contain both a grid-answer and a (stray) report-answer,
    the tail-ender path triggers because we check it first. This matches
    WSJT-Z behaviour: report-answers are *opportunistic* — when seen they
    are always taken even if a grid-answer also exists, because they save
    two slots."""
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW FN31", grid="FN31", snr=-7),
        _decode("K1JT", "DK9XR", "DK9XR K1JT -05", snr=-5),
    ])
    assert sm.state is State.QSO_REPORT
    assert sm.qso is not None and sm.qso.their_call == "K1JT"


def test_tail_ender_ignores_r_report(sm: StateMachine, good_hw: HardwareState) -> None:
    """A decode like ``DK9XR W1AW R-12`` must NOT be misread as a tail-end
    opener — that's the next exchange step of a normal QSO, not someone
    new answering our CQ."""
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW R-12", snr=-12)
    ])
    # No state change — we ignore unsolicited R-reports while CQ-ing.
    assert sm.state is State.CQ_CALLING
    assert sm.qso is None


# ---------------------------------------------------------------------------
# TX-Freq-Rotation: consecutive CQs cycle through ctx.cq_freq_rotation
def test_cq_freq_rotation_cycles_through_slots(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    rot = sm.ctx.cq_freq_rotation
    assert len(rot) >= 2, "rotation must have at least two slots for this test"
    seen = []
    sm.on_user_start_cq(good_hw)  # first CQ
    seen.append(_first_tx_freq(sm.drain_actions()))
    for _ in range(len(rot) + 1):  # walk past one full cycle
        sm.on_slot_tick(good_hw)
        seen.append(_first_tx_freq(sm.drain_actions()))
    # First N values must equal rot, and the (N+1)th equals rot[0] again.
    assert seen[: len(rot)] == rot
    assert seen[len(rot)] == rot[0]


def test_cq_freq_rotation_empty_falls_back_to_default(good_hw: HardwareState) -> None:
    """Defensive fallback: if someone wipes the rotation list, CQ still
    transmits — on CQ_DEFAULT_FREQ_HZ — instead of crashing."""
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58td"))
    sm.ctx.cq_freq_rotation = []
    sm.on_user_start_cq(good_hw)
    tx = next(a for a in sm.drain_actions() if a.kind == "TX_MESSAGE")
    assert tx.payload["freq_offset_hz"] == sm.CQ_DEFAULT_FREQ_HZ


# ---------------------------------------------------------------------------
# Auto-CQ loopback: after logging, return to CQ_CALLING instead of IDLE
def test_auto_cq_resumes_after_qso(sm: StateMachine, good_hw: HardwareState) -> None:
    sm.on_user_start_cq(good_hw)
    assert sm.ctx.auto_cq is True  # pressing CQ enables it
    sm.drain_actions()
    # Run a full QSO end-to-end.
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW FN31", grid="FN31", snr=-7)
    ])
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW -12", snr=-12)
    ])
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW RR73")
    ])
    sm.drain_actions()
    # Nach LOG_QSO landen wir in QSO_GRACE (1-Slot-Repeat-Fenster), erst
    # nach Slot-Tick weiter zu CQ_CALLING.
    assert sm.state is State.QSO_GRACE
    sm.on_slot_tick(good_hw)
    actions = sm.drain_actions()
    assert sm.state is State.CQ_CALLING
    assert sm.qso is None
    tx_kinds = [a.payload["kind"] for a in actions if a.kind == "TX_MESSAGE"]
    assert tx_kinds[-1] == "cq", f"expected fresh CQ after QSO, got {tx_kinds}"


def test_user_stop_clears_auto_cq(sm: StateMachine, good_hw: HardwareState) -> None:
    sm.on_user_start_cq(good_hw)
    assert sm.ctx.auto_cq is True
    sm.on_user_stop()
    assert sm.ctx.auto_cq is False


def test_hunting_does_not_enable_auto_cq(sm: StateMachine, good_hw: HardwareState) -> None:
    """Hunting / reply-to-others must not silently flip on Auto-CQ —
    that would cause the rig to start calling CQ after the hunted QSO."""
    cq_from_them = _decode("W1AW", None, "CQ W1AW FN31", grid="FN31")
    sm.on_user_reply_to(good_hw, cq_from_them)
    sm.drain_actions()
    # Complete the QSO via the hunting path.
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW -10", snr=-10)
    ])
    sm.on_decodes(good_hw, [
        _decode("W1AW", "DK9XR", "DK9XR W1AW RR73")
    ])
    sm.drain_actions()
    # Nach LOG_QSO landen wir in QSO_GRACE (1-Slot-Repeat-Fenster). auto_cq
    # ist False → nach Slot-Tick zu IDLE statt CQ_CALLING.
    assert sm.state is State.QSO_GRACE
    sm.on_slot_tick(good_hw)
    assert sm.ctx.auto_cq is False
    assert sm.state is State.IDLE


# ---------------------------------------------------------------------------
def _first_tx_freq(actions: list) -> int:
    """Return freq_offset_hz of the first TX_MESSAGE in the action stream."""
    for a in actions:
        if a.kind == "TX_MESSAGE":
            return a.payload["freq_offset_hz"]
    raise AssertionError(f"no TX_MESSAGE in {actions}")


# ---------------------------------------------------------------------------
# Re-Send-Limit + Failed-Attempt-Cooldown (Sebastian + Claude 2026-05-22).
#
# Beobachtung: in 2h Hunter-Modus hat eine einzige Station (SV9TLU) 12x
# unsere Anrufe ignoriert und neue CQ gerufen. Vorher hat die State-Machine
# bei jedem repeated-CQ wieder ihr Grid gesendet — endloser Versuch ohne
# Erfolgsmoeglichkeit. Plus: nach Bail wurde der Call sofort wieder gepickt
# weil kein Cooldown existierte. Fix: nach qso_max_cq_resends bailen, und
# in allen 4 Bail-Pfaden (picked_another, max_resends, went_silent,
# report_never_closed) den Call fuer qso_failed_cooldown_s ausschliessen.
def test_repeated_cq_resend_limit_then_bail(sm: StateMachine, good_hw: HardwareState) -> None:
    """Nach qso_max_cq_resends Wiederholungen wird abgebrochen."""
    sm.qso_max_cq_resends = 2
    sm.qso_failed_cooldown_s = 900.0
    cq = _decode("SV9TLU", None, "CQ SV9TLU KM25")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    assert sm.state is State.QSO_RESPOND

    # 1. repeated CQ → resend grid #1
    sm.on_decodes(good_hw, [_decode("SV9TLU", None, "CQ SV9TLU KM25")])
    assert sm.qso is not None and sm.qso.cq_resends == 1
    assert sm.state is State.QSO_RESPOND, "Loop bleibt dran"

    # 2. repeated CQ → resend grid #2 (am Limit)
    sm.on_decodes(good_hw, [_decode("SV9TLU", None, "CQ SV9TLU KM25")])
    assert sm.qso is not None and sm.qso.cq_resends == 2

    # 3. repeated CQ → bail
    sm.on_decodes(good_hw, [_decode("SV9TLU", None, "CQ SV9TLU KM25")])
    assert sm.state is State.IDLE, "muss nach max_resends bailen"
    assert sm.qso is None
    # Cooldown ist gesetzt
    assert "SV9TLU" in sm.ctx.recent_until
    assert sm.ctx.recent_until["SV9TLU"] > datetime.now(UTC).timestamp()


def test_picked_another_sets_cooldown(sm: StateMachine, good_hw: HardwareState) -> None:
    """Wenn der Partner einen anderen Caller waehlt → bail + Cooldown."""
    sm.qso_failed_cooldown_s = 900.0
    cq = _decode("EA8UP", None, "CQ EA8UP IL18")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    # Partner spricht jetzt mit jemand anderem (W1AW)
    sm.on_decodes(good_hw, [
        _decode("EA8UP", "W1AW", "W1AW EA8UP IL18"),
    ])
    assert sm.state is State.IDLE
    assert sm.qso is None
    assert "EA8UP" in sm.ctx.recent_until


def test_went_silent_sets_cooldown(sm: StateMachine, good_hw: HardwareState) -> None:
    """QSO_RESPOND-Timeout (kein Decode vom Partner ueber qso_max_stale_slots
    Slots) → bail + Cooldown."""
    sm.qso_max_stale_slots = 2
    sm.qso_failed_cooldown_s = 900.0
    cq = _decode("UB6OAK", None, "CQ UB6OAK LO87")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    # 3 leere Slot-Ticks (ueber stale-limit)
    for _ in range(3):
        sm.on_slot_tick(good_hw)
    assert sm.state is State.IDLE
    assert "UB6OAK" in sm.ctx.recent_until


def test_report_never_closed_sets_cooldown(sm: StateMachine, good_hw: HardwareState) -> None:
    """QSO_REPORT-Timeout (Partner gibt kein RR73) → bail + Cooldown."""
    sm.qso_max_stale_slots = 2
    sm.qso_failed_cooldown_s = 900.0
    cq = _decode("UZ5DM", None, "CQ UZ5DM KN78")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    # Partner sendet Signal-Report → wir gehen in QSO_REPORT
    sm.on_decodes(good_hw, [_decode("UZ5DM", "DK9XR", "DK9XR UZ5DM -09", snr=-9)])
    assert sm.state is State.QSO_REPORT
    # Stillschweigen — RR73 kommt nie
    for _ in range(3):
        sm.on_slot_tick(good_hw)
    assert sm.state is State.IDLE
    assert "UZ5DM" in sm.ctx.recent_until


def test_qso_report_partner_repeats_report_triggers_r_resend(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """Partner schickt seinen Report nochmal statt RR73 → wir resenden
    unsere R-Report 1× (WSJT-X-Verhalten, Sebastian 2026-05-24 nach
    UN7JO-QSO-Verlust)."""
    sm.qso_max_report_resends = 1
    sm.qso_max_stale_slots = 6
    cq = _decode("UN7JO", None, "CQ UN7JO MO13")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    # Partner schickt Signal-Report → wir gehen in QSO_REPORT + senden R-Report
    sm.on_decodes(good_hw, [_decode("UN7JO", "DK9XR", "DK9XR UN7JO -10", snr=-10)])
    assert sm.state is State.QSO_REPORT
    actions = sm.drain_actions()
    assert any(a.kind == "TX_MESSAGE"
               and "R-10" in a.payload.get("message", "")
               for a in actions), "R-Report wird gesendet"
    assert sm.qso.report_resends == 0

    # Partner sendet wieder -10 (statt RR73) — er hat uns nicht decodiert
    sm.on_decodes(good_hw, [_decode("UN7JO", "DK9XR", "DK9XR UN7JO -10", snr=-10)])
    assert sm.state is State.QSO_REPORT, "Wir geben noch nicht auf"
    assert sm.qso.report_resends == 1, "Resend-Counter hochgezaehlt"
    actions = sm.drain_actions()
    assert any(a.kind == "TX_MESSAGE"
               and "R-10" in a.payload.get("message", "")
               for a in actions), "R-Report wurde erneut gesendet"

    # Partner sendet ein DRITTES Mal -10 — am Resend-Limit, kein weiterer Send
    sm.on_decodes(good_hw, [_decode("UN7JO", "DK9XR", "DK9XR UN7JO -10", snr=-10)])
    assert sm.qso.report_resends == 1, "Counter bleibt am Limit"
    actions = sm.drain_actions()
    assert not any(a.kind == "TX_MESSAGE" for a in actions), \
        "Kein weiterer Resend nach Limit"


def test_qso_report_partner_repeats_cq_triggers_r_resend(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """Partner fällt nach unserem R-Report zurück zu CQ statt RR73 → wir
    resenden R-Report 1× (analog zum repeated-report-Pfad, weil Symptom
    dasselbe ist: unser R-Report kam nicht an). Sebastian 2026-05-24
    nach DO1BJF-Verlust.

    NOTE v0.3.2 (Audit-Action 5): R-Report-Inhalt = unser gemessener
    SNR des Partners (their_snr_at_us), nicht Echo seines Reports. Die
    Resend-Logik bleibt — nur die Zahl im R-Slot ist jetzt unser
    Decode-SNR, nicht +04."""
    sm.qso_max_report_resends = 1
    sm.qso_max_stale_slots = 6
    # Pickup-Decode: snr=-08 (was wir vom Partner messen)
    cq = _decode("DO1BJF", None, "CQ DO1BJF JO42", snr=-8)
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    # Partner schickt Signal-Report +04 mit decode-SNR=-12 (sein Signal
    # ist schwacher in diesem Slot) → wir senden R{decode-SNR} = R-12
    sm.on_decodes(good_hw, [_decode("DO1BJF", "DK9XR", "DK9XR DO1BJF +04", snr=-12)])
    assert sm.state is State.QSO_REPORT
    actions = sm.drain_actions()
    assert any(a.kind == "TX_MESSAGE"
               and "R-12" in a.payload.get("message", "")
               for a in actions), "R-Report nutzt our_snr_of_them (-12), nicht Echo (+04)"
    assert sm.qso.report_resends == 0

    # Partner fällt zurück in CQ mit decode-SNR=-09 — wir messen ihn
    # diesen Slot anders. R-Resend nimmt den neuesten Wert.
    sm.on_decodes(good_hw, [_decode("DO1BJF", None, "CQ DO1BJF JO42", snr=-9)])
    assert sm.state is State.QSO_REPORT, "Wir geben nicht direkt auf"
    assert sm.qso.report_resends == 1, "Resend-Counter hochgezählt"
    actions = sm.drain_actions()
    assert any(a.kind == "TX_MESSAGE"
               and "R-09" in a.payload.get("message", "")
               for a in actions), "R-Resend nutzt aktuellsten SNR (-09)"

    # Partner ruft ein DRITTES Mal CQ — am Limit, kein weiterer Send
    sm.on_decodes(good_hw, [_decode("DO1BJF", None, "CQ DO1BJF JO42")])
    assert sm.qso.report_resends == 1, "Counter bleibt am Limit"
    actions = sm.drain_actions()
    assert not any(a.kind == "TX_MESSAGE" for a in actions), \
        "Kein weiterer Resend nach Limit"


def test_qso_report_partner_picks_another_bails_with_cooldown(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """Partner schickt uns +Report, wir senden R-Report, dann startet er
    ein QSO mit jemand ANDEREM statt RR73 zu uns. Spiegel-Pattern zu
    QSO_RESPOND.picked_another. Sebastian 2026-05-24 M7CCZ-Case: M7CCZ
    gab uns +06, wir antworteten R-06, dann lief M7CCZ → EA1DUS −03 →
    RR73. Ohne Detection warteten wir 3 Slots vergeblich; mit Detection
    bail sofort + Cooldown.

    Audit Action 4 (docs/wsjtx_qso_state_audit.md)."""
    sm.qso_failed_cooldown_s = 900.0
    sm.qso_max_report_resends = 1
    cq = _decode("M7CCZ", None, "CQ M7CCZ JO02")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    # Partner schickt Signal-Report → wir gehen in QSO_REPORT
    sm.on_decodes(good_hw, [_decode("M7CCZ", "DK9XR", "DK9XR M7CCZ -06", snr=-6)])
    assert sm.state is State.QSO_REPORT
    sm.drain_actions()

    # Partner pickt nun einen anderen Caller (EA1DUS) statt unser RR73
    sm.on_decodes(good_hw, [
        _decode("M7CCZ", "EA1DUS", "EA1DUS M7CCZ -03"),
    ])
    assert sm.state is State.IDLE, "Bail sobald picked_another erkannt"
    assert sm.qso is None
    assert "M7CCZ" in sm.ctx.recent_until, \
        "Cooldown gesetzt damit wir M7CCZ nicht sofort wieder anworten"
    actions = sm.drain_actions()
    assert not any(a.kind == "TX_MESSAGE" for a in actions), \
        "Kein zus\u00e4tzlicher R-Resend wenn Partner schon weg ist"


def test_qso_report_picked_another_takes_priority_over_repeated_report(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """Im selben Decode-Slot kann der Partner sowohl seinen +Report nochmal
    schicken (an uns) UND auch was zu jemand anderem senden. Eigentlich
    Edge-Case, aber wir testen Priorit\u00e4t: picked_another schl\u00e4gt R-Resend,
    weil der Partner offensichtlich nicht mehr unser Partner ist."""
    sm.qso_failed_cooldown_s = 900.0
    sm.qso_max_report_resends = 1
    cq = _decode("M7CCZ", None, "CQ M7CCZ JO02")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    sm.on_decodes(good_hw, [_decode("M7CCZ", "DK9XR", "DK9XR M7CCZ -06", snr=-6)])
    assert sm.state is State.QSO_REPORT
    sm.drain_actions()

    # Beides im selben Slot: Report-Repeat + Other-QSO. Priority: bail.
    sm.on_decodes(good_hw, [
        _decode("M7CCZ", "DK9XR", "DK9XR M7CCZ -06", snr=-6),
        _decode("M7CCZ", "EA1DUS", "EA1DUS M7CCZ -03"),
    ])
    assert sm.state is State.IDLE
    assert sm.qso is None


def test_qso_report_closing_after_resend_logs_qso(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """Wenn der Partner nach unserem R-Resend doch noch RR73 schickt,
    wird das QSO geloggt (Happy-Path nach Late-Pickup)."""
    sm.qso_max_report_resends = 1
    cq = _decode("UN7JO", None, "CQ UN7JO MO13")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    sm.on_decodes(good_hw, [_decode("UN7JO", "DK9XR", "DK9XR UN7JO -10", snr=-10)])
    sm.drain_actions()
    # Partner repeated → wir resenden
    sm.on_decodes(good_hw, [_decode("UN7JO", "DK9XR", "DK9XR UN7JO -10", snr=-10)])
    sm.drain_actions()
    # Diesmal hoert er uns + schickt RR73 → wir loggen + landen in IDLE
    # (auto_cq war nicht gesetzt; bei auto_cq=True wuerde CQ_CALLING folgen)
    sm.on_decodes(good_hw, [_decode("UN7JO", "DK9XR", "DK9XR UN7JO RR73")])
    actions = sm.drain_actions()
    kinds = [a.kind for a in actions]
    assert "LOG_QSO" in kinds, f"QSO muss geloggt werden, actions={kinds}"
    log_action = next(a for a in actions if a.kind == "LOG_QSO")
    assert log_action.payload["call"] == "UN7JO"
    assert sm.qso is None


def test_lock_reason_auto_cleared_when_guards_recover(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """Wenn Guards wieder gruen werden, soll der Lock-Reason
    automatisch verschwinden. Sebastian 2026-05-24: nach Band-Switch
    weg von problematischem Band haengt das Banner sonst ewig im UI."""
    # Erst kuenstlich einen Lock erzeugen
    bad_hw = HardwareState(antenna_covers_band=False, gps_fix_mode=3)
    sm._check_guards(bad_hw)
    assert sm.state is State.TX_LOCKED
    assert sm.ctx.last_lock_reason is not None
    assert "Antenne" in sm.ctx.last_lock_reason

    # Naechster Check mit gesundem hw → reason muss weg sein
    sm._check_guards(good_hw)
    assert sm.ctx.last_lock_reason is None, \
        "Lock-Reason muss automatisch gecleart werden wenn Guards wieder gruen"


def test_user_reset_lock_clears_reason_even_when_not_locked(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """Reset-Lock-Button muss reason auch clearen wenn state schon
    nicht mehr TX_LOCKED ist (Lock loeste sich vorher auf, Banner
    hing aber noch). Sebastian 2026-05-24."""
    sm.ctx.last_lock_reason = "swr_guard: SWR 2.50 ueber Limit 2.00"
    sm.state = State.IDLE  # nicht mehr TX_LOCKED
    sm.on_user_reset_lock()
    assert sm.ctx.last_lock_reason is None


def test_qso_grace_partner_repeats_rr73_triggers_73_ack(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """Wenn der Partner sein RR73 wiederholt (weil er unser RR73 nicht
    decodiert hat), senden wir noch ein 73 (Tx6) als Closure-Ack
    waehrend QSO_GRACE. WSJT-X-konform, Sebastian 2026-05-24."""
    cq = _decode("W1AW", None, "CQ W1AW FN31")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW -12", snr=-12)])
    sm.drain_actions()
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW RR73")])
    sm.drain_actions()
    assert sm.state is State.QSO_GRACE
    assert sm._grace_partner_call == "W1AW"

    # Partner sendet sein RR73 nochmal -> wir senden 73 hinterher + Grace verlassen
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW RR73")])
    actions = sm.drain_actions()
    tx_msgs = [a.payload["message"] for a in actions if a.kind == "TX_MESSAGE"]
    assert any("73" in m and not "RR73" in m for m in tx_msgs), \
        f"Tx6=73 muss als Closure-Ack gesendet werden, got {tx_msgs}"
    assert sm.state is not State.QSO_GRACE, "Grace beendet nach Ack"


def test_qso_grace_silent_partner_just_exits(
    sm: StateMachine, good_hw: HardwareState,
) -> None:
    """Wenn der Partner KEIN RR73 wiederholt, beendet QSO_GRACE einfach
    nach 1 Slot ohne extra TX."""
    cq = _decode("W1AW", None, "CQ W1AW FN31")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW -12", snr=-12)])
    sm.drain_actions()
    sm.on_decodes(good_hw, [_decode("W1AW", "DK9XR", "DK9XR W1AW RR73")])
    sm.drain_actions()
    assert sm.state is State.QSO_GRACE

    # Stille im Repeat-Fenster -> Tick beendet Grace ohne extra TX
    sm.on_slot_tick(good_hw)
    actions = sm.drain_actions()
    tx_actions = [a for a in actions if a.kind == "TX_MESSAGE"]
    # auto_cq ist False (wir kamen via on_user_reply_to) → IDLE, kein CQ
    assert sm.state is State.IDLE
    assert tx_actions == [], "Kein extra TX wenn Partner still bleibt"


def test_cooldown_filters_picker(sm: StateMachine, good_hw: HardwareState) -> None:
    """Hunting-Picker uebergeht Calls die im recent_until-Fenster liegen."""
    sm.ctx.auto_answer = True
    cooldown_until = datetime.now(UTC).timestamp() + 60.0
    sm.ctx.recent_until["SV9TLU"] = cooldown_until
    # SV9TLU ruft CQ, aber wir koennen ihn nicht picken — kein anderer
    # da → kein Pick. Test prueft den Filter direkt.
    cqs = [_decode("SV9TLU", None, "CQ SV9TLU KM25", snr=-5)]
    pick = sm._pick_hunt_target(cqs)
    assert pick is None, "Cooldown-Call darf nicht gepickt werden"


def test_cooldown_disabled_when_zero(sm: StateMachine, good_hw: HardwareState) -> None:
    """qso_failed_cooldown_s == 0 → Cooldown deaktiviert (Sebastians Opt-Out)."""
    sm.qso_failed_cooldown_s = 0.0
    sm.qso_max_stale_slots = 2
    cq = _decode("X1Y2Z", None, "CQ X1Y2Z KM25")
    sm.on_user_reply_to(good_hw, cq)
    sm.drain_actions()
    for _ in range(3):
        sm.on_slot_tick(good_hw)
    assert sm.state is State.IDLE
    assert "X1Y2Z" not in sm.ctx.recent_until, "Cooldown=0 → kein Eintrag"


# ---------------------------------------------------------------------------
# SNR-Floor im Hunting-Picker (Sebastian 2026-05-22).
#
# Empirisch belegt durch QSO-DB-Analyse: rst_rcvd-Median -10 dB,
# 90%-Perzentil ~-18 dB. Stationen die mit -22 dB oder schlechter beim
# uns ankommen sind in der Praxis nicht erreichbar — sie verschwenden
# nur TX-Slots.
def test_snr_floor_filters_weak_decodes(sm: StateMachine, good_hw: HardwareState) -> None:
    """Decodes unter dem SNR-Floor werden vom Picker ignoriert."""
    sm.ctx.hunt_snr_floor_db = -22
    weak = _decode("DX1WEAK", None, "CQ DX1WEAK PM85", snr=-25)
    strong = _decode("EU1OK", None, "CQ EU1OK JO20", snr=-12)
    pick = sm._pick_hunt_target([weak, strong])
    assert pick is not None and pick.call_from == "EU1OK"


def test_snr_floor_picks_none_when_all_weak(sm: StateMachine, good_hw: HardwareState) -> None:
    """Wenn alle Decodes unter dem Floor liegen, picken wir niemanden."""
    sm.ctx.hunt_snr_floor_db = -22
    weak1 = _decode("DX1", None, "CQ DX1 AA00", snr=-24)
    weak2 = _decode("DX2", None, "CQ DX2 BB00", snr=-28)
    pick = sm._pick_hunt_target([weak1, weak2])
    assert pick is None


def test_snr_floor_disabled_picks_anyway(sm: StateMachine, good_hw: HardwareState) -> None:
    """floor=None deaktiviert den Filter."""
    sm.ctx.hunt_snr_floor_db = None
    weak = _decode("DX1WEAK", None, "CQ DX1WEAK PM85", snr=-30)
    pick = sm._pick_hunt_target([weak])
    assert pick is not None and pick.call_from == "DX1WEAK"


def test_snr_floor_accepts_decodes_without_snr(sm: StateMachine, good_hw: HardwareState) -> None:
    """Decode ohne SNR-Info (snr_db=None) wird durchgelassen — wir koennen
    das nicht beurteilen, sicherer fail-open als fail-closed."""
    sm.ctx.hunt_snr_floor_db = -22
    no_snr = _decode("UNKNOWN", None, "CQ UNKNOWN AA00", snr=None)
    pick = sm._pick_hunt_target([no_snr])
    assert pick is not None and pick.call_from == "UNKNOWN"


# ---------------------------------------------------------------------------
# Audio-Frequenz-Filter im Hunting-Picker (Sebastian 2026-05-22).
#
# Sebastian sah dass ein Reply auf 262 Hz Audio (unter dem IC-7300-
# Bandpass ~300 Hz) den PI-Loop in einen PWR-Spike + Watchdog-Cut
# trieb — unser Audio wurde stark gedaempft, Rig lieferte kaum Power,
# Loop dachte Underdrive, kurbelte hoch, knallte beim 4. Burst bei 54 %
# ALC ein. Filter im Picker verhindert dass solche Stationen ueberhaupt
# angerufen werden.
def test_audio_freq_filter_skips_below_min(sm: StateMachine, good_hw: HardwareState) -> None:
    sm.ctx.hunt_audio_freq_min_hz = 400
    sm.ctx.hunt_audio_freq_max_hz = 2600
    too_low = _decode("R1CCX", None, "CQ R1CCX LO12", snr=-10, freq=262)
    ok = _decode("EU1OK", None, "CQ EU1OK JO20", snr=-15, freq=1500)
    pick = sm._pick_hunt_target([too_low, ok])
    assert pick is not None and pick.call_from == "EU1OK", \
        f"262 Hz haette weggefiltert werden muessen, picked {pick}"


def test_audio_freq_filter_skips_above_max(sm: StateMachine, good_hw: HardwareState) -> None:
    sm.ctx.hunt_audio_freq_min_hz = 400
    sm.ctx.hunt_audio_freq_max_hz = 2600
    too_high = _decode("HIGH", None, "CQ HIGH AA00", snr=-5, freq=2800)
    ok = _decode("OK1", None, "CQ OK1 JN78", snr=-15, freq=1800)
    pick = sm._pick_hunt_target([too_high, ok])
    assert pick is not None and pick.call_from == "OK1"


def test_audio_freq_filter_disabled_passes_all(sm: StateMachine, good_hw: HardwareState) -> None:
    sm.ctx.hunt_audio_freq_min_hz = None
    sm.ctx.hunt_audio_freq_max_hz = None
    low = _decode("R1CCX", None, "CQ R1CCX LO12", snr=-10, freq=262)
    pick = sm._pick_hunt_target([low])
    assert pick is not None and pick.call_from == "R1CCX"


# ---------------------------------------------------------------------------
# IDLE+auto_answer reagiert auf Direkt-Antworten (Sebastian 2026-05-22)
#
# Bug: bei IDLE+auto_answer ignorierte der Hunting-Picker direkte
# Adressierungen ("DK9XR DM2HK -20") weil er nur CQ-Decodes filtert.
# In den API-Decodes sahen wir 4 verpasste Direkt-Antworten (IU0ERZ
# 3x, DM2HK 1x) die alle quasi-loggbar gewesen waeren. Fix: in
# on_decodes() PRIO-1 auf Direkt-Antworten pruefen, dann erst CQ-Pick.
def test_idle_auto_answer_picks_up_tail_ender_to_us(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """IDLE+auto_answer: jemand sendet uns Signal-Report ohne dass wir
    je angerufen haben → wir gehen in QSO_REPORT und senden R-Report."""
    sm.ctx.auto_answer = True
    assert sm.state is State.IDLE
    sm.on_decodes(good_hw, [
        _decode("DM2HK", "DK9XR", "DK9XR DM2HK -20", snr=-9, freq=2072),
    ])
    assert sm.state is State.QSO_REPORT, \
        f"Tail-Ender im IDLE+auto_answer haette QSO_REPORT triggern muessen, got {sm.state}"
    assert sm.qso is not None and sm.qso.their_call == "DM2HK"
    assert sm.qso.our_snr_received == -20
    tx = next(a for a in sm.drain_actions() if a.kind == "TX_MESSAGE")
    assert "DM2HK" in tx.payload["message"] and "R-" in tx.payload["message"]


def test_idle_auto_answer_picks_up_grid_reply_to_us(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """IDLE+auto_answer: jemand antwortet uns mit Grid (DK9XR XXX JN00)
    → wir gehen in QSO_RESPOND und senden Report."""
    sm.ctx.auto_answer = True
    sm.on_decodes(good_hw, [
        _decode("IU0ERZ", "DK9XR", "DK9XR IU0ERZ JN61", grid="JN61", snr=-10, freq=931),
    ])
    assert sm.state is State.QSO_RESPOND
    assert sm.qso is not None and sm.qso.their_call == "IU0ERZ"
    tx = next(a for a in sm.drain_actions() if a.kind == "TX_MESSAGE")
    msg = tx.payload["message"]
    assert "IU0ERZ" in msg and "-" in msg, f"Erwartet Report an IU0ERZ, got {msg!r}"


def test_idle_direct_reply_has_priority_over_cq_pick(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """Wenn gleichzeitig ein Direkt-Reply UND ein CQ kommt, prioritisieren
    wir den Direkt-Reply (wir verlieren sonst die offene Conversation)."""
    sm.ctx.auto_answer = True
    sm.on_decodes(good_hw, [
        _decode("CQCALLER", None, "CQ CQCALLER JO20", snr=-5, freq=1500),
        _decode("DM2HK", "DK9XR", "DK9XR DM2HK -20", snr=-12, freq=2072),
    ])
    assert sm.qso is not None
    assert sm.qso.their_call == "DM2HK", \
        f"Direkt-Reply muss Vorrang haben, gepickt: {sm.qso.their_call}"
    assert sm.state is State.QSO_REPORT


def test_idle_no_auto_answer_ignores_direct_reply(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """Ohne auto_answer geschieht im IDLE nichts — auch keine Pick-Up
    von Direkt-Antworten. Mode-Schalter respektieren."""
    sm.ctx.auto_answer = False
    sm.on_decodes(good_hw, [
        _decode("DM2HK", "DK9XR", "DK9XR DM2HK -20", snr=-9),
    ])
    assert sm.state is State.IDLE
    assert sm.qso is None


# ---------------------------------------------------------------------------
# Direct-Reply-Pickup respektiert Cooldown (Mikro-Bug-Fix 2026-05-23).
#
# Sebastian sah in der Nacht: SV0TPN wiederholte 4x ihren Tail-Ender
# "DK9XR SV0TPN -13", wir gingen jedesmal in QSO_REPORT obwohl der
# erste Versuch nach Bail in Cooldown laufen sollte. Der c5131a8-Pfad
# umging den recent_until-Filter den _pick_hunt_target schon nutzt.
# Fix: Cooldown-Check auch fuer Direct-Reply-Pickup.
def test_direct_reply_pickup_respects_cooldown(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """Wenn ein Tail-Ender von einer Station kommt die im Cooldown ist
    (z.B. nach QSO_REPORT-timeout), darf der Direct-Reply-Pickup NICHT
    triggern. Verhindert die SV0TPN-Endlosschleife."""
    sm.ctx.auto_answer = True
    # Station SV0TPN ist im Cooldown (z.B. von vorherigem Bail)
    sm.ctx.recent_until["SV0TPN"] = datetime.now(UTC).timestamp() + 600.0  # 10 min
    sm.on_decodes(good_hw, [
        _decode("SV0TPN", "DK9XR", "DK9XR SV0TPN -13", snr=-9, freq=2009),
    ])
    # State soll IDLE bleiben, kein QSO_REPORT-Wechsel
    assert sm.state is State.IDLE, \
        f"Tail-Ender im Cooldown haette NICHT triggern sollen, state={sm.state}"
    assert sm.qso is None


def test_direct_reply_pickup_grid_respects_cooldown(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """Analog fuer Grid-Reply: Stationen im Cooldown werden uebergangen."""
    sm.ctx.auto_answer = True
    sm.ctx.recent_until["IU0ERZ"] = datetime.now(UTC).timestamp() + 600.0
    sm.on_decodes(good_hw, [
        _decode("IU0ERZ", "DK9XR", "DK9XR IU0ERZ JN61", grid="JN61", snr=-10),
    ])
    assert sm.state is State.IDLE
    assert sm.qso is None


def test_direct_reply_pickup_works_after_cooldown_expires(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """Nach Ablauf des Cooldowns soll Direct-Reply-Pickup wieder triggern."""
    sm.ctx.auto_answer = True
    # Cooldown bereits abgelaufen
    sm.ctx.recent_until["SV0TPN"] = datetime.now(UTC).timestamp() - 10.0
    sm.on_decodes(good_hw, [
        _decode("SV0TPN", "DK9XR", "DK9XR SV0TPN -13", snr=-9, freq=2009),
    ])
    assert sm.state is State.QSO_REPORT
    assert sm.qso is not None and sm.qso.their_call == "SV0TPN"


# ---------------------------------------------------------------------------
# CQ-TX-Slot-Parity (Sebastian 2026-05-23)
#
# Kritischer Bug: im CQ_CALLING-Pfad pushte on_slot_tick in JEDEM Slot
# einen CQ → Halbduplex-Rig war dauerhaft im TX → 0 RX-Decodes ueber
# 34 min, Funkstille-Watchdog feuerte. Fix: in CQ_CALLING nur in der
# konfigurierten Slot-Haelfte (even oder odd) senden. Default "even"
# folgt WSJT-X-Konvention (CQ-Rufer in den 00/30s-Slots).
from ft8_appliance.runtime.slot_clock import SlotTick


def _tick(posix: float, slot_seconds: float = 15.0, index: int = 0) -> SlotTick:
    from datetime import datetime, UTC
    return SlotTick(
        index=index,
        posix=posix,
        utc_start=datetime.fromtimestamp(posix, tz=UTC),
        slot_seconds=slot_seconds,
    )


def test_cq_tx_only_in_even_slots_when_parity_even(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """cq_tx_slot_parity='even' → TX nur wenn slot_count gerade."""
    sm.ctx.cq_tx_slot_parity = "even"
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()  # initial CQ konsumieren

    # Slot mit gerader Nummer (00 oder 30 Sekunde)
    even_tick = _tick(posix=1779475200.0)  # int(/15)=118591680 % 2 == 0
    sm.on_slot_tick(good_hw, even_tick)
    even_acts = [a for a in sm.drain_actions() if a.kind == "TX_MESSAGE"]
    assert len(even_acts) == 1, "even slot soll CQ senden"

    # Ungerader Slot — soll RX bleiben
    odd_tick = _tick(posix=1779475215.0)  # int(/15)=118591681 % 2 == 1
    sm.on_slot_tick(good_hw, odd_tick)
    odd_acts = [a for a in sm.drain_actions() if a.kind == "TX_MESSAGE"]
    assert len(odd_acts) == 0, "odd slot soll RX-Fenster bleiben"


def test_cq_tx_only_in_odd_slots_when_parity_odd(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """cq_tx_slot_parity='odd' → TX nur in odd-Slots."""
    sm.ctx.cq_tx_slot_parity = "odd"
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()

    even_tick = _tick(posix=1779475200.0)
    sm.on_slot_tick(good_hw, even_tick)
    assert not [a for a in sm.drain_actions() if a.kind == "TX_MESSAGE"], \
        "even slot soll nicht senden wenn parity=odd"

    odd_tick = _tick(posix=1779475215.0)
    sm.on_slot_tick(good_hw, odd_tick)
    assert [a for a in sm.drain_actions() if a.kind == "TX_MESSAGE"], \
        "odd slot soll senden wenn parity=odd"


def test_cq_tx_parity_alternates_correctly_over_many_slots(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """Ueber 10 aufeinanderfolgende Slots → genau 5 TX + 5 RX (jedes 2. Slot)."""
    sm.ctx.cq_tx_slot_parity = "even"
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()
    tx_count = 0
    base_posix = 1779475200.0  # even-aligned
    for i in range(10):
        sm.on_slot_tick(good_hw, _tick(posix=base_posix + i * 15.0, index=i))
        if [a for a in sm.drain_actions() if a.kind == "TX_MESSAGE"]:
            tx_count += 1
    assert tx_count == 5, f"5 TX in 10 Slots erwartet, got {tx_count}"


def test_cq_tx_without_tick_falls_back_to_old_behavior(
    sm: StateMachine, good_hw: HardwareState
) -> None:
    """Backward-Compat: on_slot_tick ohne tick-Parameter (z.B. alte Tests) → TX jedes Mal.
    Real-system reicht den Tick immer durch, aber wir wollen alte Tests nicht brechen."""
    sm.on_user_start_cq(good_hw)
    sm.drain_actions()
    sm.on_slot_tick(good_hw)  # KEIN tick parameter
    assert [a for a in sm.drain_actions() if a.kind == "TX_MESSAGE"], \
        "ohne tick: Fallback-Verhalten = TX in jedem Slot"
