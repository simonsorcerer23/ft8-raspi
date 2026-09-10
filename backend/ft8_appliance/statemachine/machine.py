"""The QSO state machine.

Decisions only — no I/O. The machine takes events in, emits "actions"
(TX_MESSAGE, LOG_QSO, NOTIFY, ...) the runtime layer is responsible for
executing. That separation makes the machine trivially unit-testable
against the mocks from :mod:`tests.mocks`.

See ``architecture.md`` §5 for the diagram.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, TypeGuard

from .. import i18n as _i18n
from ..util.callsign import base_call
from .guards import GuardLimits, HardwareState, evaluate, first_failure
from .states import DecodedMsg, MachineContext, QsoContext, State

if TYPE_CHECKING:
    from ..runtime.slot_clock import SlotTick

log = logging.getLogger(__name__)


def _is_usable_partner_call(call: str | None) -> TypeGuard[str]:
    """Reject decoder placeholders such as <...> before they become TX targets."""
    if not call:
        return False
    call = call.strip().upper()
    if not call or "<" in call or ">" in call or "." in call:
        return False
    return True


# ---------------------------------------------------------------------------
# v0.10.0 Hunt-Priority-Tiers (Sebastian-Wunsch):
# Jede Tier-Funktion bekommt (decoded_msg, ctx) und liefert einen Score ≥ 0.
# Die Score-Tuples werden lexikographisch verglichen → erste Stelle dominiert,
# bei Gleichstand zweite usw. Letzte Stelle ist typisch SNR als Tie-Breaker.
# Reihenfolge wird per ctx.hunt_priority gesteuert (vom User editierbar).
# ---------------------------------------------------------------------------

def _tier_marine_psk(d: DecodedMsg, ctx: MachineContext) -> int:
    """Marinefunker AND PSK sagt 'hört uns' — beste kombi."""
    if not d.call_from:
        return 0
    norm = d.call_from.upper()
    in_marine = norm in ctx.marine_calls
    in_psk = norm in ctx.psk_heard_us
    return 1 if (in_marine and in_psk) else 0


def _tier_marine(d: DecodedMsg, ctx: MachineContext) -> int:
    """Marinefunker (egal ob PSK-Bestätigung)."""
    if not d.call_from:
        return 0
    return 1 if d.call_from.upper() in ctx.marine_calls else 0


def _tier_new_dxcc_psk(d: DecodedMsg, ctx: MachineContext) -> int:
    """Neues DXCC + PSK sagt 'hört uns'."""
    if not d.call_from:
        return 0
    norm = d.call_from.upper()
    is_new = norm in ctx.new_dxcc_calls
    in_psk = norm in ctx.psk_heard_us
    return 1 if (is_new and in_psk) else 0


def _tier_new_dxcc(d: DecodedMsg, ctx: MachineContext) -> int:
    """Neues DXCC (auch ohne PSK)."""
    if not d.call_from:
        return 0
    return 1 if d.call_from.upper() in ctx.new_dxcc_calls else 0


def _tier_psk_heard_us(d: DecodedMsg, ctx: MachineContext) -> int:
    """Reine PSK-Reciprocity (egal welches Land)."""
    if not d.call_from:
        return 0
    return 1 if d.call_from.upper() in ctx.psk_heard_us else 0


def _tier_psk_snr(d: DecodedMsg, ctx: MachineContext) -> int:
    """Graduelles PSK-Reciprocity-Signal.

    Hoeher ist besser. Fehlt der PSK-Wert, bekommt der Call einen sehr
    niedrigen Score, damit ein echter -18 dB Report immer wertvoller ist als
    ein unbekannter Wert.
    """
    if not d.call_from:
        return 0
    norm = d.call_from.upper()
    b = base_call(norm)
    val = ctx.psk_snr.get(norm)
    if val is None and b:
        val = ctx.psk_snr.get(b)
    return int(val) if val is not None else -99


def _tier_new_dxcc_band(d: DecodedMsg, ctx: MachineContext) -> int:
    """5BWAS: DXCC haben wir, aber auf DIESEM Band noch nicht."""
    if not d.call_from:
        return 0
    norm = d.call_from.upper()
    entity = ctx.call_to_dxcc.get(norm)
    if not entity:
        return 0
    if (entity, ctx.band) in ctx.worked_dxcc_band:
        return 0
    return 1


def _tier_new_grid(d: DecodedMsg, ctx: MachineContext) -> int:
    """Neues Grid-Quadrat überhaupt (Maidenhead Award).

    Der CQ-Decoder hat das Grid bereits aus dem Message-Text extrahiert
    ("CQ DK9XR JN58" → grid="JN58"). Wenn der CQ kein Grid trägt
    (manche compound-Calls oder Free-Text-CQs), liefert der Tier 0.
    """
    if not d.grid:
        return 0
    g4 = d.grid[:4].upper()
    if len(g4) != 4:
        return 0
    return 0 if g4 in ctx.worked_grids else 1


def _tier_new_grid_band(d: DecodedMsg, ctx: MachineContext) -> int:
    """Grid haben wir, aber auf DIESEM Band noch nicht (VUCC-Band-Variation)."""
    if not d.grid:
        return 0
    g4 = d.grid[:4].upper()
    if len(g4) != 4:
        return 0
    return 0 if (g4, ctx.band) in ctx.worked_grid_band else 1


def _tier_not_worked(d: DecodedMsg, ctx: MachineContext) -> int:
    """Call noch nie gearbeitet (allgemein, kein Band-Kriterium)."""
    if not d.call_from:
        return 0
    return 0 if d.call_from.upper() in ctx.worked else 1


def _tier_dxcc_rarity(d: DecodedMsg, ctx: MachineContext) -> int:
    """Rarity-Score 0..100 aus dxcc_rarity-Tabelle (höher = seltener)."""
    if not d.call_from:
        return 0
    return ctx.rarity_scores.get(d.call_from.upper(), 0)


def _maidenhead_to_latlon(grid: str) -> tuple[float, float]:
    """4-stelliges Maidenhead → (lat, lon) Center.

    Locator JN58 → ~48.5N, 9.0E (Square-Center).
    """
    g = grid.upper().strip()
    if len(g) < 4:
        raise ValueError(f"grid too short: {grid!r}")
    lon = (ord(g[0]) - ord("A")) * 20 - 180
    lat = (ord(g[1]) - ord("A")) * 10 - 90
    lon += int(g[2]) * 2
    lat += int(g[3])
    if len(g) >= 6:
        lon += (ord(g[4]) - ord("A")) * 5 / 60 + 2.5 / 60
        lat += (ord(g[5]) - ord("A")) * 2.5 / 60 + 1.25 / 60
    else:
        lon += 1
        lat += 0.5
    return lat, lon


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Grosskreisdistanz in km, ohne Runtime-Abhaengigkeit auf util.maidenhead."""
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _tier_grayline(d: DecodedMsg, ctx: MachineContext) -> int:
    """v0.14.0 — CQ-Rufer ist gerade im eigenen Grayline-Fenster.

    Lat/Lon kommt aus ctx.call_to_latlon (vom Orchestrator pro Slot
    gefuellt aus cty.dat-Lookup). Wenn unbekannt → 0. Grayline via
    util.propagation.is_in_grayline mit ±6° Civil-Twilight-Fenster.

    Effekt: 80m/40m/15m-DX-Calls die in ihrer eigenen Daemmerung sind
    bekommen Pickup-Vorrang. Klassische Grayline-Propagation.
    """
    if not d.call_from:
        return 0
    pos = ctx.call_to_latlon.get(d.call_from.upper())
    if pos is None:
        return 0
    from ..util.propagation import is_in_grayline
    lat, lon = pos
    try:
        return 1 if is_in_grayline(lat, lon, datetime.now(UTC)) else 0
    except Exception:
        return 0


def _tier_band_open(d: DecodedMsg, ctx: MachineContext) -> int:
    """v0.14.0 — hamqsl meldet aktuell "Good"-Conditions auf dem Band.

    Day/Night-Auswahl basiert auf der Sonne ueber UNSEREM QTH (my_grid).
    Effekt: bei offenem Band kriegen ALLE CQ-Calls +1, was sich
    aufaddiert mit anderen Tiers. Bei Fair/Poor → 0.
    """
    if not ctx.band_conditions_day and not ctx.band_conditions_night:
        return 0
    try:
        my_lat, my_lon = _maidenhead_to_latlon(ctx.my_grid)
    except Exception:
        return 0
    from ..util.propagation import is_band_open_for_dx
    try:
        return 1 if is_band_open_for_dx(
            ctx.band,
            ctx.band_conditions_day,
            ctx.band_conditions_night,
            my_lat=my_lat, my_lon=my_lon, when=datetime.now(UTC),
        ) else 0
    except Exception:
        return 0


def _tier_not_in_pileup(d: DecodedMsg, ctx: MachineContext) -> int:
    """v0.19.0 — Inverse-Filter: 0 wenn der Call wahrscheinlich in einem
    Pile-Up steckt (rare DX + viele Caller auf seiner Frequenz), sonst 1.

    Pile-Up-Erfolgsrate fuer ein Klasse-E QRP-Setup ist <5%. Statt
    TX-Energie zu verschwenden lieber 3-5 min warten bis der Pile-Up
    sich legt — dann nochmal versuchen. Die Detection-Heuristik liegt
    im Orchestrator (ctx.pile_up_calls); dieser Tier ist nur die
    Picker-Seite.

    Wie not_bad_reputation/not_his_tx_slot: defensive 1 (kein Filter)
    fuer Calls ohne call_from.
    """
    if not d.call_from:
        return 1
    call = d.call_from.upper()
    # v0.44.0 — Ausnahme: ein NEUES DXCC ist den Pile-Up-Kampf wert.
    # Seltenheit schlaegt QRP-Effizienz; die Headless-Box soll auf den
    # Sonderling losgehen statt ihn wegen Pile-Up zu meiden. Greift nur
    # fuer echte neue DXCC (selten) — normale Calls werden weiter gemieden.
    if call in ctx.new_dxcc_calls:
        return 1
    return 0 if call in ctx.pile_up_calls else 1


def _tier_continent_prior(d: DecodedMsg, ctx: MachineContext) -> int:
    """2026-09-07 — lernende Prior aus der eigenen pick_attempt-Telemetrie:
    1, wenn der Kontinent des Ziels eine mindestens durchschnittliche
    Vollendungsquote hat (oder unbekannt ist), sonst 0. Am 6.9.: EU 14 %,
    AS 8 %, AF 7 %. Wirkt nur bei mehreren Kandidaten."""
    if not d.call_from:
        return 0
    if not ctx.continent_success:
        return 1
    cont = ctx.call_to_continent.get(d.call_from.upper())
    if not cont or cont not in ctx.continent_success:
        return 1
    return 1 if ctx.continent_success[cont] >= ctx.continent_success_overall else 0


def _tier_lonely_cq(d: DecodedMsg, ctx: MachineContext) -> int:
    """2026-09-06 — 1, wenn der Rufer schon im Slot davor (gleiche
    Paritaet) CQ gerufen hat und dazwischen niemand ihn angerufen hat.
    Pick-Telemetrie am 6.9.: 55 Picks, 6 vollendet, 31 "went silent" —
    die Rufer hatten meist schon einen anderen Partner. Wer zweimal
    hintereinander unbeantwortet ruft, hat keinen Pile-Up und hoert zu.
    Die Erkennung liegt im Orchestrator (detect_lonely_cqs)."""
    if not d.call_from:
        return 0
    return 1 if d.call_from.upper() in ctx.lonely_cq_calls else 0


def _tier_buddy_seen(d: DecodedMsg, ctx: MachineContext) -> int:
    """v0.17.0 — Call ist global worked (wir wissen er hoert uns) ABER
    nicht auf DIESEM Band gearbeitet → +Boost.

    Begruendung: ein bestehendes QSO ist Beweis dass der RX-Pfad
    bilateral funktioniert. Bei anderem Band ist nur die Propagation
    anders, das Equipment auf beiden Seiten bleibt gleich. Hoehere
    Erfolgsrate als Cold-Calls aus dem gleichen DXCC.
    """
    if not d.call_from:
        return 0
    norm = d.call_from.upper()
    if norm not in ctx.worked:
        return 0
    if (norm, ctx.band) in ctx.worked_call_band:
        return 0  # auf diesem Band bereits → kein Buddy-Seen-Boost
    return 1


def _tier_active_hour(d: DecodedMsg, ctx: MachineContext) -> int:
    """v0.16.0 — Aktuelle UTC-Stunde ist historisch aktiv fuer den
    Continent des CQ-Rufers.

    Continent kommt aus ctx.call_to_continent (vom Orchestrator pro Slot
    aus cty.dat befuellt). active_continent_hours ist die Set-Repraesen-
    tation der Top-50%-Stunden pro Continent aus eigener QSO-DB.

    Effekt: VK morgens auf 15m aktiv → Boost. Mittagspause auf 15m fuer
    VK → kein Boost (andere Tiers entscheiden).
    """
    if not d.call_from or not ctx.active_continent_hours:
        return 0
    continent = ctx.call_to_continent.get(d.call_from.upper())
    if not continent:
        return 0
    hour = datetime.now(UTC).hour
    return 1 if (continent, hour) in ctx.active_continent_hours else 0


def _tier_not_bad_reputation(d: DecodedMsg, ctx: MachineContext) -> int:
    """v0.15.0 — Soft-Blacklist-Aware: 0 fuer Calls die wir mehrfach
    erfolglos angerufen haben (Reputation-Score >= 5), sonst 1.

    Da andere Tiers bei Match 1 liefern, wirkt der 0-Wert hier wie ein
    Filter: bad-reputation Calls landen IMMER schlechter im lex-Score
    als ein neutraler Call. Ohne ihn als Tier ganz oben in
    hunt_priority zu legen, kann der User entscheiden ob der Effekt
    hart (oben) oder weich (mittig) wirkt.
    """
    if not d.call_from:
        return 1  # unknown → kein Filter
    return 0 if base_call(d.call_from) in ctx.soft_blacklist else 1


def _tier_not_his_tx_slot(d: DecodedMsg, ctx: MachineContext) -> int:
    """v0.15.0 — Slot-Parity-Awareness: 0 wenn der Op gerade in SEINEM
    eigenen TX-Slot ist (= er sendet, er hoert uns nicht), sonst 1.

    Wir tracken pro Call welche Slot-Parity er typischerweise zum
    Senden nutzt (siehe Orchestrator). Wenn unser aktueller Slot SEIN
    TX-Slot ist und wir antworten wollen, ist das verschwendete TX-
    Energie. Wir warten lieber einen Slot.
    """
    if not d.call_from or not ctx.current_slot_parity:
        return 1
    his_parity = ctx.op_slot_parity.get(d.call_from.upper())
    if his_parity is None or his_parity == "":
        return 1  # unknown → kein Filter
    # Wenn unser aktueller Slot SEIN TX-Slot ist → er sendet jetzt,
    # er hoert uns nicht. Picker soll ihn nicht jetzt picken — wir
    # wuerden in den naechsten Slot antworten waehrend er noch sendet.
    return 0 if his_parity == ctx.current_slot_parity else 1


def _tier_snr(d: DecodedMsg, ctx: MachineContext) -> int:
    """SNR als Tie-Breaker — bestes Signal gewinnt."""
    return d.snr_db if d.snr_db is not None else -99


# v0.11.0 Tail-End-Hunter
TAIL_END_COOLDOWN_S = 24 * 3600  # 24h pro Station


def _tier_tail_end_target(d: DecodedMsg, ctx: MachineContext) -> int:
    """Station hat in den letzten 30 s ein Closing (RR73/RRR/73)
    gesendet → sie ist gleich frei wie nach einem CQ. Wir koennen
    sie direkt anrufen statt zu warten bis ihr naechster CQ kommt.

    Cooldown 24h pro Station (Sebastian-Wunsch): wir wollen nicht
    denselben Op an einem Tag mehrfach per Tail-End anrufen.
    """
    if not d.call_from:
        return 0
    norm = d.call_from.upper()
    if norm not in ctx.tail_end_candidates:
        return 0
    # 24h-Cooldown — wenn wir den schon mal per Tail-End gepickt haben,
    # nicht nochmal heute.
    last = ctx.tail_end_last_pick.get(norm)
    if last is not None:
        now = datetime.now(UTC).timestamp()
        if now - last < TAIL_END_COOLDOWN_S:
            return 0
    return 1


# Registry: name → scoring function. Unbekannte Namen werden in
# _compute_tier_score() ignoriert (defensive, kein KeyError bei Tippfehler).
HUNT_TIERS: dict[str, callable] = {  # type: ignore[type-arg]
    "not_bad_reputation": _tier_not_bad_reputation,  # v0.15.0
    "not_his_tx_slot":    _tier_not_his_tx_slot,     # v0.15.0
    "not_in_pileup":      _tier_not_in_pileup,       # v0.19.0
    "marine_psk":      _tier_marine_psk,
    "marine":          _tier_marine,
    "tail_end_target": _tier_tail_end_target,
    "grayline":        _tier_grayline,
    "band_open":       _tier_band_open,
    "active_hour":     _tier_active_hour,   # v0.16.0
    "buddy_seen":      _tier_buddy_seen,    # v0.17.0
    "new_dxcc_psk":    _tier_new_dxcc_psk,
    "new_dxcc":        _tier_new_dxcc,
    "continent_prior": _tier_continent_prior, # 2026-09-07
    "lonely_cq":       _tier_lonely_cq,       # 2026-09-06
    "psk_heard_us":    _tier_psk_heard_us,
    "psk_snr":         _tier_psk_snr,
    "new_dxcc_band":   _tier_new_dxcc_band,
    "new_grid":        _tier_new_grid,
    "new_grid_band":   _tier_new_grid_band,
    "not_worked":      _tier_not_worked,
    "dxcc_rarity":     _tier_dxcc_rarity,
    "snr":             _tier_snr,
}


def _compute_tier_score(d: DecodedMsg, ctx: MachineContext) -> tuple[int, ...]:
    """Kaskadierender Score nach ctx.hunt_priority-Reihenfolge.

    Unbekannte Tier-Namen werden übersprungen (kein Crash). Wenn die
    Liste leer ist (oder nur Unbekanntes enthält), fällt der Score auf
    (snr,) zurück — sonst gibt's keinen Tie-Breaker.
    """
    priority = ctx.hunt_priority or []
    parts: list[int] = []
    for name in priority:
        fn = HUNT_TIERS.get(name)
        if fn is not None:
            parts.append(fn(d, ctx))
    # Sicherer Fallback: wenn niemand SNR als Tie-Breaker drin hatte,
    # häng's hinten an — wir wollen nicht zufällig den ersten Decode
    # aus einer gleich-priorisierten Gruppe nehmen.
    if "snr" not in priority:
        parts.append(_tier_snr(d, ctx))
    return tuple(parts)


# ---------------------------------------------------------------------------
# Outgoing actions the controller has to execute
ActionKind = Literal[
    "TX_MESSAGE", "STOP_TX", "LOG_QSO", "TX_LOCKED",
    # v0.15.0 — Bail-Notification fuer Reputation-Tracking + Slot-Parity-Tracking
    "QSO_BAIL",
]


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
    qso_failed_cooldown_went_silent_multiplier: float = 2.0
    qso_failed_cooldown_repeat_multiplier: float = 1.7
    qso_failed_cooldown_max_s: float = 3600.0
    qso_report_extra_resends: int = 0
    qso_report_extra_resend_snr_db: int = -12
    qso_report_extra_resend_psk_snr_db: int = -10
    # v0.64.0 — Picker-Diagnose vom letzten _pick_hunt_target (reine
    # Telemetrie): {winning_tier, n_candidates, was_tailend}. Wird direkt
    # nach dem Pick in die hunt_attempt_meta uebernommen.
    _last_pick_diag: dict = field(default_factory=dict)

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
        self.ctx.cq_fallback_active = False
        self.ctx.idle_slots_without_pick = 0
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
        self.ctx.last_lock_code = None
        self.ctx.last_lock_params = None
        if self.state is State.TX_LOCKED:
            self.state = State.IDLE

    def on_user_reply_to(
        self, hw: HardwareState, decoded: DecodedMsg
    ) -> None:
        """User picked a CQ to answer (Hunting / S&P)."""
        if not self._check_guards(hw):
            return
        if not _is_usable_partner_call(decoded.call_from):
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

    def on_user_tail_end(
        self, hw: HardwareState, closing_decode: DecodedMsg
    ) -> None:
        """User klickt 🎯 auf einen RR73/RRR/73-Decode in der UI → wir
        rufen den Sender (call_from) direkt an wie nach einem CQ.

        Manueller Override: ignoriert 5-min-Recent-CQ-Filter UND
        bestehenden 24h-Cooldown — wenn Sebastian klickt weiss er was
        er tut. Setzt aber tail_end_last_pick danach, damit der
        automatische Hunter nicht im selben oder naechsten Slot
        denselben Call nochmal pickt.

        v0.12.0 Companion zur automatischen Detection (v0.11.0).
        """
        if not self._check_guards(hw):
            return
        if not _is_usable_partner_call(closing_decode.call_from):
            return
        # Defensive: Closing-Self-Reply waere sinnlos
        if closing_decode.call_from.upper() == self.ctx.tx_callsign.upper():
            log.warning("on_user_tail_end ignored — closing is from us")
            return
        # Wenn das Closing an UNS gerichtet ist, ist's unser eigener
        # QSO-Partner. Tail-End-Pickup hier ist redundant — der Standard-
        # Cooldown via _bail oder LOG_QSO greift sowieso.
        if closing_decode.call_to and closing_decode.call_to.upper() == self.ctx.tx_callsign.upper():
            log.warning("on_user_tail_end ignored — closing addressed to us")
            return
        self.qso = QsoContext(
            their_call=closing_decode.call_from,
            their_grid=closing_decode.grid,
            band=closing_decode.band,
            freq_offset_hz=closing_decode.freq_offset_hz or 1500,
            their_snr_at_us=closing_decode.snr_db,
        )
        self.state = State.QSO_RESPOND
        self._emit_respond_with_grid()
        # 24h-Cooldown setzen — siehe _pick_hunt_target. Damit der
        # Auto-Picker nicht 1 Slot spaeter denselben Call nochmal
        # auf demselben Pfad pickt.
        norm = closing_decode.call_from.upper()
        self.ctx.tail_end_last_pick[norm] = datetime.now(UTC).timestamp()
        log.info(
            "User-Tail-End: %s (Closing an %s, freq=%s, 24h-Cooldown gesetzt)",
            norm, closing_decode.call_to or "?", closing_decode.freq_offset_hz,
        )

    def _hunt_respond_to_best(self, hw: HardwareState, decodes: list[DecodedMsg]) -> bool:
        """PRIO 2 des Huntings: staerksten brauchbaren CQ picken und antworten.
        True, wenn ein Pick passiert ist (State QSO_RESPOND). 2026-09-07 aus
        on_decodes ausgegliedert, damit der CQ-Fallback denselben Pfad nutzt."""
        # PRIO 2 — kein Direkt-Caller, picke staerksten CQ.
        best = self._pick_hunt_target(decodes)
        if best is not None:
            if not self._check_guards(hw):
                return False
            # Antwortfrequenz (2026-09-06): auf dem ruhigsten Bin statt
            # exakt auf der des Rufers — dort stapeln sich die anderen
            # Anrufer. Der Rufer dekodiert das ganze Passband.
            reply_hz = best.freq_offset_hz or 1500
            reply_kind = "on_freq"
            use_quiet = self.ctx.hunt_reply_quiet_freq
            if self.ctx.hunt_reply_ab_test:
                # 2026-09-07 A/B: abwechselnd ruhiger Bin / Rufer-Frequenz,
                # pick_attempt.reply_kind entscheidet spaeter mit Zahlen.
                self.ctx.reply_ab_counter += 1
                use_quiet = (self.ctx.reply_ab_counter % 2 == 0)
            # Randfrequenz: das A/B ist hier ausgesetzt. Auf seiner Frequenz
            # zu antworten hiesse in den Rig-Bandpass zu senden — genau der
            # Fall, gegen den der alte Picker-Filter gebaut war.
            am_rand = self._freq_outside_tx_window(best)
            if am_rand:
                use_quiet = True
            if use_quiet:
                quiet = self._next_cq_freq_hz()
                if quiet:
                    log.info("hunt reply on quiet bin %d Hz (CQ at %d Hz)",
                             quiet, reply_hz)
                    reply_hz = quiet
                    reply_kind = "quiet_edge" if am_rand else "quiet"
                elif am_rand:
                    # Ohne ruhigen Bin lieber Bandmitte als der Rand.
                    reply_hz = 1500
                    reply_kind = "quiet_edge"
            self.qso = QsoContext(
                their_call=best.call_from or "?",
                their_grid=best.grid,
                band=best.band,
                freq_offset_hz=reply_hz,
                their_snr_at_us=best.snr_db,
            )
            # v0.30.0 — Pick-Attempt-Telemetrie (reine Messung, kein
            # Logik-Einfluss): festhalten ob dieses Hunt-Ziel uns laut
            # PSK-Reciprocity gehoert hat, + SNR/DT/Band zum Pick-Zeit-
            # punkt. Orchestrator schreibt beim Ausgang die pick_attempt-
            # Zeile. base_call als Key passend zu LOG_QSO/QSO_BAIL.
            tgt = base_call(best.call_from)
            if tgt:
                # Cap gegen Leak durch Picks die nie LOG_QSO/QSO_BAIL
                # ausloesen (selten, aber moeglich).
                if len(self.ctx.hunt_attempt_meta) > 100:
                    self.ctx.hunt_attempt_meta.clear()
                call_u = (best.call_from or "").upper()
                # v0.62.0 — Alter des gepickten Decodes (s) zum Pick-
                # Zeitpunkt. Defensiv: best.ts kann theoretisch naiv sein.
                _now = datetime.now(UTC)
                pick_age_s: float | None
                try:
                    _bts = best.ts
                    if _bts.tzinfo is None:
                        _bts = _bts.replace(tzinfo=UTC)
                    pick_age_s = (_now - _bts).total_seconds()
                except Exception:
                    pick_age_s = None
                # War der gepickte Decode ein CQ / an uns / an wen anders?
                _ct = (best.call_to or "").upper()
                if not _ct:
                    pick_kind = "cq"
                elif _ct in (
                    (self.ctx.callsign or "").upper(),
                    (self.ctx.tx_callsign or "").upper(),
                ):
                    pick_kind = "to_us"
                else:
                    pick_kind = "to_other"
                self.ctx.hunt_attempt_meta[tgt] = {
                    "psk_heard_us": tgt in self.ctx.psk_heard_us
                    or call_u in self.ctx.psk_heard_us,
                    # v0.31.0 weitere Mess-Dimensionen (reine Telemetrie):
                    "was_worked": call_u in self.ctx.worked
                    or tgt in self.ctx.worked,
                    "was_new_dxcc": (best.call_from or "") in self.ctx.new_dxcc_calls,
                    "n_decodes": len(decodes),
                    "snr_db": best.snr_db,
                    "dt_s": best.dt_s,
                    "band": best.band,
                    "ts": _now,
                    "pick_age_s": pick_age_s,
                    "pick_kind": pick_kind,
                    "reply_kind": reply_kind,
                    "freq_offset_hz": best.freq_offset_hz,
                    "target_grid": best.grid,
                    # v0.64.0 — Picker-Diagnose + Kontext:
                    "winning_tier": self._last_pick_diag.get("winning_tier"),
                    "n_candidates": self._last_pick_diag.get("n_candidates"),
                    "was_tailend": self._last_pick_diag.get("was_tailend"),
                    "hunt_priority": ",".join(self.ctx.hunt_priority or []) or None,
                    "psk_snr": self.ctx.psk_snr.get(call_u)
                    if call_u in self.ctx.psk_snr
                    else self.ctx.psk_snr.get(tgt),
                }
            self.state = State.QSO_RESPOND
            self._emit_respond_with_grid()
            return True
        return False

    def on_decodes(self, hw: HardwareState, decodes: Iterable[DecodedMsg]) -> None:
        decodes = list(decodes)
        self.last_decodes = decodes

        # v0.11.0 Tail-End-Hunter: bei jedem Slot CQ-Tracking + Closing-
        # Detection auffrischen. Laeuft VOR der State-Machine-Logik damit
        # ein Closing das im selben Slot wie ein anderer CQ kommt sofort
        # als Candidate verfuegbar ist falls wir in IDLE+auto_answer
        # gerade in den Picker fallen.
        if self.ctx.tail_end_hunter_enabled:
            self._update_tail_end_state(decodes)

        # Hunting mode: when idle and auto_answer is on.
        if self.state is State.IDLE and self.ctx.auto_answer and not self.ctx.drain_for_update:
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
            # Vorrang vor allem anderen: ein abgebrochenes QSO, das sich
            # doch noch meldet, gehoert zu Ende gebracht (Loch A/C).
            if self._try_resume_recent_qso(hw, decodes):
                return
            te = _find_answer_with_report_to_us(decodes, self.ctx.tx_callsign)
            if te is not None:
                their_snr, decoded = te
                their_call = decoded.call_from or ""
                cd = max(
                    self.ctx.recent_until.get(their_call, 0.0),
                    self.ctx.worked_until.get((their_call, decoded.band or ""), 0.0),
                )
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
                    self._note_inbound_attempt(decoded, decodes, "inbound_report")
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
            ans = _find_answer_to_us(decodes, self.ctx.tx_callsign)
            if ans is not None:
                their_call = ans.call_from or ""
                cd = max(
                    self.ctx.recent_until.get(their_call, 0.0),
                    self.ctx.worked_until.get((their_call, ans.band or ""), 0.0),
                )
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
                    self._note_inbound_attempt(ans, decodes, "inbound_grid")
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
            if self._hunt_respond_to_best(hw, decodes):
                self.ctx.idle_slots_without_pick = 0
                return
            self.ctx.idle_slots_without_pick += 1
            return
        if self.state is State.CQ_CALLING:
            # Auch waehrend wir CQ rufen: ein abgebrochenes QSO, das sich
            # nachtraeglich meldet, zuerst zu Ende bringen (Loch A/C).
            if self._try_resume_recent_qso(hw, decodes):
                return
            was_fallback = self.ctx.cq_fallback_active
            # Tail-Ender first: someone answered our CQ with a direct
            # signal report (skipping the grid stage). When that happens
            # we jump straight to QSO_REPORT and send the R-report,
            # cutting two slots out of the QSO. Architecture §6.6.
            te = _find_answer_with_report_to_us(decodes, self.ctx.tx_callsign)
            if te is not None:
                if not self._check_guards(hw):
                    return
                their_snr, decoded = te
                self._note_inbound_attempt(decoded, decodes, "inbound_report")
                self.qso = QsoContext(
                    their_call=decoded.call_from or "?",
                    their_grid=None,
                    band=decoded.band,
                    freq_offset_hz=decoded.freq_offset_hz or 1500,
                    our_snr_received=their_snr,
                    their_snr_at_us=decoded.snr_db,
                    from_cq_fallback=was_fallback,
                )
                self.ctx.cq_fallback_active = False
                if was_fallback:
                    self.ctx.cq_fallback_unanswered_rounds = 0   # Antwort: Backoff zurueck
                self.state = State.QSO_REPORT
                self._emit_send_r_report()
                return

            # Normal sequence: someone answered us with their grid.
            ans = _find_answer_to_us(decodes, self.ctx.tx_callsign)
            if ans is not None:
                if not self._check_guards(hw):
                    return
                # Grid-Antwort = noch kein Report von ihnen, our_snr_received
                # bleibt None. their_snr_at_us aus unserer Decode-Messung.
                self._note_inbound_attempt(ans, decodes, "inbound_grid")
                self.qso = QsoContext(
                    their_call=ans.call_from or "?",
                    their_grid=ans.grid,
                    band=ans.band,
                    freq_offset_hz=ans.freq_offset_hz or 1500,
                    their_snr_at_us=ans.snr_db,
                    from_cq_fallback=was_fallback,
                )
                self.ctx.cq_fallback_active = False
                if was_fallback:
                    self.ctx.cq_fallback_unanswered_rounds = 0   # Antwort: Backoff zurueck
                self.state = State.QSO_RESPOND
                self._emit_respond_with_report()
            # 2026-09-07 CQ-Fallback: taucht ein brauchbarer Rufer auf, hat
            # das Antworten Vorrang vor dem eigenen CQ.
            if was_fallback and self.state is State.CQ_CALLING and self.ctx.auto_answer \
                    and self._hunt_respond_to_best(hw, decodes):
                self.ctx.cq_fallback_active = False
                self.ctx.idle_slots_without_pick = 0

        elif self.state is State.QSO_RESPOND and self.qso is not None:
            # Tracking: bei jedem Decode des Partners their_snr_at_us
            # auf den aktuellsten Wert ziehen (Sebastian Audit Action 5,
            # WSJT-X-Konformanz fuer R-Report). Nutzt den SNR den WIR
            # gemessen haben, nicht den den er uns gemeldet hat.
            self._track_partner_snr(decodes)
            # Haben sie unseren Report mit R-Report bestaetigt (Tx3)? Dann
            # ist das QSO auf ihrer Seite komplett und ihnen fehlt nur noch
            # unser RR73 — genau wie ein eingehendes RR73 in QSO_REPORT.
            # Ohne diesen Zweig wartete die Box auf einen Report ohne R,
            # der nach einem R-Report nie mehr kommt, und lief in den
            # Timeout (s. _find_r_report_from_them).
            r_rep = _find_r_report_from_them(
                decodes, self.qso.their_call, self.ctx.tx_callsign
            )
            if r_rep is not None:
                self.qso.our_snr_received = r_rep
                self.qso.stale_slots = 0
                self.state = State.QSO_LOG
                self._emit_log_qso(hw)
                return
            # Loch B (2026-09-10): Manche Partner ueberspringen den R-Report
            # und schliessen direkt ab. Fuer sie ist das QSO damit fertig —
            # wir warteten auf einen Report, der nie mehr kommt, und liefen
            # in den Timeout.
            if _find_closing(decodes, self.qso.their_call, self.ctx.tx_callsign):
                self.qso.stale_slots = 0
                self.state = State.QSO_LOG
                self._emit_log_qso(hw)
                return
            # did they send us a report?
            rep = _find_report_from_them(decodes, self.qso.their_call, self.ctx.tx_callsign)
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
                    and d.call_to != self.ctx.tx_callsign
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
                        # v0.18.0 — vor finalem Bail einmalig Audio-Freq
                        # hopen. Vielleicht haben wir QRM bei IHM auf
                        # unserer Freq; auf ±200 Hz funkt's vielleicht.
                        if not self.qso.freq_hopped_once:
                            if not self._check_guards(hw):
                                return
                            old_freq = self.qso.freq_offset_hz
                            self.qso.freq_offset_hz = self._hop_audio_freq(old_freq)
                            self.qso.freq_hopped_once = True
                            self.qso.stale_slots = 0
                            log.info(
                                "QSO_RESPOND: %s ignored us %d× — Freq-Hop %d → %d Hz",
                                their_call, self.qso.cq_resends,
                                old_freq, self.qso.freq_offset_hz,
                            )
                            self._emit_respond_with_grid()
                            return
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
            if _find_closing(decodes, self._grace_partner_call, self.ctx.tx_callsign):
                if not self._check_guards(hw):
                    return
                log.info(
                    "QSO_GRACE: %s repeated RR73 → 73 hinterher zur Closure",
                    self._grace_partner_call,
                )
                msg = f"{self._grace_partner_call} {self.ctx.tx_callsign} 73"
                # freq_offset_hz kennt der Grace-State nicht mehr direkt
                # (qso wurde in _emit_log_qso auf None gesetzt), default
                # tx_payload nutzt CQ_DEFAULT — fuer ein einzelnes 73
                # akzeptabel. Alternative: freq in _grace_partner_call
                # mitschleppen, aber das ist Overhead fuer 1-Slot-Edge.
                self._pending.append(
                    Action("TX_MESSAGE", self._tx_payload(msg, "ack73"))
                )
                self._exit_grace(hw)

        elif self.state is State.QSO_REPORT and self.qso is not None:
            # Tracking (siehe Audit Action 5): aktualisiere their_snr_at_us
            # bei jedem Partner-Decode auch in QSO_REPORT damit ein R-Resend
            # mit dem neuesten Wert sendet.
            self._track_partner_snr(decodes)
            # did they send RR73 / 73 ?
            if _find_closing(decodes, self.qso.their_call, self.ctx.tx_callsign):
                self.qso.stale_slots = 0
                self.state = State.QSO_LOG
                self._emit_log_qso(hw)
                return
            # Loch D (2026-09-10): Er wiederholt seinen R-Report, weil er
            # unseren nicht gehoert hat. Beide Seiten haben damit bestaetigt
            # — das QSO ist komplett, einer muss abschliessen. Bisher fiel
            # das durch, weil _find_report_from_them R-Reports ausfiltert:
            # kein Fortschritt erkannt, Timeout nach drei Slots.
            r_rep_again = _find_r_report_from_them(
                decodes, self.qso.their_call, self.ctx.tx_callsign
            )
            if r_rep_again is not None:
                self.qso.our_snr_received = r_rep_again
                self.qso.stale_slots = 0
                self.state = State.QSO_LOG
                self._emit_log_qso(hw)
                return
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
                and d.call_to != self.ctx.tx_callsign
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
                decodes, their_call, self.ctx.tx_callsign
            )
            them_cq_again = any(
                d.call_from == their_call
                and (d.message or "").startswith("CQ")
                for d in decodes
            )
            if rep_again is not None or them_cq_again:
                reason = "repeated report" if rep_again is not None else "repeated CQ"
                report_limit = self._effective_report_resend_limit()
                if self.qso.report_resends >= report_limit:
                    # Schon resent — sie hoeren uns einfach nicht.
                    # In Timeout laufen lassen.
                    log.info(
                        "QSO_REPORT: %s %s %d× (max %d) — kein R-Resend mehr",
                        their_call,
                        reason,
                        self.qso.report_resends,
                        report_limit,
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
                        report_limit,
                    )
                    self.qso.stale_slots = 0
                    self._emit_send_r_report()

    def on_slot_tick(self, hw: HardwareState, tick: SlotTick | None = None) -> None:
        # v0.11.0 — Tail-End-Candidates altert: jeder Slot pruefen ob
        # die 30-s-Expiry abgelaufen ist. Laeuft auch wenn der Toggle
        # aus ist (defensiv — falls jemand wechselt waehrend Candidates
        # noch im Dict stehen).
        if (self.ctx.tail_end_candidates
                or self.ctx.tail_end_recent_cq
                or self.ctx.pre_staged_tail_ends):
            self._prune_tail_end_state()

        # Boot-Auto-Resume (Sebastian 2026-05-23 nach Multi-Operator-
        # Refactor): wenn auto_cq beim Boot via boot_mode=cq gesetzt
        # wurde aber state noch IDLE (kein User-Event seit Restart),
        # transition zu CQ_CALLING im naechsten Slot. Damit ueberlebt
        # der CQ-Modus einen Service-Restart automatisch.
        if self.state is State.IDLE:
            self.ctx.cq_fallback_active = False
        if (
            self.state is State.IDLE
            and self.ctx.auto_cq
            and self._check_guards(hw)
        ):
            log.info("on_slot_tick: auto_cq=True bei IDLE → CQ_CALLING")
            self.state = State.CQ_CALLING
        elif (
            self.state is State.IDLE
            and self.ctx.auto_answer
            and self.ctx.hunt_cq_fallback
            and self.ctx.idle_slots_without_pick >= self.ctx.hunt_cq_fallback_after_slots
            and (tick is None or tick.posix >= self.ctx.cq_fallback_paused_until)
            and not self.ctx.drain_for_update
            and self._check_guards(hw)
        ):
            # 2026-09-07: kein brauchbarer Rufer seit N Slots -> selbst CQ,
            # bis wieder einer da ist (on_decodes bricht ab, sobald der
            # Picker etwas findet).
            log.info("Hunting: %d Slots ohne brauchbaren Rufer → CQ-Fallback",
                     self.ctx.idle_slots_without_pick)
            self.ctx.cq_fallback_active = True
            self.ctx.cq_fallback_starts += 1
            self.ctx.idle_slots_without_pick = 0
            self.ctx.cq_count = 0
            self.state = State.CQ_CALLING

        # In CQ_CALLING senden wir nur in der konfigurierten Slot-
        # Haelfte (even oder odd). Der andere Slot bleibt frei zum
        # Empfang von Antwortern. Sebastian sah 2026-05-23 dass ohne
        # diesen Filter TX in JEDEM Slot stattfand → halbduplex Rig
        # immer im TX → 0 RX-Decodes ueber 34 min trotz laufendem
        # Decoder. Funkstille-Watchdog feuerte voellig zu Recht.
        if self.state is State.CQ_CALLING:
            if self.ctx.drain_for_update:
                # 2026-09-07: CQ ist kein QSO — fuer das Update sofort aufhoeren
                log.info("CQ pausiert: Self-Update wartet")
                self.ctx.cq_fallback_active = False
                self.state = State.IDLE
                return
            # Guards laufen IMMER (auch in RX-Slots) — sonst wuerde bei
            # SWR-Spike/GPS-Loss im RX-Slot keine TX_LOCKED-Transition
            # ausgeloest und der naechste TX-Slot wuerde unsicher senden.
            if not self._check_guards(hw):
                return
            # 2026-09-07: Fallback-Deckel — nach N unbeantworteten CQs Pause,
            # ein totes Band wird nicht endlos angerufen.
            if (self.ctx.cq_fallback_active
                    and self.ctx.cq_count >= self.ctx.hunt_cq_fallback_max_cqs):
                # 2026-09-08: adaptiv — jede Runde ohne Antwort verdoppelt die Pause
                pause_min = min(self.ctx.hunt_cq_fallback_pause_min * (2 ** self.ctx.cq_fallback_unanswered_rounds),
                                self.ctx.hunt_cq_fallback_pause_max_min)
                self.ctx.cq_fallback_unanswered_rounds += 1
                pause_s = pause_min * 60.0
                self.ctx.cq_fallback_paused_until = (tick.posix if tick is not None else 0.0) + pause_s
                log.info("CQ-Fallback: %d CQs ohne Antwort → Pause %d min (Runde %d), zurueck ins Hunting",
                         self.ctx.cq_count, pause_min, self.ctx.cq_fallback_unanswered_rounds)
                self.ctx.cq_fallback_active = False
                self.ctx.idle_slots_without_pick = 0
                self.ctx.cq_count = 0
                self.state = State.IDLE
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
                self._exit_grace(hw)

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

    def _can_dodge_to_quiet_bin(self) -> bool:
        """Duerfen wir dem Sende-Randbereich per ruhigem Bin ausweichen?

        Nur dann darf der Picker Ziele ausserhalb des Audio-Fensters
        behalten. Steht die Station fest auf der Frequenz des Rufers
        (beide Schalter aus), bleibt der alte Filter die einzige Bremse.
        """
        return bool(self.ctx.hunt_reply_quiet_freq or self.ctx.hunt_reply_ab_test)

    def _freq_outside_tx_window(self, d: DecodedMsg) -> bool:
        """Laege unsere Antwort auf *seiner* Frequenz im gedaempften Rand?"""
        hz = getattr(d, "freq_offset_hz", None)
        if hz is None:
            return False
        fmin = self.ctx.hunt_audio_freq_min_hz
        fmax = self.ctx.hunt_audio_freq_max_hz
        return bool((fmin is not None and hz < fmin) or (fmax is not None and hz > fmax))

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
            # Sort by (occupancy, -reputation_score, distance to mid-band).
            # v0.18.0: reputation_score = successes/attempts (Wilson-Lower-
            # Bound waere fancy, fuer den Anfang reicht success-rate mit
            # Laplace-Smoothing). Hoch-erfolgreiche Bins gewinnen bei
            # gleicher Belegung.
            mid = (min_hz + max_hz) // 2
            freq_rep = self.ctx.freq_reputation
            band_now = self.ctx.band

            def reputation_score(b: int) -> float:
                att, succ = freq_rep.get((band_now, b), (0, 0))
                # Laplace-Smoothing: (succ + 1) / (att + 2) damit
                # ungetestete Bins nicht 0 sind und ueberschaetzte
                # 1-Treffer-Bins nicht 100% bekommen.
                return (succ + 1) / (att + 2)

            # Sort: (occupancy ASC, reputation DESC, distance ASC).
            # Negative reputation damit sort ASC die hoechste Reputation
            # vorne hat.
            candidates.sort(
                key=lambda b: (hist.get(b, 0), -reputation_score(b), abs(b - mid))
            )
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

    def _hop_audio_freq(self, current_hz: int) -> int:
        """v0.18.0 — Audio-Freq +/-200 Hz hopen, innerhalb FT8-Passband.

        Richtung: nach oben wenn current < 1500, sonst nach unten.
        Konsistent + reproduzierbar, kein Random.
        """
        hop_hz = 200
        if current_hz < 1500:
            new = current_hz + hop_hz
        else:
            new = current_hz - hop_hz
        # Clamp ins FT8-Passband (300..2400 Hz)
        new = max(self.CQ_AUDIO_MIN_HZ + 50, min(self.CQ_AUDIO_MAX_HZ - 50, new))
        return new

    def _note_inbound_attempt(
        self, d: DecodedMsg, decodes: list[DecodedMsg], art: str
    ) -> None:
        """Telemetrie fuer QSOs, die aus einem eingehenden Anruf entstehen.

        2026-09-10: ``pick_attempt`` erfasste bis dahin ausschliesslich
        Stationen, die der Picker selbst angerufen hat — ``hunt_attempt_meta``
        wird nur dort gefuellt, und ``_record_hunt_outcome`` steigt bei
        unbekannten Rufzeichen sofort aus. Antwortete jemand auf unser CQ,
        lief das QSO voellig ohne Messung durch. Das war ausgerechnet der
        Fall, in dem die Sequenz-Loecher steckten (v0.85.0/v0.86.0): Weder
        war zu sehen, wie oft er vorkommt, noch ob die Reparaturen wirken.

        ``art`` landet als ``pick_kind`` in der Zeile:
          * ``inbound_grid``   — sie riefen uns mit ihrem Locator
          * ``inbound_report`` — sie riefen uns direkt mit Report (Tail-Ender)
          * ``inbound_resume`` — verspaetete Fortsetzung aus dem Nachklang
        Damit lassen sich Abschlussquoten getrennt auswerten: eingehend
        gegen selbst gerufen.
        """
        call_u = (d.call_from or "").upper()
        key = base_call(call_u)
        if not key:
            return
        if len(self.ctx.hunt_attempt_meta) > 100:
            self.ctx.hunt_attempt_meta.clear()
        self.ctx.hunt_attempt_meta[key] = {
            "psk_heard_us": key in self.ctx.psk_heard_us or call_u in self.ctx.psk_heard_us,
            "was_worked": call_u in self.ctx.worked or key in self.ctx.worked,
            "was_new_dxcc": (d.call_from or "") in self.ctx.new_dxcc_calls,
            "n_decodes": len(decodes),
            "snr_db": d.snr_db,
            "dt_s": d.dt_s,
            "band": d.band,
            "ts": datetime.now(UTC),
            "pick_age_s": None,          # nicht gepickt — sie kamen zu uns
            "pick_kind": art,
            "reply_kind": None,
            "freq_offset_hz": d.freq_offset_hz,
            "target_grid": d.grid,
            "winning_tier": None,        # kein Picker-Durchlauf beteiligt
            "n_candidates": None,
            "was_tailend": art == "inbound_report",
            "hunt_priority": None,
            "psk_snr": self.ctx.psk_snr.get(call_u) or self.ctx.psk_snr.get(key),
        }

    def _stamp_outcome_meta(self) -> None:
        """v0.62.0 — finale QSO-Zaehler in die pick_attempt-Meta stempeln,
        BEVOR self.qso geleert wird. So tragen completed/bailed-Zeilen wie oft
        wir gerufen (n_resends) + wie viele Slots wir gewartet haben
        (stale_slots) — trennt "nie geantwortet" von "engagiert, dann weg"."""
        if self.qso is None:
            return
        key = base_call(self.qso.their_call)
        meta = self.ctx.hunt_attempt_meta.get(key) if key else None
        if meta is None:
            return
        meta["n_resends"] = self.qso.cq_resends + self.qso.report_resends
        meta["stale_slots"] = self.qso.stale_slots
        meta["our_snr_received"] = self.qso.our_snr_received
        try:
            _st = self.qso.started
            if _st.tzinfo is None:
                _st = _st.replace(tzinfo=UTC)
            meta["qso_duration_s"] = (datetime.now(UTC) - _st).total_seconds()
        except Exception:
            meta["qso_duration_s"] = None

    def _psk_snr_for_call(self, call: str | None) -> int | None:
        if not call:
            return None
        norm = call.upper()
        b = base_call(norm)
        val = self.ctx.psk_snr.get(norm)
        if val is None and b:
            val = self.ctx.psk_snr.get(b)
        return int(val) if val is not None else None

    def _record_hunt_outcome(self, their_call: str, completed: bool) -> None:
        key = base_call(their_call)
        if not key or key not in self.ctx.hunt_attempt_meta:
            return
        if completed:
            self.ctx.failed_attempt_counts.pop(key, None)
        outcomes = self.ctx.hunt_recent_outcomes
        outcomes.append(completed)
        window = max(1, int(self.ctx.hunt_poor_run_window))
        del outcomes[:-window]
        if len(outcomes) < window:
            return
        successes = sum(1 for ok in outcomes if ok)
        if successes < self.ctx.hunt_poor_run_min_successes:
            self.ctx.hunt_strict_until = max(
                self.ctx.hunt_strict_until,
                datetime.now(UTC).timestamp()
                + max(0, self.ctx.hunt_poor_run_strict_s),
            )

    def _effective_report_resend_limit(self) -> int:
        if self.qso is None:
            return self.qso_max_report_resends
        limit = self.qso_max_report_resends
        psk_snr = self._psk_snr_for_call(self.qso.their_call)
        strong_rx = (
            self.qso.their_snr_at_us is not None
            and self.qso.their_snr_at_us >= self.qso_report_extra_resend_snr_db
        )
        strong_psk = (
            psk_snr is not None
            and psk_snr >= self.qso_report_extra_resend_psk_snr_db
        )
        heard_us_strong = (
            self.qso.our_snr_received is not None
            and self.qso.our_snr_received >= self.qso_report_extra_resend_snr_db
        )
        if (
            self.qso_report_extra_resends > 0
            and (strong_rx or strong_psk or heard_us_strong)
        ):
            limit += self.qso_report_extra_resends
        return max(0, min(3, limit))

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
        self._stamp_outcome_meta()
        self._record_hunt_outcome(their_call, completed=False)
        self._remember_unfinished_qso()
        if self.qso_failed_cooldown_s > 0 and their_call:
            key = base_call(their_call) or their_call.upper()
            repeats = self.ctx.failed_attempt_counts.get(key, 0) + 1
            self.ctx.failed_attempt_counts[key] = repeats
            cooldown_s = self.qso_failed_cooldown_s
            if reason == "went_silent":
                cooldown_s *= max(1.0, self.qso_failed_cooldown_went_silent_multiplier)
            if repeats > 1:
                cooldown_s *= (
                    max(1.0, self.qso_failed_cooldown_repeat_multiplier)
                    ** (repeats - 1)
                )
            cooldown_s = min(
                max(0.0, cooldown_s),
                max(0.0, self.qso_failed_cooldown_max_s),
            )
            cooldown_until = datetime.now(UTC).timestamp() + cooldown_s
            self.ctx.recent_until[their_call] = cooldown_until
            log.debug(
                "Cooldown: %s set for %.0f s (reason=%s, repeats=%d)",
                their_call, cooldown_s, reason, repeats,
            )
        self.qso = None
        self.state = State.IDLE
        self._pending.append(Action("STOP_TX", {}))
        # v0.15.0 — Reputation/Slot-Parity-Hook: Orchestrator updated
        # CallReputation basierend auf Reason. Wir liefern den Grund
        # mit (picked_another zaehlt anders als max_resends).
        if their_call:
            self._pending.append(Action(
                "QSO_BAIL",
                {"call": their_call, "reason": reason},
            ))

    # Wie lange ein abgebrochenes QSO abrufbar bleibt. Deckt die typischen
    # Wiederholungen ab (Partner sendet alle 15 s); danach ist die Station
    # weitergezogen und ein nachtraeglicher Abschluss waere geraten.
    RECENT_QSO_TTL_S = 600

    def _remember_unfinished_qso(self) -> None:
        """QSO-Daten fuer eine verspaetete Fortsetzung aufbewahren.

        Nur wenn wir dem Partner schon einen Report gesendet haben — sonst
        fehlt dem Logeintrag der Rapport und es waere kein gueltiges QSO.
        """
        q = self.qso
        if q is None or q.their_snr is None or not q.their_call:
            return
        self.ctx.recent_qso_ctx[q.their_call] = (
            datetime.now(UTC).timestamp() + self.RECENT_QSO_TTL_S,
            {
                "their_call": q.their_call,
                "their_grid": q.their_grid,
                "their_snr": q.their_snr,
                "our_snr_received": q.our_snr_received,
                "their_snr_at_us": q.their_snr_at_us,
                "band": q.band,
                "freq_offset_hz": q.freq_offset_hz,
                "started": q.started,
                "from_cq_fallback": q.from_cq_fallback,
            },
        )

    def _try_resume_recent_qso(
        self, hw: HardwareState, decodes: list[DecodedMsg]
    ) -> bool:
        """Loch A und C (2026-09-10): verspaetete Fortsetzung abschliessen.

        Nach einem Timeout stehen wir in IDLE oder CQ_CALLING. Meldet sich
        der Partner dann doch noch — weil er unser RR73 nicht gehoert hat
        und seinen R-Report wiederholt (A), oder weil sein Abschluss erst
        jetzt bei uns ankommt (C) —, wurde das bisher ignoriert: Das QSO
        stand in seinem Log und fehlte in unserem. Gemessen ueber sieben
        Tage: 16 Stationen, davon EA3GXK mit 186 Wiederholungen.

        Liefert True, wenn ein QSO abgeschlossen wurde.
        """
        if not self.ctx.recent_qso_ctx:
            return False
        jetzt = datetime.now(UTC).timestamp()
        for call, (ablauf, daten) in list(self.ctx.recent_qso_ctx.items()):
            if ablauf < jetzt:
                del self.ctx.recent_qso_ctx[call]
                continue
            r_rep = _find_r_report_from_them(decodes, call, self.ctx.tx_callsign)
            schluss = _find_closing(decodes, call, self.ctx.tx_callsign)
            if r_rep is None and not schluss:
                continue
            if not self._check_guards(hw):
                return False
            del self.ctx.recent_qso_ctx[call]
            treffer = next(
                (d for d in decodes if _hashed_match(d.call_from, call)), None
            )
            if treffer is not None:
                self._note_inbound_attempt(treffer, decodes, "inbound_resume")
            self.qso = QsoContext(**daten)
            if r_rep is not None:
                self.qso.our_snr_received = r_rep
            log.info(
                "%s meldet sich nach dem Abbruch nochmal (%s) → QSO abschliessen",
                call, "R-Report" if r_rep is not None else "Abschluss",
            )
            self.state = State.QSO_LOG
            self._emit_log_qso(hw)
            return True
        return False

    def _emit_cq(self) -> None:
        # Directed-CQ (Audit F7, v0.3.4): "CQ DX/EU/POTA/TEST" prefix
        # wenn ctx.cq_directed gesetzt ist. Leer = klassischer CQ.
        directed = (self.ctx.cq_directed or "").strip().upper()
        if directed:
            msg = f"CQ {directed} {self.ctx.tx_callsign} {self.ctx.my_grid[:4]}"
        else:
            msg = f"CQ {self.ctx.tx_callsign} {self.ctx.my_grid[:4]}"
        freq = self._next_cq_freq_hz()
        self._pending.append(Action("TX_MESSAGE", self._tx_payload(msg, "cq", freq_override_hz=freq)))

    def _emit_respond_with_grid(self) -> None:
        assert self.qso is not None
        msg = f"{self.qso.their_call} {self.ctx.tx_callsign} {self.ctx.my_grid[:4]}"
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
        msg = f"{self.qso.their_call} {self.ctx.tx_callsign} {snr:+03d}"
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
        msg = f"{self.qso.their_call} {self.ctx.tx_callsign} R{snr:+03d}"
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

    def _emit_log_qso(self, hw: HardwareState) -> None:
        """QSO abschliessen: RR73 senden (wenn erlaubt) und loggen.

        ``hw`` ist Pflicht, weil das RR73 eine echte Aussendung ist und
        jeder andere TX-Pfad hier durch die Guards laeuft. Dieser hier tat
        es nicht: kam das RR73 des Partners rein, ging unser RR73 raus,
        ohne dass SWR, Zeitsync, Antenne oder Lizenz nochmal gefragt
        wurden — also genau in dem Moment, in dem ein Guard schon gegriffen
        haben koennte.

        Wichtig ist die Reihenfolge der Konsequenzen: bei Guard-Fehler
        entfaellt nur das RR73, das LOG_QSO wird trotzdem emittiert. Fuer
        die Gegenstation ist das QSO komplett — wuerden wir es nicht
        loggen, verloeren wir ein echtes QSO wegen eines lokalen
        Hardware-Problems. Der Grace-State entfaellt dagegen, weil sein
        einziger Zweck ein weiteres TX (das 73) ist.
        """
        assert self.qso is not None
        self._stamp_outcome_meta()
        self._record_hunt_outcome(self.qso.their_call, completed=True)
        tx_ok = self._check_guards(hw)
        if tx_ok:
            rr73 = f"{self.qso.their_call} {self.ctx.tx_callsign} RR73"
            self._pending.append(Action("TX_MESSAGE", self._tx_payload(rr73, "rr73")))
        else:
            log.warning(
                "QSO mit %s wird geloggt, aber RR73 bleibt aus — Guard gesperrt",
                self.qso.their_call,
            )
        if self.qso is not None and getattr(self.qso, "from_cq_fallback", False):
            self.ctx.cq_fallback_qsos += 1
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
                    # v0.22.0 — TX-Callsign (mit DX-Prefix wenn Auslandsbetrieb)
                    # damit ADIF station_callsign sauber "9A/DK9XR" emittiert
                    # statt nur "DK9XR". operator-Feld bleibt der Heimat-Call.
                    "station_callsign": self.ctx.tx_callsign,
                    "operator": self.ctx.callsign,
                },
            )
        )
        # QSO_GRACE: 1-Slot-Wartefenster fuer Partner-RR73-Repeat. Wenn
        # er sein RR73 nochmal sendet (= unser RR73 nicht decodiert),
        # antworten wir noch mit 73 (Tx6) als WSJT-X-konforme Closure.
        # Sebastian 2026-05-24, Audit-Finding 2.
        partner = self.qso.their_call
        self.qso = None
        if not tx_ok:
            # State bleibt TX_LOCKED (von _check_guards gesetzt) — kein
            # Grace-Fenster, dessen einziger Zweck ein weiteres TX waere.
            return
        self._grace_partner_call = partner
        self._grace_ticks_remaining = 1
        self.state = State.QSO_GRACE

    def _exit_grace(self, hw: HardwareState) -> None:
        """Beende QSO_GRACE — normaler Continuation-Pfad.

        ``hw`` ist Pflicht, weil der auto_cq-Zweig unten ein TX_MESSAGE
        emittiert. Vorher lief dieser Weg an den Guards vorbei: nach jedem
        abgeschlossenen QSO ging im auto_cq-Betrieb ungeprueft ein CQ
        raus — und das ist der Normalfall im Dauerbetrieb, nicht die
        Ausnahme.
        """
        self._grace_partner_call = None
        self._grace_ticks_remaining = 0
        if self.ctx.auto_cq and not self._check_guards(hw):
            # _check_guards hat bereits TX_LOCKED gesetzt + STOP_TX
            # emittiert. Sticky wie ueberall sonst: der Operator muss den
            # Lock quittieren, sonst laeuft die Box nach einem SWR-Spike
            # einfach weiter CQ rufend.
            return
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
    def _build_synthetic_tail_end_decodes(
        self, real_decodes: Iterable[DecodedMsg]
    ) -> list[DecodedMsg]:
        """Bauet synthetische CQ-Decodes fuer aktive Tail-End-Candidates.

        Wir treten so auf als haetten sie gerade CQ gerufen — damit sie
        durch den Standard-Filter-Stack (worked, blacklist, SNR-floor,
        freq-bounds, cooldown) laufen und der Tier-Score sie via
        _tier_tail_end_target hochzieht. Reuse: snr/freq/band/grid vom
        letzten echten Decode des Closings.

        Duplikat-Schutz: wenn dieselbe Station in real_decodes bereits
        einen echten CQ-Decode hat, kein Synthetic — der echte hat
        Vorrang und der Tier-Score gilt fuer ihn genauso.
        """
        if not self.ctx.tail_end_candidates:
            return []
        existing_cq_calls = {
            d.call_from.upper() for d in real_decodes
            if _is_usable_partner_call(d.call_from) and d.call_to is None
            and (d.message or "").startswith("CQ")
        }
        now_ts = datetime.now(UTC)
        now_posix = now_ts.timestamp()
        synth: list[DecodedMsg] = []
        for call, meta in self.ctx.tail_end_candidates.items():
            if not _is_usable_partner_call(call):
                continue
            if call in existing_cq_calls:
                continue
            # 24h-Cooldown auch im Injection-Pfad respektieren — sonst
            # landet ein bereits gepickter Call wieder als synthetischer
            # CQ-Decode im Pool und gewinnt ueber den SNR-Tie-Breaker,
            # selbst wenn _tier_tail_end_target 0 liefert. Bug 2026-05-27:
            # UN7GBX wurde 17 min nach erstem Tail-End-Pick erneut gepickt
            # (Tier=0 reichte nicht, weil SNR -6 dB als Tie-Breaker
            # zuschlug). Der echte Closing-Decode wuerde den Standard-
            # Picker eh nicht durchlaufen (kein CQ-Prefix), also ist
            # dieser Filter exklusiv zustaendig fuer die 24h-Sperre.
            last_pick = self.ctx.tail_end_last_pick.get(call)
            if last_pick is not None and now_posix - last_pick < TAIL_END_COOLDOWN_S:
                continue
            grid = meta.get("grid") or ""
            # Fake-CQ-Message: nutzt das Grid wenn bekannt (matched dann
            # auch new_grid-Tier), sonst nur Call. Format kompatibel
            # zum Standard-CQ-Parser (call_from = sender, call_to = None).
            msg = f"CQ {call} {grid}".strip() if grid else f"CQ {call}"
            synth.append(DecodedMsg(
                ts=now_ts,
                call_from=call,
                call_to=None,
                grid=grid or None,
                message=msg,
                snr_db=meta.get("snr_db"),
                # dt_s=0.0 bypassed bewusst den DT-Filter — der Closing-
                # Decode hat schon bewiesen dass die Station decodebar
                # ist; ein moeglicher DT-Drift war im Original-Decode
                # bereits gemessen und auf der RX-Seite ok.
                dt_s=0.0,
                freq_offset_hz=meta.get("freq_offset_hz"),
                band=meta.get("band") or self.ctx.band,
                is_freetext=False,
            ))
        return synth

    def _decisive_tier(self, winner: DecodedMsg, cqs: list[DecodedMsg]) -> str:
        """v0.64.0 — welcher hunt_priority-Tier den Pick entschied: der erste
        Tier (in Prioritaetsreihenfolge), an dem der Gewinner den besten
        Mitbewerber strikt schlaegt. 'sole' bei nur einem Kandidaten, 'tie'
        bei identischem Score (Gewinn nur per Listenreihenfolge). Fail-soft."""
        try:
            others = [d for d in cqs if d is not winner]
            if not others:
                return "sole"
            priority = self.ctx.hunt_priority or []
            names = [nm for nm in priority if nm in HUNT_TIERS]
            if "snr" not in priority:
                names = [*names, "snr"]
            if not names:
                return "snr"
            w = _compute_tier_score(winner, self.ctx)
            runner = max(others, key=lambda d: _compute_tier_score(d, self.ctx))
            r = _compute_tier_score(runner, self.ctx)
            for i, nm in enumerate(names):
                if i >= len(w) or i >= len(r):
                    break
                if w[i] > r[i]:
                    return nm
                if w[i] < r[i]:
                    break  # darf nicht passieren (winner ist der max)
            return "tie"
        except Exception:
            return "unknown"

    def _is_award_or_context_pick(self, d: DecodedMsg) -> bool:
        if not d.call_from:
            return False
        norm = d.call_from.upper()
        if norm in self.ctx.new_dxcc_calls or norm in self.ctx.marine_calls:
            return True
        if _tier_new_dxcc_band(d, self.ctx) > 0:
            return True
        if _tier_new_grid(d, self.ctx) > 0 or _tier_new_grid_band(d, self.ctx) > 0:
            return True
        if _tier_buddy_seen(d, self.ctx) > 0:
            return True
        if _tier_active_hour(d, self.ctx) > 0 or _tier_grayline(d, self.ctx) > 0:
            return True
        return False

    def _decode_distance_km(self, d: DecodedMsg) -> float | None:
        try:
            my_lat, my_lon = _maidenhead_to_latlon(self.ctx.my_grid)
            if d.grid:
                lat, lon = _maidenhead_to_latlon(d.grid)
                return _distance_km(my_lat, my_lon, lat, lon)
            if d.call_from:
                pos = self.ctx.call_to_latlon.get(d.call_from.upper())
                if pos is not None:
                    lat, lon = pos
                    return _distance_km(my_lat, my_lon, lat, lon)
        except Exception:
            return None
        return None

    def _is_high_confidence_pick(self, d: DecodedMsg, *, strict: bool) -> bool:
        if not strict and self.ctx.hunt_snr_floor_db is None:
            return True
        if self._is_award_or_context_pick(d):
            return True
        psk_snr = self._psk_snr_for_call(d.call_from)
        snr_limit = (
            self.ctx.hunt_strict_min_snr_db
            if strict else self.ctx.hunt_sole_min_snr_db
        )
        psk_limit = (
            self.ctx.hunt_strict_min_psk_snr_db
            if strict else self.ctx.hunt_sole_min_psk_snr_db
        )
        if d.snr_db is not None and d.snr_db >= snr_limit:
            return True
        if d.snr_db is None and not strict:
            return True
        if psk_snr is not None and psk_snr >= psk_limit:
            return True
        if d.call_from and d.call_from.upper() in self.ctx.psk_heard_us and not strict:
            return True
        return False

    def _apply_hunt_profile(self, cqs: list[DecodedMsg]) -> list[DecodedMsg]:
        profile = self.ctx.hunt_profile
        if profile == "balanced" and self.ctx.mode == "FT4":
            profile = "rate"
        if profile == "balanced" or len(cqs) <= 1:
            return cqs
        if profile == "rate":
            kept: list[DecodedMsg] = []
            for d in cqs:
                if self._is_award_or_context_pick(d):
                    kept.append(d)
                    continue
                dist = self._decode_distance_km(d)
                if d.snr_db is not None and d.snr_db >= -12:
                    kept.append(d)
                elif dist is not None and dist < 3000 and d.snr_db is not None and d.snr_db >= -16:
                    kept.append(d)
            return kept or cqs
        if profile == "dx":
            dx = [
                d for d in cqs
                if self._is_award_or_context_pick(d)
                or (
                    (self._decode_distance_km(d) or 0) >= 3000
                    and (d.snr_db is None or d.snr_db >= -18)
                )
            ]
            return dx or cqs
        return cqs

    def _pick_hunt_target(self, decodes: Iterable[DecodedMsg]) -> DecodedMsg | None:
        """Pick the strongest non-blacklisted CQ from this slot's decodes.

        Rules:
          * Must be a CQ (call_to is None and message starts with CQ)
          * call_from not in blacklist
          * call_from != our own callsign (don't reply to ourselves)
          * SNR >= hunt_snr_floor_db (kein "die wird uns eh nicht hoeren")
          * highest SNR wins

        v0.11.0: zusaetzlich werden synthetische CQ-Decodes fuer aktive
        Tail-End-Candidates injiziert — siehe _build_synthetic_tail_end_decodes.
        """
        if self.ctx.drain_for_update:
            return None   # 2026-09-07: Update wartet — keine neuen Picks
        decodes = list(decodes)
        if self.ctx.tail_end_hunter_enabled:
            decodes = decodes + self._build_synthetic_tail_end_decodes(decodes)
        cqs = [
            d for d in decodes
            if _is_usable_partner_call(d.call_from)
            and d.call_from != self.ctx.tx_callsign
            and d.call_from not in self.ctx.blacklist
            and d.call_to is None
            and (d.message or "").startswith("CQ")
            and not getattr(d, "is_freetext", False)  # Audit F8 v0.3.4
        ]
        # Contest-CQ-Deprio (Audit F9 v0.3.4): Stationen die "CQ TEST",
        # "CQ RU", "CQ FD", "CQ WW" rufen erwarten ein Contest-Exchange
        # (RST + Sektion/Class), nicht unsere Standard-Grid-Antwort.
        # Wir wuerden 4-5 Slots verschwenden bis Bail. Filter sie raus.
        # Sebastian operiert nicht im Contest — wenn das mal noetig wird,
        # ist's per-Config togglebar (ctx.allow_contest_cq, default False).
        contest_tokens = {"TEST", "RU", "FD", "WW", "WPX", "SS", "IARU", "CWT"}
        cqs = [
            d for d in cqs
            if not _is_contest_cq(d.message or "", contest_tokens)
        ]
        # Gerichtete CQs (2026-09-06): "CQ NA", "CQ JA", "CQ AS" gelten nicht
        # uns, "CQ DX" von einer Station des eigenen Kontinents auch nicht.
        # Wer sie trotzdem anruft, wird ignoriert und verbrennt bis zum Bail
        # 3-6 Slots. Unbekannte Tokens (POTA, SOTA, …) schliessen nichts aus.
        if self.ctx.hunt_respect_directed_cq and self.ctx.my_continent:
            cqs = [
                d for d in cqs
                if not _directed_cq_excludes_us(
                    getattr(d, "cq_directed", None),
                    self.ctx.call_to_continent.get((d.call_from or "").upper()),
                    self.ctx.my_continent,
                )
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
                or _in_watchlist(d.call_from, self.ctx.watchlist_calls)
            ]
        # 2026-09-08: Kontinent-Gate — aus Kontinenten mit Vollendungsquote
        # unter hunt_continent_gate_pct nur mit PSK-Bestaetigung (NA 3 %).
        if self.ctx.hunt_continent_gate and self.ctx.continent_success:
            thr = self.ctx.hunt_continent_gate_pct / 100.0
            def _cont_ok(d: DecodedMsg) -> bool:
                cu = (d.call_from or "").upper()
                cont = self.ctx.call_to_continent.get(cu)
                rate = self.ctx.continent_success.get(cont) if cont else None
                if rate is None or rate >= thr:
                    return True
                if _in_watchlist(d.call_from, self.ctx.watchlist_calls):
                    return True
                return cu in self.ctx.psk_heard_us or (base_call(d.call_from) or "") in self.ctx.psk_heard_us
            cqs = [d for d in cqs if _cont_ok(d)]
        # 2026-09-07: schwache Ziele nur mit PSK-Bestaetigung (Telemetrie:
        # unter -13 dB kamen 3 % zurueck, darueber 12 %).
        if self.ctx.hunt_weak_requires_psk:
            weak = self.ctx.hunt_weak_snr_db
            cqs = [
                d for d in cqs
                if d.snr_db is None or d.snr_db >= weak
                or (d.call_from or "").upper() in self.ctx.psk_heard_us
                or (base_call(d.call_from) or "") in self.ctx.psk_heard_us
                or _in_watchlist(d.call_from, self.ctx.watchlist_calls)
            ]
        # DT-Filter (Sebastian v0.5.4, Audit-Lücke 1 vs WSJT-X):
        # Stationen mit |dt_s| > 2.5s sind zwar decodebar (FT8-Decoder
        # toleriert mehr), aber ihr eigenes RX-Fenster ist schon zu
        # Ende wenn unsere Reply ankommt — sie hoeren uns gar nicht.
        # Wir vergeuden TX-Slots fuer Stationen die nicht zurueck
        # koennen. Audit 2026-05-25: 0.6% der Decodes betroffen,
        # darunter mehrere CQs die wir gepickt aber nie ne Reply
        # bekommen haben (z.B. T48FCR dt=+3.1s, OH5C dt=+2.6s).
        cqs = [
            d for d in cqs
            if d.dt_s is None or abs(d.dt_s) <= 2.5
        ]
        # Audio-Frequenz-Filter: Decodes ausserhalb [min, max] uebersprungen,
        # weil unser Reply dort durch den Rig-Audio-Bandpass gedaempft
        # waere. Sebastian sah 2026-05-22 wie ein Reply auf 262 Hz (unter
        # dem IC-7300-Bandpass von ~300 Hz) den PI in einen PWR-Spike
        # mit Watchdog-Cut trieb. Mit Filter wird die Station gar nicht
        # erst angerufen.
        # 2026-09-10: Der Filter verwirft nur noch, wenn wir dem Rand nicht
        # ausweichen koennen. Sein Grund war immer das *Senden* — ein Reply
        # auf 262 Hz lief 2026-05-22 in den Rig-Bandpass und riss den Pi in
        # einen PWR-Spike. Seit 2026-09-06 antworten wir aber ohnehin auf dem
        # ruhigsten Bin (300-2400 Hz, immer innerhalb des Fensters), und der
        # Rufer dekodiert das ganze Passband. Damit hielt der Filter
        # Stationen zurueck, deren Frequenz fuer unsere Sendung keine Rolle
        # mehr spielt: ueber fuenf Tage 1018 CQ-Rufe von 161 Stationen,
        # darunter J38DX (Grenada, 51-mal), AA3B, 4L7T, SV8/F6BLP — alle nie
        # angerufen. Statt zu verwerfen erzwingt _do_hunt_pick fuer diese
        # Ziele jetzt den ruhigen Bin.
        if not self._can_dodge_to_quiet_bin():
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
        # Recent-Cooldown: Station ist gerade erst gearbeitet/abgebrochen,
        # ruft aber weiter CQ → wir überspringen ihn bis sein Fenster
        # abgelaufen ist. Zwei Cooldowns:
        #   recent_until[call]          — kurzer Failed/Reply-Cooldown, band-weit
        #   worked_until[(call, band)]  — Erfolgs-Cooldown, BAND-BEWUSST, damit
        #     ein neuer Band-Slot derselben Station nicht geblockt wird
        # Verhindert "3× EA4GA in 5 min"-Effekte ohne 5BWAS/Band-Füllen
        # zu sabotieren.
        if self.ctx.recent_until or self.ctx.worked_until:
            now_ts = datetime.now(UTC).timestamp()
            cqs = [
                d for d in cqs
                if self.ctx.recent_until.get(d.call_from or "", 0) <= now_ts
                and self.ctx.worked_until.get(
                    (d.call_from or "", d.band or ""), 0
                ) <= now_ts
            ]
        # v0.20.4 — Hard-Filter fuer die drei "Filter-Tiers" damit sie
        # auch dann greifen wenn nur ein einziger CQ im Slot ist
        # (Tier-Score 0 alleine reicht nicht — bei nur einem Kandidaten
        # gewinnt er trotzdem). Sebastian-Live-Audit 2026-05-27:
        # EA5KB wurde mit score=10/attempts=10 weiterhin gepickt obwohl
        # ctx.soft_blacklist ihn enthielt — Tier lieferte 0, aber er war
        # der einzige CQ und gewann via SNR-Tie-Breaker.
        #
        # Bug-Klasse trifft analog Pile-Up + Slot-Parity. Alle drei
        # werden hier hart gefiltert; die Tiers in HUNT_TIERS bleiben
        # bestehen als zusaetzliche Defense (falls User sie aus der
        # Priority-Liste rausnimmt aber das Filter-Set noch befuellt).
        if self.ctx.soft_blacklist:
            cqs = [
                d for d in cqs
                if (d.call_from or "").upper() not in self.ctx.soft_blacklist
            ]
        # 2026-09-10 — Trennlinie bei den Filtern: Gates, die fragen "lohnt
        # sich das?" (Pile-Up, SNR-Floor, Kontinent-Quote, Schwach-Gate),
        # werden fuer Stationen von der Wunschliste uebersteuert. Wer eine
        # Station dort eintraegt, hat die Abwaegung schon getroffen — und
        # seltenes DX ist praktisch immer schwach UND umlagert. Gates, die
        # sagen "geht technisch nicht" (DT ausserhalb des Empfangsfensters,
        # gleiche Slot-Paritaet) und die ausdrueckliche Sperre der
        # Soft-Blacklist bleiben fuer alle bestehen.
        if self.ctx.pile_up_calls:
            # 2026-09-10: Stationen von der Wunschliste sind vom Pile-Up-
            # Filter ausgenommen. Seltenes DX hat per Definition Pile-Up —
            # der harte Filter machte die Liste damit genau fuer die
            # Stationen wirkungslos, fuer die man sie anlegt. Gemessen:
            # Z68PX (Kosovo) rief 16-mal CQ, kein einziger Anrufversuch;
            # V51WH (Namibia) ebenso. Wer eine Station auf die Liste setzt,
            # nimmt das Gedraenge bewusst in Kauf. Alle anderen bleiben
            # gefiltert.
            cqs = [
                d for d in cqs
                if (d.call_from or "").upper() not in self.ctx.pile_up_calls
                or _in_watchlist(d.call_from, self.ctx.watchlist_calls)
            ]
        # Slot-Parity: meide Calls deren TX-Parity == aktueller Slot —
        # sie senden gerade selbst und hoeren uns nicht.
        if self.ctx.current_slot_parity and self.ctx.op_slot_parity:
            cur = self.ctx.current_slot_parity
            cqs = [
                d for d in cqs
                if self.ctx.op_slot_parity.get(
                    (d.call_from or "").upper()
                ) != cur
            ]
        # Conservative Hunt Gates:
        # - Ein einziger CQ im Slot ("sole") ist live auffaellig oft ein
        #   Fehlversuch. Ohne Award-/Kontextsignal rufen wir ihn nur an,
        #   wenn Decode- oder PSK-SNR gut genug ist.
        # - Nach einer schlechten Outcome-Serie geht der Autopilot fuer
        #   einige Minuten in Strict Mode und verlangt diese Evidenz fuer
        #   alle Routine-Ziele.
        # - hunt_profile kann Rate/DX-Praeferenz als zusaetzliches Gate
        #   anwenden, ohne die bestehende Priority-Liste zu ersetzen.
        now_ts = datetime.now(UTC).timestamp()
        strict = self.ctx.hunt_strict_until > now_ts
        if len(cqs) == 1 and not self._is_high_confidence_pick(cqs[0], strict=False):
            self._last_pick_diag = {
                "winning_tier": "sole_rejected",
                "n_candidates": 1,
                "was_tailend": False,
            }
            return None
        if strict:
            cqs = [d for d in cqs if self._is_high_confidence_pick(d, strict=True)]
        cqs = self._apply_hunt_profile(cqs)
        if not cqs:
            return None
        # v0.10.0 Hunt-Priority-Tiers: kaskadierender Score nach ctx.
        # hunt_priority. Default-Reihenfolge ist aus OperatingConfig
        # gehydratet. User kann via UI permutieren. Siehe HUNT_TIERS-
        # Registry oben + docs/hunt_priority.md.
        #
        # Edge-case: wenn ctx.hunt_priority leer ist (z.B. alte Config
        # ohne Migration durchgelaufen), fällt _compute_tier_score auf
        # reines SNR-Ranking zurück — kein Crash, just less smart.
        if not self.ctx.hunt_priority:
            # Backward-compat: alte prefer_new_dxcc-Logik wenn neue Liste
            # nicht gesetzt ist.
            prefer_dxcc = (
                self.ctx.prefer_new_dxcc and bool(self.ctx.new_dxcc_calls)
            )

            def legacy_score(d: DecodedMsg) -> tuple[int, int]:
                is_new = (
                    1 if prefer_dxcc and (d.call_from or "") in self.ctx.new_dxcc_calls
                    else 0
                )
                return (is_new, d.snr_db if d.snr_db is not None else -99)

            winner = max(cqs, key=legacy_score)
            decisive = "legacy"
        else:
            winner = max(cqs, key=lambda d: _compute_tier_score(d, self.ctx))
            decisive = self._decisive_tier(winner, cqs)
        # v0.64.0 — Picker-Diagnose fuer die pick_attempt-Telemetrie.
        was_tail = bool(
            self.ctx.tail_end_hunter_enabled
            and winner.call_from
            and winner.call_from.upper() in self.ctx.tail_end_candidates
        )
        self._last_pick_diag = {
            "winning_tier": decisive,
            "n_candidates": len(cqs),
            "was_tailend": was_tail,
        }

        # v0.11.0: wenn Tail-End-Candidate gewonnen hat, 24h-Cooldown
        # setzen. Auch wenn der Tier nicht in hunt_priority steht — wir
        # wollen nicht mehrfach am Tag ueber den Tail-End-Pfad bei
        # derselben Station landen.
        if (
            self.ctx.tail_end_hunter_enabled
            and winner.call_from
            and winner.call_from.upper() in self.ctx.tail_end_candidates
        ):
            self.ctx.tail_end_last_pick[winner.call_from.upper()] = (
                datetime.now(UTC).timestamp()
            )
            log.info(
                "Tail-End-Pick: %s (24h-Cooldown gesetzt)",
                winner.call_from,
            )
        return winner

    def set_drain_for_update(self, enabled: bool) -> None:
        """2026-09-07: Self-Update angekuendigt — laufendes QSO zu Ende
        bringen, aber nichts Neues anfangen. Zurueck auf False, falls das
        Update doch nicht kommt."""
        self.ctx.drain_for_update = enabled
        if enabled:
            log.info("Drain fuer Self-Update: keine neuen Picks/CQs, laufendes QSO wird beendet")

    def set_auto_answer(self, enabled: bool) -> None:
        """Toggle hunting mode. Active only while state is IDLE."""
        self.ctx.auto_answer = enabled
        # 2026-09-07: Antwortmodus aus beendet auch den CQ-Fallback — der
        # gehoert zum Hunting, nicht zum manuellen CQ-Modus.
        if not enabled and self.ctx.cq_fallback_active and self.state is State.CQ_CALLING:
            self.ctx.cq_fallback_active = False
            self.ctx.idle_slots_without_pick = 0
            self.ctx.cq_count = 0
            self.state = State.IDLE
            self._pending.append(Action("STOP_TX", {}))

    # ------------------------------------------------------------------ tail-end hunter
    # v0.11.0 — siehe docstring im Picker und feature_completeness-Memory.
    TAIL_END_EXPIRY_S = 30.0          # 2 FT8-Slots
    TAIL_END_RECENT_CQ_S = 300.0      # 5 min — wer noch CQ ruft braucht keinen Tail-End
    TAIL_END_RECENT_CQ_PRUNE_S = 600.0  # 10 min — Tracking-Dict-Hygiene

    def _update_tail_end_state(self, decodes: Iterable[DecodedMsg]) -> None:
        """Pflegt ctx.tail_end_candidates + ctx.tail_end_recent_cq.

        - Jeder CQ-Decode aktualisiert tail_end_recent_cq[call] = now.
        - Jeder R-Report-Decode (X→Y "R-12") markiert X als
          tail_end_pre_staged (v0.16.0): naechster Slot bringt mit
          hoher Wahrscheinlichkeit RR73 von X. Damit ueberschreiben
          wir den 5-min-CQ-Filter beim Closing-Detect.
        - Jeder Closing-Decode (RR73/RRR/73) macht den Sender zum
          Candidate, sofern er nicht in den letzten 5 min selbst CQ
          gerufen hat (sein naechster CQ kommt eh) UND er das Closing
          nicht an UNS gesendet hat (das ist unser Partner, der kommt
          via Standard-Cooldown). Pre-Staged Calls ueberspringen den
          5-min-Filter weil wir wissen dass sie GERADE fertig sind.
        - Expiry-Pflege geschieht in on_slot_tick (siehe dort).
        """
        now = datetime.now(UTC).timestamp()
        # Index aller "echten CQs" dieses Slots — used both fuer recent_cq-
        # Tracking und um zu wissen welcher Candidate gerade CQ ruft.
        for d in decodes:
            if not _is_usable_partner_call(d.call_from):
                continue
            if getattr(d, "is_freetext", False):
                continue
            if d.call_to is not None:
                continue
            if not (d.message or "").startswith("CQ"):
                continue
            self.ctx.tail_end_recent_cq[d.call_from.upper()] = now

        # v0.16.0 Pre-Stage: R-Report-Decodes erkennen. Pattern:
        # "<Y> <X> R-<snr>" — X sendet R-Report an Y. X ist also in
        # QSO_REPORT-State; naechster Slot bringt mit hoher
        # Wahrscheinlichkeit sein RR73. Markieren wir ihn als Pre-
        # Staged damit der spaetere Closing-Detect die snr/freq
        # parat hat (zero detection latency) + den 5-min-CQ-Filter
        # uebersteuert (wir wissen er ist gerade fertig, kein
        # Routine-CQ-Caller).
        for d in decodes:
            if not _is_usable_partner_call(d.call_from) or getattr(d, "is_freetext", False):
                continue
            msg = (d.message or "")
            if d.call_to is None:
                continue
            if d.call_to == self.ctx.tx_callsign:
                # Das ist UNSER Partner → kein Pre-Stage. Sein RR73
                # geht an UNS, nicht an andere.
                continue
            if _R_SNR_RE.search(" " + msg) is None:
                continue
            call = d.call_from.upper()
            self.ctx.pre_staged_tail_ends[call] = {
                "expiry": now + self.TAIL_END_EXPIRY_S,
                "snr_db": d.snr_db,
                "freq_offset_hz": d.freq_offset_hz,
                "band": d.band,
                "grid": d.grid,
            }
            log.debug(
                "Tail-End-PreStage: %s (R-Report an %s, snr=%s freq=%s)",
                call, d.call_to, d.snr_db, d.freq_offset_hz,
            )

        # Closings einsammeln. Eligibility-Filter direkt hier statt im
        # Tier — sonst muessten wir die snr/freq vom letzten Decode auch
        # noch durchschleusen.
        for d in _iter_closings(decodes):
            call = (d.call_from or "").upper()
            if not _is_usable_partner_call(call):
                continue
            # Wenn das Closing an UNS gerichtet ist: das ist unser
            # Partner. Der kommt nach LOG_QSO eh in den Standard-Cooldown
            # (qso_cooldown_min), kein Tail-End noetig.
            if d.call_to == self.ctx.tx_callsign:
                continue
            # 5-min-Filter: Station ruft sowieso noch CQ, kein Tail-End-
            # Boost noetig — ihr naechster CQ-Slot kommt von selbst.
            # AUSNAHME (v0.16.0 Pre-Stage): wenn wir gerade einen R-Report
            # von ihm gesehen haben, war er nachweislich im QSO_REPORT
            # → das jetzt sichtbare Closing ist die natuerliche Fortsetzung,
            # kein "weiter-rufen-und-irgendwann-Closing". Pre-Stage
            # uebersteuert den 5-min-CQ-Filter.
            is_pre_staged = call in self.ctx.pre_staged_tail_ends
            recent_cq_at = self.ctx.tail_end_recent_cq.get(call)
            if (recent_cq_at is not None
                    and now - recent_cq_at < self.TAIL_END_RECENT_CQ_S
                    and not is_pre_staged):
                log.debug(
                    "Tail-End: %s ignoriert (selbst CQ vor %.0fs)",
                    call, now - recent_cq_at,
                )
                continue
            # Speichere Metadaten vom Closing-Decode — die brauchen wir
            # spaeter um den synthetischen CQ-Decode im Picker zu bauen
            # (freq_offset_hz fuer Reply-Frequenz, snr_db fuer SNR-Floor,
            # band fuer den Filter-Stack).
            self.ctx.tail_end_candidates[call] = {
                "expiry": now + self.TAIL_END_EXPIRY_S,
                "snr_db": d.snr_db,
                "freq_offset_hz": d.freq_offset_hz,
                "band": d.band,
                "grid": d.grid,
            }
            log.info(
                "Tail-End-Candidate: %s (Closing an %s, snr=%s freq=%s)",
                call, d.call_to or "?", d.snr_db, d.freq_offset_hz,
            )

    def _prune_tail_end_state(self) -> None:
        """Expirierte Candidates rauswerfen + recent_cq-Dict trimmen.

        Wird im on_slot_tick aufgerufen. Pflicht damit der Tail-End-
        Tier nicht ewig auf einem 5-min-alten Closing kleben bleibt.
        """
        now = datetime.now(UTC).timestamp()
        expired = [
            call for call, meta in self.ctx.tail_end_candidates.items()
            if meta.get("expiry", 0) <= now
        ]
        for call in expired:
            del self.ctx.tail_end_candidates[call]
            log.debug("Tail-End-Candidate %s expired", call)
        # recent_cq-Hygiene: alles aelter als 10 min ist eh nicht mehr
        # filter-relevant (Filter-Fenster ist 5 min) — wegputzen damit
        # das Dict nicht ewig waechst.
        stale_cq = [
            call for call, ts in self.ctx.tail_end_recent_cq.items()
            if now - ts > self.TAIL_END_RECENT_CQ_PRUNE_S
        ]
        for call in stale_cq:
            del self.ctx.tail_end_recent_cq[call]
        # v0.16.0 Pre-Stage expiry analog Candidates
        expired_ps = [
            call for call, meta in self.ctx.pre_staged_tail_ends.items()
            if meta.get("expiry", 0) <= now
        ]
        for call in expired_ps:
            del self.ctx.pre_staged_tail_ends[call]

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
                self.ctx.last_lock_code = None
                self.ctx.last_lock_params = None
            return True
        # Localize the reason in the config-default lang for logs/health/tests;
        # the web layer re-localizes from code+params for the browser.
        reason = _i18n.translate(bad.code or "", None, **(bad.params or {}))
        log.warning("guard %s failed: %s", bad.name, reason)
        self.state = State.TX_LOCKED
        self.ctx.last_lock_reason = f"{bad.name}: {reason}"
        self.ctx.last_lock_code = bad.code
        self.ctx.last_lock_params = bad.params
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


_CONTINENT_TOKENS = {"EU", "NA", "SA", "AF", "AS", "OC", "AN"}
# Laender-/Regions-Tokens, die man in gerichteten CQs sieht, auf Kontinente.
_REGION_TOKENS = {
    "JA": "AS", "VK": "OC", "ZL": "OC", "USA": "NA", "US": "NA", "EUR": "EU",
    "ASIA": "AS", "AFR": "AF", "SAM": "SA", "NAM": "NA", "OCE": "OC",
}


def _directed_cq_excludes_us(
    token: str | None, their_continent: str | None, my_continent: str | None,
) -> bool:
    """Gilt ein gerichtetes CQ NICHT uns? Unbekannt heisst: gilt uns."""
    if not token or not my_continent:
        return False
    t = token.upper()
    if t == "DX":
        # "CQ DX" heisst: nicht mein eigener Kontinent. Steht der Rufer
        # auf unserem Kontinent, sind wir kein DX fuer ihn.
        return bool(their_continent) and their_continent == my_continent
    if t in _CONTINENT_TOKENS:
        return t != my_continent
    region = _REGION_TOKENS.get(t)
    if region is not None:
        return region != my_continent
    return False


def _is_contest_cq(message: str, contest_tokens: set[str]) -> bool:
    """True wenn der CQ einen Contest-Token enthaelt (CQ TEST/RU/FD/WW/WPX/...)
    — Sebastian Audit F9 v0.3.4. Pattern: "CQ <TOKEN> <call> <grid>"."""
    parts = message.split()
    if len(parts) < 2 or parts[0] != "CQ":
        return False
    return parts[1].upper() in contest_tokens


def _hashed_match(field: str | None, expected: str) -> bool:
    """Hashed-Call-Wildcard (Sebastian Audit F5, v0.3.4): FT8 hashes
    Calls die nicht in 13 chars passen (compound calls wie DL/W1AW,
    DK9XR/P, EK/RX3DPK). Decoder zeigt sie als ``<...>``. Wenn wir in
    einer aktiven QSO mit *expected* sind und der andere Field-Slot der
    Decode-Message korrekt zu uns passt, akzeptieren wir das ``<...>``
    als Wildcard-Match. Ohne diese Erweiterung wuerden Antworten auf
    unsere compound-Replies (siehe EK/RX3DPK-Case) verloren gehen.
    """
    if field is None:
        return False
    return field == expected or field == "<...>"


def _find_report_from_them(
    decodes: Iterable[DecodedMsg], their_call: str, my_call: str
) -> int | None:
    """Decode like ``DK9XR W1AW -12`` — them giving us a signal report.

    Hashed-Call-tolerant seit v0.3.4: matched auch wenn unser Call oder
    ihrer als ``<...>`` decoded wurde (compound-call-Hash). Beide
    gleichzeitig als <...> waere mehrdeutig → mindestens eine Seite
    muss exakt matchen.
    """
    for d in decodes:
        to_ok = _hashed_match(d.call_to, my_call)
        from_ok = _hashed_match(d.call_from, their_call)
        if not (to_ok and from_ok):
            continue
        # Ambiguity-Guard: beide als <...> waere wild guess
        if d.call_to == "<...>" and d.call_from == "<...>":
            continue
        m = _SNR_RE.search(" " + d.message)
        if m and not d.message.startswith(f"{d.call_to} {d.call_from} R"):
            return int(m.group(1))
    return None


def _iter_closings(decodes: Iterable[DecodedMsg]) -> Iterable[DecodedMsg]:
    """Liefert alle Decodes die ein QSO-Closing sind (RR73/RRR/73 als
    letztes Wort der Message). Im Gegensatz zu _find_closing filtert
    diese Funktion NICHT auf Partner/MyCall — sie sieht ALLE Closings
    im Slot, damit der Tail-End-Hunter erkennen kann wer gerade ein
    QSO mit irgendwem beendet hat.

    is_freetext-Decodes werden uebersprungen (Tx5/Tx6 Free-Text wie
    "73 GL" wuerde sonst false-positives ergeben).
    """
    for d in decodes:
        if not d.message or not d.call_from:
            continue
        if getattr(d, "is_freetext", False):
            continue
        tail = d.message.split()[-1].upper() if d.message.split() else ""
        if tail in {"RR73", "RRR", "73"}:
            yield d


def _in_watchlist(call: str | None, watchlist: set[str]) -> bool:
    """Steht dieses Rufzeichen auf der Wunschliste?

    Die Liste enthaelt sowohl volle Rufzeichen (``Z68PX``) als auch
    Praefixe fuer angekuendigte DXpeditionen (``KH8``, ``VP5``) — beim
    Import aus dem NG3K-Kalender steht oft nur das Praefix fest. Beides
    muss greifen, sonst laeuft die Ausnahme fuer genau die Faelle ins
    Leere, fuer die die Liste gedacht ist.
    """
    if not call or not watchlist:
        return False
    c = call.upper()
    if c in watchlist:
        return True
    basis = base_call(c) or c
    if basis in watchlist:
        return True
    return any(len(w) >= 2 and basis.startswith(w) for w in watchlist)


def _find_r_report_from_them(
    decodes: Iterable[DecodedMsg], their_call: str, my_call: str
) -> int | None:
    """Decode wie ``DK9XR R7OV R-11`` — sie bestaetigen unseren Report.

    Das ist Tx3 der Standardsequenz: Der Partner hat unseren Report
    empfangen (das ``R``) und gibt uns seinen. Damit ist das QSO auf
    seiner Seite komplett; ihm fehlt nur noch unser RR73.

    2026-09-10: Bis v0.84.5 fiel dieser Fall zwischen die Zustaende.
    ``_find_report_from_them`` schliesst R-Reports ausdruecklich aus, und
    ``_find_answer_with_report_to_us`` ebenso — mit dem Kommentar "those
    belong to QSO_RESPOND state". In QSO_RESPOND behandelte sie aber
    niemand: Die Box wartete auf einen Report ohne R, der nie kam, und
    lief nach 90 s in den Timeout ("went silent"). Betroffen war der
    Normalfall einer CQ-rufenden Station — jemand ruft uns, wir geben den
    Report, er bestaetigt mit R-Report. Sichtbar wurde es am 2026-09-09:
    15 gesendete Reports, ein einziges RR73; R7OV wiederholte sein R-11
    sogar, bevor er aufgab. Hashed-Call-tolerant wie die Schwesterfunktionen.
    """
    for d in decodes:
        to_ok = _hashed_match(d.call_to, my_call)
        from_ok = _hashed_match(d.call_from, their_call)
        if not (to_ok and from_ok):
            continue
        if d.call_to == "<...>" and d.call_from == "<...>":
            continue
        m = _R_SNR_RE.search(" " + (d.message or ""))
        if m:
            return int(m.group(1))
    return None


def _find_closing(
    decodes: Iterable[DecodedMsg], their_call: str, my_call: str
) -> bool:
    """Decode like ``DK9XR W1AW RR73`` / ``RRR`` / ``73``.
    Hashed-Call-tolerant seit v0.3.4 (siehe _find_report_from_them).
    """
    for d in decodes:
        to_ok = _hashed_match(d.call_to, my_call)
        from_ok = _hashed_match(d.call_from, their_call)
        if not (to_ok and from_ok):
            continue
        if d.call_to == "<...>" and d.call_from == "<...>":
            continue
        tail = d.message.split()[-1].upper()
        if tail in {"RR73", "RRR", "73"}:
            return True
    return False
