"""The QSO state machine.

Decisions only — no I/O. The machine takes events in, emits "actions"
(TX_MESSAGE, LOG_QSO, NOTIFY, ...) the runtime layer is responsible for
executing. That separation makes the machine trivially unit-testable
against the mocks from :mod:`tests.mocks`.

See ``architecture.md`` §5 for the diagram.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from typing import TYPE_CHECKING

from .guards import GuardLimits, HardwareState, evaluate, first_failure
from .states import DecodedMsg, MachineContext, QsoContext, State

if TYPE_CHECKING:
    from ..runtime.slot_clock import SlotTick

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Outgoing actions the controller has to execute
ActionKind = Literal["TX_MESSAGE", "STOP_TX", "LOG_QSO", "TX_LOCKED"]


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    payload: dict


# ---------------------------------------------------------------------------
@dataclass(slots=True)
class StateMachine:
    """Pure decision engine. One instance per running appliance."""

    ctx: MachineContext
    limits: GuardLimits = field(default_factory=GuardLimits)
    state: State = State.IDLE
    qso: QsoContext | None = None
    # last DecodedMsg list we acted on, for debugging
    last_decodes: list[DecodedMsg] = field(default_factory=list)
    # actions emitted but not yet consumed
    _pending: list[Action] = field(default_factory=list)
    # Per-State-Slot-Budget bevor wir die Konversation aufgeben.
    # Class-Default 6 Slots (~90 s); Orchestrator überschreibt aus
    # config.operating.qso_max_stale_slots beim Boot + Hot-Reload.
    qso_max_stale_slots: int = 6
    # Re-Send-Limit fuer "repeated CQ" — siehe OperatingConfig.
    qso_max_cq_resends: int = 2
    # Re-Send-Limit fuer "repeated report waehrend QSO_REPORT" — Partner
    # hat unsere R-Report nicht decodiert und schickt seinen Report
    # nochmal statt RR73. WSJT-X-Verhalten: R-Report 1× wiederholen.
    qso_max_report_resends: int = 1
    # Tracking-Hilfen fuer QSO_GRACE-State (1-Slot-Fenster nach RR73
    # in dem wir auf Partner-Repeat lauschen). Sebastian 2026-05-24,
    # Audit-Finding 2.
    _grace_partner_call: str | None = None
    _grace_ticks_remaining: int = 0
    # Cooldown-Dauer (Sekunden) nach einem misslungenen QSO-Versuch.
    # Class-Default 900 s = 15 min; Orchestrator ueberschreibt aus
    # config.operating.qso_failed_cooldown_min beim Boot + Hot-Reload.
    qso_failed_cooldown_s: float = 900.0

    # ------------------------------------------------------------------ I/O
    def drain_actions(self) -> list[Action]:
        """Consume and return all actions emitted since the last call."""
        out, self._pending = self._pending, []
        return out

    # ------------------------------------------------------------------ events
    def on_user_start_cq(self, hw: HardwareState) -> None:
        if not self._check_guards(hw):
            return
        self.state = State.CQ_CALLING
        self.qso = None
        self.ctx.cq_count = 0
        # WSJT-Z-style: pressing CQ implies "keep calling after each QSO".
        # Cleared by Stop. Hunting mode (auto_answer) does NOT set this.
        self.ctx.auto_cq = True
        self._emit_cq()

    def on_user_stop(self) -> None:
        """Globaler STOP: alles aus.

        Bisher räumte das nur auto_cq weg, ließ aber auto_answer
        unangetastet. Folge: Off-Klick → state IDLE, aber Hunting-Flag
        noch an → der nächste Decode-Slot picked sofort den nächsten
        CQ-Sender und wir landen ohne sichtbare Pause wieder in
        QSO_RESPOND. Wirkte so als hätte Off nichts getan.
        """
        self.state = State.IDLE
        self.qso = None
        self.ctx.auto_cq = False
        self.ctx.auto_answer = False
        self._pending.append(Action("STOP_TX", {}))

    def on_user_skip_qso(self) -> None:
        """Drop the current QSO without logging it. Used when the
        operator decides mid-sequence that this contact isn't worth
        completing (wrong call, garbled, blacklist hit etc.).
        """
        self.qso = None
        self.state = State.IDLE
        self._pending.append(Action("STOP_TX", {}))

    def on_user_reset_lock(self) -> None:
        # Defensiv: lock_reason in jedem Fall clearen, auch wenn state
        # bereits weiter ist (z.B. Lock loeste sich von selbst auf nach
        # Band-Switch, aber Banner haengt noch im UI). Sebastian
        # 2026-05-24.
        self.ctx.last_lock_reason = None
        if self.state is State.TX_LOCKED:
            self.state = State.IDLE

    def on_user_reply_to(
        self, hw: HardwareState, decoded: DecodedMsg
    ) -> None:
        """User picked a CQ to answer (Hunting / S&P)."""
        if not self._check_guards(hw):
            return
        if decoded.call_from is None:
            return
        self.qso = QsoContext(
            their_call=decoded.call_from,
            their_grid=decoded.grid,
            band=decoded.band,
            freq_offset_hz=decoded.freq_offset_hz or 1500,
            their_snr_at_us=decoded.snr_db,
        )
        self.state = State.QSO_RESPOND
        self._emit_respond_with_grid()

    def on_decodes(self, hw: HardwareState, decodes: Iterable[DecodedMsg]) -> None:
        decodes = list(decodes)
        self.last_decodes = decodes

        # Hunting mode: when idle and auto_answer is on.
        if self.state is State.IDLE and self.ctx.auto_answer:
            # PRIO 1 — jemand spricht UNS direkt an (Tail-Ender oder
            # verspaeteter Reply nach Slot-Verlust). Sebastian sah am
            # 2026-05-22 dass DM2HK "DK9XR DM2HK -20" sendete waehrend
            # wir IDLE waren. Der Picker hat das ignoriert (filtert nur
            # CQ-Decodes) und wir riefen statt dessen Random-Caller an —
            # ein direkt-loggbares QSO ging dabei verloren. Fix: in
            # IDLE+auto_answer zuerst nach Direkt-Antworten suchen.
            #
            # Mikro-Bug-Fix 2026-05-23: ueber Nacht sah Sebastian eine
            # SV0TPN-Endlos-Schleife — Station wiederholte 4x ihren
            # Tail-Ender "DK9XR SV0TPN -13", wir gingen jedesmal in
            # QSO_REPORT und sendeten R-Report, der Partner decodierte
            # uns nie. Der existierende failed-cooldown im
            # _pick_hunt_target griff hier nicht, weil dieser Pfad
            # nicht durch den Picker geht. Loesung: Cooldown-Check
            # auch fuer Direct-Reply-Pickup, damit nach Bail derselbe
            # Tail-Ender nicht sofort wieder triggert.
            now_ts = datetime.now(UTC).timestamp()
            te = _find_answer_with_report_to_us(decodes, self.ctx.callsign)
            if te is not None:
                their_snr, decoded = te
                their_call = decoded.call_from or ""
                cd = self.ctx.recent_until.get(their_call, 0.0)
                if cd > now_ts:
                    log.debug(
                        "Tail-Ender %s im Cooldown (%.0fs verbleibend) — ignoriert",
                        their_call, cd - now_ts,
                    )
                elif not self._check_guards(hw):
                    return
                else:
                    log.info(
                        "IDLE→QSO_REPORT: %s ruft uns direkt mit Report %d → R-Report",
                        their_call, their_snr,
                    )
                    self.qso = QsoContext(
                        their_call=their_call or "?",
                        their_grid=None,
                        band=decoded.band,
                        freq_offset_hz=decoded.freq_offset_hz or 1500,
                        our_snr_received=their_snr,
                        their_snr_at_us=decoded.snr_db,
                    )
                    self.state = State.QSO_REPORT
                    self._emit_send_r_report()
                    return
            ans = _find_answer_to_us(decodes, self.ctx.callsign)
            if ans is not None:
                their_call = ans.call_from or ""
                cd = self.ctx.recent_until.get(their_call, 0.0)
                if cd > now_ts:
                    log.debug(
                        "Grid-Reply %s im Cooldown (%.0fs verbleibend) — ignoriert",
                        their_call, cd - now_ts,
                    )
                elif not self._check_guards(hw):
                    return
                else:
                    log.info(
                        "IDLE→QSO_RESPOND: %s ruft uns mit Grid → Report senden",
                        their_call,
                    )
                    # ans ist eine Grid-Antwort (kein Report von ihnen) →
                    # our_snr_received bleibt None bis sie uns spaeter
                    # einen echten Report schicken. their_snr_at_us nimmt
                    # ans.snr_db (unsere Messung ihrer Grid-Message).
                    self.qso = QsoContext(
                        their_call=their_call or "?",
                        their_grid=ans.grid,
                        band=ans.band,
                        freq_offset_hz=ans.freq_offset_hz or 1500,
                        their_snr_at_us=ans.snr_db,
                    )
                    self.state = State.QSO_RESPOND
                    self._emit_respond_with_report()
                    return

            # PRIO 2 — kein Direkt-Caller, picke staerksten CQ.
            best = self._pick_hunt_target(decodes)
            if best is not None:
                if not self._check_guards(hw):
                    return
                self.qso = QsoContext(
                    their_call=best.call_from or "?",
                    their_grid=best.grid,
                    band=best.band,
                    freq_offset_hz=best.freq_offset_hz or 1500,
                    their_snr_at_us=best.snr_db,
                )
                self.state = State.QSO_RESPOND
                self._emit_respond_with_grid()
                return

        if self.state is State.CQ_CALLING:
            # Tail-Ender first: someone answered our CQ with a direct
            # signal report (skipping the grid stage). When that happens
            # we jump straight to QSO_REPORT and send the R-report,
            # cutting two slots out of the QSO. Architecture §6.6.
            te = _find_answer_with_report_to_us(decodes, self.ctx.callsign)
            if te is not None:
                if not self._check_guards(hw):
                    return
                their_snr, decoded = te
                self.qso = QsoContext(
                    their_call=decoded.call_from or "?",
                    their_grid=None,
                    band=decoded.band,
                    freq_offset_hz=decoded.freq_offset_hz or 1500,
                    our_snr_received=their_snr,
                    their_snr_at_us=decoded.snr_db,
                )
                self.state = State.QSO_REPORT
                self._emit_send_r_report()
                return

            # Normal sequence: someone answered us with their grid.
            ans = _find_answer_to_us(decodes, self.ctx.callsign)
            if ans is not None:
                if not self._check_guards(hw):
                    return
                # Grid-Antwort = noch kein Report von ihnen, our_snr_received
                # bleibt None. their_snr_at_us aus unserer Decode-Messung.
                self.qso = QsoContext(
                    their_call=ans.call_from or "?",
                    their_grid=ans.grid,
                    band=ans.band,
                    freq_offset_hz=ans.freq_offset_hz or 1500,
                    their_snr_at_us=ans.snr_db,
                )
                self.state = State.QSO_RESPOND
                self._emit_respond_with_report()

        elif self.state is State.QSO_RESPOND and self.qso is not None:
            # Tracking: bei jedem Decode des Partners their_snr_at_us
            # auf den aktuellsten Wert ziehen (Sebastian Audit Action 5,
            # WSJT-X-Konformanz fuer R-Report). Nutzt den SNR den WIR
            # gemessen haben, nicht den den er uns gemeldet hat.
            self._track_partner_snr(decodes)
            # did they send us a report?
            rep = _find_report_from_them(decodes, self.qso.their_call, self.ctx.callsign)
            if rep is not None:
                if not self._check_guards(hw):
                    return
                self.qso.our_snr_received = rep
                self.qso.stale_slots = 0  # progress — reset bail counter
                self.state = State.QSO_REPORT
                self._emit_send_r_report()
            else:
                # Kein Report von ihnen, aber sie rufen erneut CQ
                # → sie haben uns nicht gehört (Kollision/QSB). Wir
                # senden unser Grid nochmal anstatt stumm bis zum
                # Timeout zu warten.
                their_call = self.qso.their_call
                heard_them_cq_again = any(
                    d.call_from == their_call
                    and (d.message or "").startswith("CQ")
                    for d in decodes
                )
                # Wenn sie schon mit jemand anderem im QSO sind, geben
                # wir auf — die haben einen anderen Caller gepickt.
                heard_them_with_other = any(
                    d.call_from == their_call
                    and d.call_to is not None
                    and d.call_to != self.ctx.callsign
                    for d in decodes
                )
                if heard_them_with_other:
                    log.info("QSO_RESPOND: %s picked another caller — bailing",
                             their_call)
                    self._bail_qso_with_cooldown(their_call, "picked_another")
                elif heard_them_cq_again:
                    # Re-Send-Limit: wenn der Partner uns trotz N Antworten
                    # weiter ignoriert (sie hoeren uns nicht), aufhoeren
                    # und in den Cooldown. Sebastian sah 2026-05-22 wie
                    # SV9TLU 12x ignoriert wurde und wir 12 TX-Slots
                    # verschwendeten.
                    if self.qso.cq_resends >= self.qso_max_cq_resends:
                        log.info(
                            "QSO_RESPOND: %s ignored us %d× (max %d) — bailing",
                            their_call, self.qso.cq_resends, self.qso_max_cq_resends,
                        )
                        self._bail_qso_with_cooldown(their_call, "max_resends")
                        return
                    if not self._check_guards(hw):
                        return
                    self.qso.cq_resends += 1
                    log.info(
                        "QSO_RESPOND: %s repeated CQ → re-sending grid (%d/%d)",
                        their_call, self.qso.cq_resends, self.qso_max_cq_resends,
                    )
                    self.qso.stale_slots = 0
                    self._emit_respond_with_grid()

        elif self.state is State.QSO_GRACE and self._grace_partner_call:
            # Wir haben RR73 gesendet + geloggt, lauschen jetzt 1 Slot
            # ob der Partner sein RR73 wiederholt (= er hat unser RR73
            # nicht decodiert). Wenn ja: ein 73 hinterher als finale
            # Closure-Bestaetigung (analog WSJT-X Tx6). Sebastian
            # 2026-05-24, Audit-Finding 2.
            if _find_closing(decodes, self._grace_partner_call, self.ctx.callsign):
                if not self._check_guards(hw):
                    return
                log.info(
                    "QSO_GRACE: %s repeated RR73 → 73 hinterher zur Closure",
                    self._grace_partner_call,
                )
                msg = f"{self._grace_partner_call} {self.ctx.callsign} 73"
                # freq_offset_hz kennt der Grace-State nicht mehr direkt
                # (qso wurde in _emit_log_qso auf None gesetzt), default
                # tx_payload nutzt CQ_DEFAULT — fuer ein einzelnes 73
                # akzeptabel. Alternative: freq in _grace_partner_call
                # mitschleppen, aber das ist Overhead fuer 1-Slot-Edge.
                self._pending.append(
                    Action("TX_MESSAGE", self._tx_payload(msg, "ack73"))
                )
                self._exit_grace()

        elif self.state is State.QSO_REPORT and self.qso is not None:
            # Tracking (siehe Audit Action 5): aktualisiere their_snr_at_us
            # bei jedem Partner-Decode auch in QSO_REPORT damit ein R-Resend
            # mit dem neuesten Wert sendet.
            self._track_partner_snr(decodes)
            # did they send RR73 / 73 ?
            if _find_closing(decodes, self.qso.their_call, self.ctx.callsign):
                self.qso.stale_slots = 0
                self.state = State.QSO_LOG
                self._emit_log_qso()
            else:
                # Kein RR73 — zwei Symptome dass sie unseren R-Report nicht
                # decoded haben, und in beiden Fällen wollen wir nochmal
                # senden (gleicher Cap qso_max_report_resends):
                #
                #  (a) Partner schickt nochmal seinen Report ohne R-Prefix
                #      → er hat unser Grid decoded und gibt uns Report,
                #      aber unser R-Report kam bei ihm nicht an.
                #      Sebastian 2026-05-24 nach UN7JO-Verlust.
                #
                #  (b) Partner ruft wieder CQ → er ist gar nicht in den
                #      QSO-Modus eingestiegen (Decode bei ihm war zu
                #      schwach für unsere TX-Sequence). Auf seiner Seite
                #      existiert das QSO nicht. Wir versuchen einen
                #      R-Report-Resend; klappt's nicht, läuft Timeout.
                #      Sebastian 2026-05-24 nach DO1BJF-Verlust.
                their_call = self.qso.their_call
                # (c) Partner hat einen anderen Caller gepickt nach unserem
                #     R-Report (z.B. weil eine staerkere Station gleichzeitig
                #     geantwortet hat). Spiegelt die picked_another-Detection
                #     aus QSO_RESPOND. Sebastian 2026-05-24 nach M7CCZ-Case:
                #     M7CCZ gab uns +06, wir sendeten R-06, dann startete er
                #     QSO mit EA1DUS statt RR73 zu uns. Wir warteten 3 Slots
                #     vergeblich. Ohne diesen Check laufen wir in Timeout
                #     + verschwenden TX-Slots, mit Check bail sofort +
                #     Cooldown (Partner ist sowieso weg).
                heard_them_with_other = any(
                    d.call_from == their_call
                    and d.call_to is not None
                    and d.call_to != self.ctx.callsign
                    for d in decodes
                )
                if heard_them_with_other:
                    log.info(
                        "QSO_REPORT: %s picked another caller — bailing",
                        their_call,
                    )
                    self._bail_qso_with_cooldown(their_call, "picked_another")
                    return
                rep_again = _find_report_from_them(
                    decodes, their_call, self.ctx.callsign
                )
                them_cq_again = any(
                    d.call_from == their_call
                    and (d.message or "").startswith("CQ")
                    for d in decodes
                )
                if rep_again is not None or them_cq_again:
                    reason = "repeated report" if rep_again is not None else "repeated CQ"
                    if self.qso.report_resends >= self.qso_max_report_resends:
                        # Schon resent — sie hoeren uns einfach nicht.
                        # In Timeout laufen lassen.
                        log.info(
                            "QSO_REPORT: %s %s %d× (max %d) — kein R-Resend mehr",
                            their_call,
                            reason,
                            self.qso.report_resends,
                            self.qso_max_report_resends,
                        )
                    else:
                        if not self._check_guards(hw):
                            return
                        self.qso.report_resends += 1
                        log.info(
                            "QSO_REPORT: %s %s → R-Report Resend (%d/%d)",
                            their_call,
                            reason,
                            self.qso.report_resends,
                            self.qso_max_report_resends,
                        )
                        self.qso.stale_slots = 0
                        self._emit_send_r_report()

    def on_slot_tick(self, hw: HardwareState, tick: "SlotTick | None" = None) -> None:
        # Boot-Auto-Resume (Sebastian 2026-05-23 nach Multi-Operator-
        # Refactor): wenn auto_cq beim Boot via boot_mode=cq gesetzt
        # wurde aber state noch IDLE (kein User-Event seit Restart),
        # transition zu CQ_CALLING im naechsten Slot. Damit ueberlebt
        # der CQ-Modus einen Service-Restart automatisch.
        if (
            self.state is State.IDLE
            and self.ctx.auto_cq
            and self._check_guards(hw)
        ):
            log.info("on_slot_tick: auto_cq=True bei IDLE → CQ_CALLING")
            self.state = State.CQ_CALLING

        # In CQ_CALLING senden wir nur in der konfigurierten Slot-
        # Haelfte (even oder odd). Der andere Slot bleibt frei zum
        # Empfang von Antwortern. Sebastian sah 2026-05-23 dass ohne
        # diesen Filter TX in JEDEM Slot stattfand → halbduplex Rig
        # immer im TX → 0 RX-Decodes ueber 34 min trotz laufendem
        # Decoder. Funkstille-Watchdog feuerte voellig zu Recht.
        if self.state is State.CQ_CALLING:
            # Guards laufen IMMER (auch in RX-Slots) — sonst wuerde bei
            # SWR-Spike/GPS-Loss im RX-Slot keine TX_LOCKED-Transition
            # ausgeloest und der naechste TX-Slot wuerde unsicher senden.
            if not self._check_guards(hw):
                return
            # Slot-Parity: nur in der konfigurierten Slot-Haelfte senden,
            # der andere Slot bleibt RX. Sebastian sah 2026-05-23 dass
            # ohne Filter TX in JEDEM Slot → 0 RX-Decodes → Funkstille.
            if tick is not None and self.ctx.cq_tx_slot_parity in ("even", "odd"):
                # round() statt int() — die SlotClock liefert posix kurz
                # NACH der Slot-Grenze (z.B. 1779520349.9995 statt
                # 1779520350.0), int() rundet das in den vorigen Slot
                # und kippt die Parity. Sebastian sah 2026-05-23: trotz
                # parity=even sendeten wir auch in odd-Slots. round()
                # waehlt die naechstgelegene Slot-Boundary korrekt.
                slot_count = round(tick.posix / (tick.slot_seconds or 15.0))
                is_even = (slot_count % 2 == 0)
                want_even = (self.ctx.cq_tx_slot_parity == "even")
                if is_even != want_even:
                    return
            self.ctx.cq_count += 1
            self._emit_cq()
            return

        # In QSO states, count stale slots and abort if the partner went
        # silent. Without this the machine sits forever waiting for a
        # report or RR73 that will never arrive — observed live with
        # EA7GUM on 20m: we sent grid, they never answered, we hung.
        if self.state is State.QSO_RESPOND and self.qso is not None:
            self.qso.stale_slots += 1
            if self.qso.stale_slots > self.qso_max_stale_slots:
                their_call = self.qso.their_call
                log.info("QSO_RESPOND timeout — %s went silent, returning to IDLE",
                         their_call)
                self._bail_qso_with_cooldown(their_call, "went_silent")
        elif self.state is State.QSO_REPORT and self.qso is not None:
            self.qso.stale_slots += 1
            if self.qso.stale_slots > self.qso_max_stale_slots:
                their_call = self.qso.their_call
                log.info("QSO_REPORT timeout — %s never closed, returning to IDLE",
                         their_call)
                self._bail_qso_with_cooldown(their_call, "report_never_closed")
        elif self.state is State.QSO_GRACE:
            # 1-Slot-Fenster fuer Partner-RR73-Repeat. Wenn der Slot
            # rum ist ohne Repeat -> normaler Continuation-Pfad
            # (auto_cq → CQ_CALLING; sonst IDLE).
            self._grace_ticks_remaining -= 1
            if self._grace_ticks_remaining <= 0:
                self._exit_grace()

    # ------------------------------------------------------------------ TX-emit helpers
    # Default audio-frequency for own-initiated CQs. The orchestrator may
    # override this from the smart-frequency-picker (architecture §6.2)
    # once that lands.
    CQ_DEFAULT_FREQ_HZ = 1500

    def _tx_payload(self, message: str, kind: str, freq_override_hz: int | None = None) -> dict:
        """Common TX_MESSAGE payload — always carries the audio frequency
        so the downstream synth knows where to put the signal.

        For CQ we pick our own default (caller can rebind it). For all
        in-QSO messages we *match the frequency we heard them on* — this
        is what well-behaved FT8 stations do (stay split-frequency).
        """
        if freq_override_hz is not None:
            freq_hz = freq_override_hz
        elif self.qso is not None:
            freq_hz = self.qso.freq_offset_hz
        else:
            freq_hz = self.CQ_DEFAULT_FREQ_HZ
        return {
            "message": message,
            "kind": kind,
            "state": self.state.name,
            "freq_offset_hz": freq_hz,
        }

    # FT8-Audio-Passband: 200–2900 Hz nutzbar, aber wir bleiben in
    # 200–2400 Hz (klassisches WSJT-X-Decoder-Fenster). Quiet-Picker
    # rastert in 100-Hz-Bins, mit FT8-Tonband-Breite ~50 Hz schauen
    # wir ±100 Hz Nachbarbereich an für die Belegungs-Heuristik.
    CQ_AUDIO_MIN_HZ = 300
    CQ_AUDIO_MAX_HZ = 2400
    CQ_BIN_HZ = 100

    def _next_cq_freq_hz(self) -> int:
        """Smart-Picker: wähle die aktuell ruhigste Audio-Frequenz.

        Statt dumm durch ``ctx.cq_freq_rotation`` zu cyclen schauen wir
        in den letzten Decodes welcher 100-Hz-Bin am wenigsten belegt
        ist und senden dort. Findet keine Decodes (Pi gerade gebootet,
        Band tot) → Fallback auf die Rotation oder den Fix-Default.

        Architekt-Kommentar: Auswahl mit Pseudo-Random bei Gleichstand
        verhindert dass mehrere Geräte mit gleicher Heuristik in das
        gleiche freie Loch springen. Den Index der Rotation behalten
        wir als Fallback wenn die Smart-Heuristik abschaltet.
        """
        # Smart-Picker aktiv wenn wir Decodes haben
        decodes_with_freq = [
            d for d in self.last_decodes if d.freq_offset_hz is not None
        ]
        if decodes_with_freq:
            bin_hz = self.CQ_BIN_HZ
            min_hz = self.CQ_AUDIO_MIN_HZ
            max_hz = self.CQ_AUDIO_MAX_HZ
            # Belegungs-Histogramm: pro Bin die Zahl der Decodes in
            # ±1-Bin-Reichweite (50 Hz Tonband überlappt locker eine
            # 100-Hz-Bin-Grenze, also Nachbarn mitzählen).
            from collections import Counter
            hist: Counter[int] = Counter()
            for d in decodes_with_freq:
                center = int(d.freq_offset_hz)
                if min_hz - bin_hz < center < max_hz + bin_hz:
                    b = (center // bin_hz) * bin_hz
                    for nb in (b - bin_hz, b, b + bin_hz):
                        hist[nb] += 1
            # Kandidaten in unserem Sende-Bereich
            candidates = list(range(min_hz, max_hz + 1, bin_hz))
            # Sort by (occupancy, distance to mid-band) — ruhigster bin
            # gewinnt, bei Gleichstand möglichst Bandmitte (geringere
            # Drift-Empfindlichkeit beim Decoder).
            mid = (min_hz + max_hz) // 2
            candidates.sort(key=lambda b: (hist.get(b, 0), abs(b - mid)))
            # Top-3 Kandidaten mischen damit zwei Pis nicht denselben
            # picken — pseudo-random via cq_freq_index als seed.
            top = candidates[:3]
            self.ctx.cq_freq_index = (self.ctx.cq_freq_index + 1) % max(1, len(top))
            return top[self.ctx.cq_freq_index % len(top)]

        # Fallback: alte Rotation
        rot = self.ctx.cq_freq_rotation
        if not rot:
            return self.CQ_DEFAULT_FREQ_HZ
        idx = self.ctx.cq_freq_index % len(rot)
        self.ctx.cq_freq_index = (idx + 1) % len(rot)
        return rot[idx]

    def _bail_qso_with_cooldown(self, their_call: str, reason: str) -> None:
        """Abbrechen eines QSO-Versuchs + Cooldown-Eintrag fuer den Partner.

        Sebastian sah am 2026-05-22 abends dass eine einzige Station
        (SV9TLU) 12 TX-Slots verschwendete weil sie unsere Anrufe nicht
        hoerte und wir bei jedem repeated-CQ erneut antworteten. Failed-
        Attempt-Cooldown: nach einem Bail wird der Call fuer
        qso_failed_cooldown_s in ctx.recent_until eingetragen — der
        Hunting-Picker filtert ihn dort raus. Cooldown ist kuerzer als
        der Erfolgs-Cooldown (15 vs 30 min) damit kurz danach veraen-
        derte Propagation/QSB wieder eine Chance bekommt.

        Reasons (fuer Logs/Debug): "picked_another", "max_resends",
        "went_silent", "report_never_closed".
        """
        if self.qso_failed_cooldown_s > 0 and their_call:
            cooldown_until = datetime.now(UTC).timestamp() + self.qso_failed_cooldown_s
            self.ctx.recent_until[their_call] = cooldown_until
            log.debug(
                "Cooldown: %s set for %.0f s (reason=%s)",
                their_call, self.qso_failed_cooldown_s, reason,
            )
        self.qso = None
        self.state = State.IDLE
        self._pending.append(Action("STOP_TX", {}))

    def _emit_cq(self) -> None:
        msg = f"CQ {self.ctx.callsign} {self.ctx.my_grid[:4]}"
        freq = self._next_cq_freq_hz()
        self._pending.append(Action("TX_MESSAGE", self._tx_payload(msg, "cq", freq_override_hz=freq)))

    def _emit_respond_with_grid(self) -> None:
        assert self.qso is not None
        msg = f"{self.qso.their_call} {self.ctx.callsign} {self.ctx.my_grid[:4]}"
        self._pending.append(Action("TX_MESSAGE", self._tx_payload(msg, "respond_grid")))

    # FT8-Spec: SNR-Feld ist 7-bit signed, Bereich -50..+49 dB (siehe
    # WSJT-X-Manual + QEX-Paper). Werte ausserhalb werden vom ft8_lib-
    # Encoder ggf. truncated oder fuehren zu invaliden Frames.
    # Sebastian-Audit v0.3.3 (defensiv): clamp auf safe range.
    FT8_SNR_MIN = -50
    FT8_SNR_MAX = 49

    def _clamp_snr(self, snr: int | None, default: int = -10) -> int:
        if snr is None:
            return default
        return max(self.FT8_SNR_MIN, min(self.FT8_SNR_MAX, int(snr)))

    def _emit_respond_with_report(self) -> None:
        assert self.qso is not None
        # WSJT-X-konform (Audit Action 5, v0.3.2): R + SNR-of-them-at-us
        # = der Wert den WIR von ihrem Signal gemessen haben, NICHT
        # Echo von their_snr_at_them.
        snr = self._clamp_snr(self.qso.their_snr_at_us)
        self.qso.their_snr = snr  # = rst_sent fuer Log
        msg = f"{self.qso.their_call} {self.ctx.callsign} {snr:+03d}"
        self._pending.append(Action("TX_MESSAGE", self._tx_payload(msg, "respond_report")))

    def _emit_send_r_report(self) -> None:
        assert self.qso is not None
        # WSJT-X-konform (Audit Action 5, v0.3.2): R + SNR-of-them-at-us.
        # Bis v0.3.1 sendeten wir hier Echo des their_snr_at_them
        # (= our_snr_received), was rst_sent in der DB stets gleich
        # rst_rcvd machte (= statistisch wertlos) und Partner kein
        # echtes Feedback ueber sein Signal bei uns gab.
        snr = self._clamp_snr(self.qso.their_snr_at_us)
        self.qso.their_snr = snr  # = rst_sent fuer Log
        msg = f"{self.qso.their_call} {self.ctx.callsign} R{snr:+03d}"
        self._pending.append(Action("TX_MESSAGE", self._tx_payload(msg, "r_report")))

    def _track_partner_snr(self, decodes: Iterable[DecodedMsg]) -> None:
        """Update qso.their_snr_at_us auf den neuesten SNR den wir vom
        Partner gemessen haben (= d.snr_db jedes Partner-Decodes diesen
        Slot). Sebastian Audit-Action 5, v0.3.2. Mehrere Decodes pro
        Slot: nimm den letzten (Slot-RX ist short window, alle Werte
        sind ungefaehr aequivalent — Komplexitaet von Median-ueber-QSO
        bringt fuer FT8-15s-Slots keinen Mehrwert).
        """
        if self.qso is None:
            return
        their = self.qso.their_call
        for d in decodes:
            if d.call_from == their and d.snr_db is not None:
                self.qso.their_snr_at_us = d.snr_db

    def _emit_log_qso(self) -> None:
        assert self.qso is not None
        rr73 = f"{self.qso.their_call} {self.ctx.callsign} RR73"
        self._pending.append(Action("TX_MESSAGE", self._tx_payload(rr73, "rr73")))
        self._pending.append(
            Action(
                "LOG_QSO",
                {
                    "call": self.qso.their_call,
                    "band": self.qso.band,
                    "grid_rcvd": self.qso.their_grid,
                    "rst_sent": self.qso.their_snr,
                    "rst_rcvd": self.qso.our_snr_received,
                    "qso_start": self.qso.started,
                    "qso_end": datetime.now(UTC),
                    # audio-offset for accurate ADIF freq logging — without
                    # this the orchestrator would only know the rig dial
                    # frequency and miss the per-QSO audio split.
                    "freq_offset_hz": self.qso.freq_offset_hz,
                },
            )
        )
        # QSO_GRACE: 1-Slot-Wartefenster fuer Partner-RR73-Repeat. Wenn
        # er sein RR73 nochmal sendet (= unser RR73 nicht decodiert),
        # antworten wir noch mit 73 (Tx6) als WSJT-X-konforme Closure.
        # Sebastian 2026-05-24, Audit-Finding 2.
        self._grace_partner_call = self.qso.their_call
        self._grace_ticks_remaining = 1
        self.qso = None
        self.state = State.QSO_GRACE

    def _exit_grace(self) -> None:
        """Beende QSO_GRACE — normaler Continuation-Pfad."""
        self._grace_partner_call = None
        self._grace_ticks_remaining = 0
        if self.ctx.auto_cq:
            # WSJT-Z-style: keep calling after each completed QSO until
            # the user hits Stop. Reset cq_count so periodic retransmit
            # timing in on_slot_tick stays sane (auch der Idle-Watchdog
            # in Finding 1 nutzt cq_count als Schwelle).
            self.state = State.CQ_CALLING
            self.ctx.cq_count = 0
            self._emit_cq()
        else:
            self.state = State.IDLE

    # ------------------------------------------------------------------ hunting helpers
    def _pick_hunt_target(self, decodes: Iterable[DecodedMsg]) -> DecodedMsg | None:
        """Pick the strongest non-blacklisted CQ from this slot's decodes.

        Rules:
          * Must be a CQ (call_to is None and message starts with CQ)
          * call_from not in blacklist
          * call_from != our own callsign (don't reply to ourselves)
          * SNR >= hunt_snr_floor_db (kein "die wird uns eh nicht hoeren")
          * highest SNR wins
        """
        cqs = [
            d for d in decodes
            if d.call_from
            and d.call_from != self.ctx.callsign
            and d.call_from not in self.ctx.blacklist
            and d.call_to is None
            and (d.message or "").startswith("CQ")
        ]
        # SNR-Floor: sehr schwache Decodes uebersprungen — die Station hoert
        # uns wahrscheinlich nicht. Sebastian 2026-05-22: rst_rcvd-Median
        # bei seinem Setup ist -10 dB, 90%-Perzentil ~-18 dB. Stationen
        # die mit SNR < hunt_snr_floor_db ankommen sind hoehstens schwach
        # erreichbar und produzieren in der Praxis lange Bail-Sequences.
        if self.ctx.hunt_snr_floor_db is not None:
            cqs = [
                d for d in cqs
                if d.snr_db is None or d.snr_db >= self.ctx.hunt_snr_floor_db
            ]
        # Audio-Frequenz-Filter: Decodes ausserhalb [min, max] uebersprungen,
        # weil unser Reply dort durch den Rig-Audio-Bandpass gedaempft
        # waere. Sebastian sah 2026-05-22 wie ein Reply auf 262 Hz (unter
        # dem IC-7300-Bandpass von ~300 Hz) den PI in einen PWR-Spike
        # mit Watchdog-Cut trieb. Mit Filter wird die Station gar nicht
        # erst angerufen.
        fmin = self.ctx.hunt_audio_freq_min_hz
        fmax = self.ctx.hunt_audio_freq_max_hz
        if fmin is not None or fmax is not None:
            cqs = [
                d for d in cqs
                if d.freq_offset_hz is None
                or (
                    (fmin is None or d.freq_offset_hz >= fmin)
                    and (fmax is None or d.freq_offset_hz <= fmax)
                )
            ]
        if self.ctx.skip_worked:
            cqs = [d for d in cqs if d.call_from not in self.ctx.worked]
        # DXCC-Only-Modus (Award-Hunter): wir picken nur Calls aus
        # Ländern die wir noch nicht haben. Falls keine neu-DXCC-Calls
        # in dieser Slot-Welle, halten wir die Sendung an statt was
        # Routine-mäßiges anzurufen.
        if self.ctx.dxcc_only_mode:
            cqs = [d for d in cqs if (d.call_from or "") in self.ctx.new_dxcc_calls]
        # Recent-Cooldown: Station ist gerade erst gearbeitet, gleicher
        # Op ruft trotzdem weiter CQ → wir überspringen ihn bis sein
        # Cooldown-Fenster abgelaufen ist (Default 30 min, in Config
        # einstellbar). Verhindert "3× EA4GA in 5 min"-Effekte.
        if self.ctx.recent_until:
            now_ts = datetime.now(UTC).timestamp()
            cqs = [
                d for d in cqs
                if self.ctx.recent_until.get(d.call_from or "", 0) <= now_ts
            ]
        if not cqs:
            return None
        # Priorisierung: zuerst neue-DXCC, dann SNR. Sortier-Key liefert
        # (is_new_dxcc, snr_db) absteigend — Python-tuple-compare macht
        # den Rest. Sebastian's Wunsch: lieber den ZP6CW aus Paraguay
        # mit -18 dB anrufen statt den vierten EA4 aus Spanien mit -8 dB.
        prefer_dxcc = (
            self.ctx.prefer_new_dxcc and bool(self.ctx.new_dxcc_calls)
        )

        def score(d: DecodedMsg) -> tuple[int, int]:
            is_new = (
                1 if prefer_dxcc and (d.call_from or "") in self.ctx.new_dxcc_calls
                else 0
            )
            return (is_new, d.snr_db if d.snr_db is not None else -99)

        return max(cqs, key=score)

    def set_auto_answer(self, enabled: bool) -> None:
        """Toggle hunting mode. Active only while state is IDLE."""
        self.ctx.auto_answer = enabled

    # ------------------------------------------------------------------ guard plumbing
    def _check_guards(self, hw: HardwareState) -> bool:
        results = evaluate(hw, self.limits)
        bad = first_failure(results)
        if bad is None:
            # Alle Guards gruen → ein eventuell hängender lock_reason
            # vom letzten Block ist obsolet, banner soll verschwinden.
            # Sebastian 2026-05-24: nach 20m-Antenna-Lock blieb der
            # Reason ewig im Status-Bar stehen obwohl wir laengst
            # wieder auf 15m mit QSO_RESPOND aktiv waren.
            if self.ctx.last_lock_reason is not None:
                log.info(
                    "Lock-Reason auto-cleared (Guards wieder gruen): %s",
                    self.ctx.last_lock_reason,
                )
                self.ctx.last_lock_reason = None
            return True
        log.warning("guard %s failed: %s", bad.name, bad.reason)
        self.state = State.TX_LOCKED
        self.ctx.last_lock_reason = f"{bad.name}: {bad.reason}"
        self._pending.append(Action("TX_LOCKED", {"reason": self.ctx.last_lock_reason}))
        self._pending.append(Action("STOP_TX", {}))
        return False


# ---------------------------------------------------------------------------
# Tiny parsers — message texts in FT8 are very stylised, no AI needed
_SNR_RE = re.compile(r"\s([+-]\d{1,2})\b")
_R_SNR_RE = re.compile(r"\sR([+-]\d{1,2})\b")
_GRID_RE = re.compile(r"\b([A-R]{2}[0-9]{2})\b")
_CALL_RE = r"[A-Z0-9/]{3,11}"


def _find_answer_to_us(decodes: Iterable[DecodedMsg], my_call: str) -> DecodedMsg | None:
    """Decode like ``DK9XR W1AW FN31`` — someone calling us with their grid."""
    for d in decodes:
        if d.call_to == my_call and d.grid:
            return d
    return None


def _find_answer_with_report_to_us(
    decodes: Iterable[DecodedMsg], my_call: str
) -> tuple[int, DecodedMsg] | None:
    """Tail-Ender: ``DK9XR W1AW -12`` — they skip the grid stage and
    answer our CQ with a direct signal report (no ``R`` prefix yet).

    Returns ``(their_snr_for_us, decoded)`` on match, else ``None``.
    Skips messages that look like the *next* exchange step (``R-12``,
    grid present) so we don't mis-classify a normal QSO-RESPOND echo
    as a tail-end opener.
    """
    for d in decodes:
        if d.call_to != my_call:
            continue
        if d.grid:
            # That's a grid-answer, handled by _find_answer_to_us
            continue
        msg = d.message or ""
        # Must NOT be an R-report (those belong to QSO_RESPOND state)
        if _R_SNR_RE.search(" " + msg):
            continue
        m = _SNR_RE.search(" " + msg)
        if m is None:
            continue
        return int(m.group(1)), d
    return None


def _find_report_from_them(
    decodes: Iterable[DecodedMsg], their_call: str, my_call: str
) -> int | None:
    """Decode like ``DK9XR W1AW -12`` — them giving us a signal report."""
    for d in decodes:
        if d.call_to == my_call and d.call_from == their_call:
            m = _SNR_RE.search(" " + d.message)
            if m and not d.message.startswith(f"{my_call} {their_call} R"):
                return int(m.group(1))
    return None


def _find_closing(
    decodes: Iterable[DecodedMsg], their_call: str, my_call: str
) -> bool:
    """Decode like ``DK9XR W1AW RR73`` / ``RRR`` / ``73``."""
    for d in decodes:
        if d.call_to == my_call and d.call_from == their_call:
            tail = d.message.split()[-1].upper()
            if tail in {"RR73", "RRR", "73"}:
                return True
    return False
