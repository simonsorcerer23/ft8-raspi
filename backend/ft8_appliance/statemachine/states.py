"""States, events and dataclasses for the QSO state machine.

Matches ``architecture.md`` §5. Each *State* is what we are doing at the
moment, each *Event* is what triggers a transition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto


class State(Enum):
    IDLE = auto()
    CQ_CALLING = auto()
    QSO_RESPOND = auto()  # we received a call, replying with grid
    QSO_REPORT = auto()  # we sent grid, sending signal report
    QSO_CLOSING = auto()  # waiting to send RR73 / 73
    QSO_LOG = auto()  # write to DB, move to IDLE
    # 1-Slot-Wartefenster nach RR73 + LOG_QSO: falls Partner sein RR73
    # wiederholt (= er hat unser RR73 nicht decodiert), senden wir noch
    # ein 73 (Tx6) hinterher fuer eine saubere QSO-Closure analog WSJT-X.
    # Sebastian 2026-05-24, Audit-Finding 2.
    QSO_GRACE = auto()
    TX_LOCKED = auto()  # guard violated, no TX until manual reset


class Event(Enum):
    USER_START_CQ = auto()
    USER_STOP = auto()
    USER_RESET_LOCK = auto()
    USER_REPLY_TO = auto()  # user picked a call to answer

    DECODE_RECEIVED = auto()  # one or more decodes arrived this slot
    SLOT_TICK = auto()  # 15 s boundary

    GUARD_VIOLATION = auto()  # any pre-flight guard failed
    QSO_COMPLETE = auto()  # full sequence done, log it
    TIMEOUT = auto()  # too many slots without progress in a QSO


@dataclass(slots=True)
class DecodedMsg:
    """A single FT8 decode handed to the state machine."""

    ts: datetime
    call_from: str | None
    call_to: str | None
    grid: str | None
    message: str
    snr_db: int | None
    dt_s: float | None
    freq_offset_hz: int | None
    band: str


@dataclass(slots=True)
class QsoContext:
    """Mutable per-QSO state while we work one station."""

    their_call: str
    their_grid: str | None = None
    their_snr: int | None = None  # snr we send them
    our_snr_received: int | None = None  # snr they send us
    band: str = "20m"
    freq_offset_hz: int = 1500
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    # How many slot ticks the state machine has spent on this QSO without
    # making forward progress. Bumped in on_slot_tick, reset on every
    # successful state transition. The orchestrator aborts the QSO when
    # this exceeds the per-state limit (see QSO_*_MAX_STALE_SLOTS).
    stale_slots: int = 0
    # Wie oft hat der Partner waehrend QSO_RESPOND ein weiteres CQ
    # ausgestrahlt (uns ignoriert) und wir haben unser Grid erneut
    # gesendet? Wenn ueber qso_max_cq_resends → bail + Cooldown setzen.
    # Sebastian sah am 2026-05-22 wie SV9TLU uns 12x in 2 Stunden
    # ignorierte und wir verschwendeten 12 Slots mit erfolglosen
    # Antworten — jeder ein 15-s-TX-Burst der woanders besser angelegt
    # gewesen waere.
    cq_resends: int = 0
    # Analog zu cq_resends, aber fuer QSO_REPORT: Partner sendet uns
    # in einem Slot R-Report zurueck (er hat unsere R-Report nicht
    # gehoert) -> wir senden R-Report erneut, statt direkt in den
    # Timeout zu laufen. Sebastian 2026-05-24 nach UN7JO-QSO-Verlust:
    # Partner repeated -10 statt RR73 zu schicken, wir sind nach 45s
    # rausgeflogen ohne die R-Report-Wiederholung zu versuchen — WSJT-X
    # macht hier standardmaessig einen Re-Send vor dem Aufgeben.
    report_resends: int = 0


@dataclass(slots=True)
class MachineContext:
    """Long-lived state outside any single QSO."""

    callsign: str
    my_grid: str
    band: str = "20m"
    cq_count: int = 0
    last_lock_reason: str | None = None
    # Hunting / Search-and-Pounce mode: auto-answer any decoded CQ
    # while idle. We never call CQ ourselves in this mode — just listen
    # and respond. Architecture §6.1 (Hunting).
    auto_answer: bool = False
    # WSJT-Z-style "Auto CQ": after a QSO completes, automatically return
    # to CQ_CALLING (instead of IDLE). Set when the user presses the CQ
    # button; cleared by Stop. Without this, CQ mode is one-shot.
    auto_cq: bool = False
    # blacklist + worked-before lists are stored in DB; this is just the
    # in-memory set the state machine consults when picking which CQ to
    # auto-answer to.
    blacklist: set[str] = field(default_factory=set)
    skip_worked: bool = False
    # Strenger Award-Modus: NUR neue-DXCC-Calls picken. Wenn keiner
    # in dieser Slot-Welle dabei ist, schweigen wir bewusst statt
    # eine Routine-Station anzurufen.
    dxcc_only_mode: bool = False
    worked: set[str] = field(default_factory=set)
    # Cooldown-Fenster: Call → POSIX-Zeit ab der wir ihn wieder
    # anrufen dürfen. Verhindert dass der Hunting-Picker innerhalb
    # eines Runs dieselbe Station 3x kurz hintereinander wählt
    # weil sie weiter CQ ruft.
    recent_until: dict[str, float] = field(default_factory=dict)
    # Set neuer DXCC-Calls (vom Orchestrator pro Slot aktualisiert).
    # Hunting-Picker priorisiert diese vor SNR — wenn drei Stationen
    # CQ rufen und nur eine ist aus einem neuen Land, wählen wir die
    # auch wenn ihr SNR schlechter ist als die anderen.
    new_dxcc_calls: set[str] = field(default_factory=set)
    prefer_new_dxcc: bool = True
    # SNR-Floor fuer den Hunting-Picker (dB). Stationen die mit Decode-
    # SNR unter diesem Schwellwert ankommen werden gefiltert — die hoeren
    # uns wahrscheinlich nicht. Sebastian 2026-05-22: empirisch -22 dB
    # als Default basierend auf rst_rcvd-Median -10 dB. None = aus.
    hunt_snr_floor_db: int | None = -22
    # Audio-Frequenz-Filter im Picker. Decodes mit freq_offset_hz ausserhalb
    # [min, max] werden uebersprungen weil unser Reply dann durch den
    # Rig-Bandpass gedaempft wuerde (Sebastian sah 2026-05-22 wie ein
    # Reply auf 262 Hz Audio den PI in einen PWR-Spike+Watchdog-Cut trieb).
    # None,None = aus.
    hunt_audio_freq_min_hz: int | None = 400
    hunt_audio_freq_max_hz: int | None = 2600
    # CQ-TX-Slot-Parity: "even" oder "odd". None = altes Verhalten
    # (TX in jedem Slot) zur Backward-Compat — aber das ist effektiv
    # der Bug der RX killed, also sollte produktiv immer "even" oder
    # "odd" gesetzt sein.
    cq_tx_slot_parity: str | None = "even"
    # TX-audio-frequency rotation for CQ (Hz). Cycles through this list
    # slot-by-slot so we don't keep colliding with the same QRM on a fixed
    # spot. Default: 4 spread points across the FT8 passband.
    cq_freq_rotation: list[int] = field(default_factory=lambda: [1200, 1500, 1800, 2100])
    cq_freq_index: int = 0
