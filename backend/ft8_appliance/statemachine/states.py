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
    # True wenn die Message keinem Standard-FT8-Pattern entspricht
    # (Free-Text Tx5/Tx6 wie "73 GL", "TU JIM", "NAME RAY"). Audit F8
    # v0.3.4. Hunting-Picker und Worked-Set ignorieren is_freetext-
    # Decodes damit Junk-Tokens nicht als Calls behandelt werden.
    is_freetext: bool = False


@dataclass(slots=True)
class QsoContext:
    """Mutable per-QSO state while we work one station."""

    their_call: str
    their_grid: str | None = None
    their_snr: int | None = None  # snr we send them (= rst_sent fuer Log)
    our_snr_received: int | None = None  # snr they send us (= rst_rcvd fuer Log)
    # SNR den WIR von IHM gemessen haben — Initialwert vom Pickup-Decode,
    # dann bei jedem weiteren Decode des Partners im QSO-Verlauf auf
    # den aktuellsten Wert getrackt. Sebastian 2026-05-24 Audit-Action 5:
    # WSJT-X-Konformanz fuer R-Report (R + SNR-of-them-at-us statt Echo
    # von our_snr_received). None bis erster Decode-mit-SNR vorliegt.
    their_snr_at_us: int | None = None
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
    # v0.18.0 — TX-Audio-Freq Smart-Hop. Wenn der Partner uns nach
    # max_resends nicht hoert ODER went_silent: einmalig die TX-Freq
    # +/-200 Hz wechseln und nochmal versuchen statt sofort zu bailen.
    # Vielleicht haben wir gerade QRM auf der Freq bei IHM. Set wird
    # gesetzt sobald der Hop passiert; danach normaler Bail-Pfad.
    freq_hopped_once: bool = False
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
    # weil sie weiter CQ ruft. KURZER Failed/Reply-Cooldown, call-weit
    # (band-unabhängig — wer gerade abgebrochen hat, soll auch auf einem
    # anderen Band kurz Ruhe haben).
    recent_until: dict[str, float] = field(default_factory=dict)
    # v0.32.0 — Erfolgs-Cooldown nach abgeschlossenem QSO, BAND-BEWUSST:
    # (Call, Band) → POSIX-Zeit. Damit ein langer Cooldown (z.B. 6 h)
    # dieselbe Station nicht für einen NEUEN Band-Slot blockt (5BWAS/
    # Band-Füllen): W1AW auf 20m gearbeitet → 15m bleibt frei wählbar.
    worked_until: dict[tuple[str, str], float] = field(default_factory=dict)
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
    # Directed-CQ-Target (Audit F7, v0.3.4): leer = klassischer CQ,
    # sonst eines von "DX", "EU", "NA", "POTA", "TEST", ... — wird vor
    # dem Callsign in die CQ-Message eingefuegt: "CQ DX DK9XR JN58".
    # Aus OperatingConfig.cq_directed gehydratet.
    cq_directed: str = ""
    # v0.10.0 Hunt-Priority-Tiers
    # ────────────────────────────────────────────────────────────────
    # Reihenfolge der Picker-Tiers. Aus OperatingConfig.hunt_priority
    # gehydratet. Leerstring-Namen werden vom Picker ignoriert.
    hunt_priority: list[str] = field(default_factory=list)
    # Marinefunker-Mitglieds-Set (normalisierte Calls). Vom Orchestrator
    # beim Boot aus marinefunker.json befüllt.
    marine_calls: set[str] = field(default_factory=set)
    # PSK-Reciprocity: Set normalisierter Calls die uns recently gehört
    # haben (laut pskreporter.info). Vom Orchestrator alle paar Minuten
    # aktualisiert. Leer wenn psk_reciprocity_enabled=False.
    psk_heard_us: set[str] = field(default_factory=set)
    # v0.30.0 — Pick-Attempt-Telemetrie fuer das psk_heard_us-A/B. Beim
    # Hunt-Pick schreibt die Machine hier {base_call: {psk_heard_us, snr_db,
    # dt_s, band, ts}}; der Orchestrator liest+poppt es beim QSO-Ausgang
    # (LOG_QSO/QSO_BAIL) und schreibt eine pick_attempt-Zeile. Reine
    # Messung — beeinflusst die Pick-Logik NICHT.
    hunt_attempt_meta: dict[str, dict] = field(default_factory=dict)
    # 5BWAS-Tracking: (dxcc_entity_name, band) Tuples die wir bereits
    # bestätigt haben. Vom Orchestrator beim Boot aus DB rekonstruiert
    # und bei jedem LOG_QSO upgedated.
    worked_dxcc_band: set[tuple[str, str]] = field(default_factory=set)
    # VUCC-Tracking (v0.10.2): worked 4-char grid squares + (grid, band) tuples.
    # Wird vom Orchestrator aus _worked_grids / _worked_grid_band gespiegelt.
    worked_grids: set[str] = field(default_factory=set)
    worked_grid_band: set[tuple[str, str]] = field(default_factory=set)
    # DXCC-Rarity-Lookup: call_from → DXCC-Entity (für Tier 8). Vom
    # Orchestrator pro Slot mit den frisch geseten Calls befüllt.
    # Wert ist der Rarity-Score (0..100). Aus integrations.dxcc_rarity
    # geladen.
    rarity_scores: dict[str, int] = field(default_factory=dict)
    # call_from → dxcc_entity_name (für 5BWAS-Check). Auch pro Slot
    # vom Orchestrator befüllt aus cty.dat-Lookup.
    call_to_dxcc: dict[str, str] = field(default_factory=dict)
    # v0.11.0 Tail-End-Hunter
    # ────────────────────────────────────────────────────────────────
    # Toggle: aus OperatingConfig.tail_end_hunter_enabled gespiegelt.
    # Wenn False: Detection läuft nicht, Candidates bleibt leer.
    tail_end_hunter_enabled: bool = False
    # Aktive Tail-End-Candidates: Stationen die in den letzten 30 s ein
    # Closing (RR73/RRR/73) gesendet haben und damit jetzt frei sind
    # für direkten Anruf wie nach CQ. Key: call_from (uppercase),
    # Value: {"expiry": posix, "snr_db": int|None, "freq_offset_hz":
    # int|None, "band": str, "grid": str|None}. Expiry-Pflege passiert
    # im State-Machine-Slot-Tick.
    tail_end_candidates: dict[str, dict] = field(default_factory=dict)
    # 24h-Cooldown pro Station: verhindert dass wir denselben Op an
    # einem Tag mehrfach per Tail-End anrufen — wäre nervig fuer ihn
    # und uns. Key: call (uppercase), Value: posix-Timestamp des
    # letzten Tail-End-Picks.
    tail_end_last_pick: dict[str, float] = field(default_factory=dict)
    # Letzter-CQ-Zeitstempel pro Call: wer in den letzten 5 min selber
    # CQ ruft, braucht keinen Tail-End-Boost (sein naechster CQ kommt
    # eh, wir koennen normal antworten). Verhindert dass routinemaessige
    # CQ-Rufer mit jedem Closing in den Tail-End-Tier rutschen.
    tail_end_recent_cq: dict[str, float] = field(default_factory=dict)
    # v0.14.0 Watchlist — Calls die wir aktiv beobachten (DXpeditions etc.).
    # Bei jedem Decode eines Watchlist-Calls feuert der Orchestrator eine
    # ntfy-Push mit Action-Buttons. In-memory-Mirror der DB-Watchlist-
    # Tabelle, vom Orchestrator pro Slot aus DB synchronisiert.
    watchlist_calls: set[str] = field(default_factory=set)
    # v0.14.0 Grayline-Tier — pro Slot vom Orchestrator befuellt: call_from
    # (uppercase) → (lat, lon) aus cty.dat-Lookup. Nur Calls deren DXCC-
    # Entity wir mit Lat/Lon kennen sind drin (compound calls ohne klares
    # Land fallen raus).
    call_to_latlon: dict[str, tuple[float, float]] = field(default_factory=dict)
    # v0.14.0 Propagation-Tier — hamqsl-Conditions pro Band ("Good"/"Fair"/
    # "Poor"). Vom Orchestrator alle 30 min aus HamQslClient.solar() befuellt.
    # Key: Band-Bucket ("80m-40m", "30m-20m", "17m-15m", "12m-10m"); Value:
    # dict {"day": "Good", "night": "Poor"}. Leer wenn hamqsl nicht erreichbar.
    band_conditions_day: dict[str, str] = field(default_factory=dict)
    band_conditions_night: dict[str, str] = field(default_factory=dict)
    # v0.15.0 Soft-Blacklist — Calls die nach Reason-Aware-Scoring
    # systematisch nicht reagieren (Score >= 5 nach >=3 Versuchen).
    # picked_another zaehlt NICHT — das ist Propagation-Pech, nicht
    # Operator-Verhalten. Vom Orchestrator gepflegt, hier nur Read-Set.
    soft_blacklist: set[str] = field(default_factory=set)
    # v0.15.0 Slot-Parity-Predictor — Op → "even" | "odd" | None (unknown).
    # Aus Beobachtung der TX-Slots in Decodes. Picker meidet Calls deren
    # Slot-Parity gerade SEIN TX-Slot ist (er hoert uns nicht waehrend
    # seinem eigenen Senden).
    op_slot_parity: dict[str, str] = field(default_factory=dict)
    # Aktuelle Slot-Parity dieses Slots — vom Orchestrator pro Slot
    # gesetzt aus SlotTick. "even" oder "odd".
    current_slot_parity: str = ""
    # v0.16.0 Hour-of-Day-Predictor — fuer jeden Decoded-Call cachen wir
    # den Continent (aus cty.dat). Tier `active_hour` schaut nach ob
    # die aktuelle UTC-Hour historisch ein "aktive Stunde" fuer diesen
    # Continent ist (aus eigener QSO-DB aggregiert).
    call_to_continent: dict[str, str] = field(default_factory=dict)
    # Set of (continent, hour) Tupeln die laut DB-History "aktiv" sind
    # (Top-50% Stunden pro Continent). Vom Orchestrator beim Boot +
    # periodisch aus call_reputation/qso-Daten aggregiert.
    active_continent_hours: set[tuple[str, int]] = field(default_factory=set)
    # v0.16.0 Tail-End-PreStage — Calls die wir gerade in QSO_REPORT
    # gesehen haben (R-Report decoded). Cached snr/freq/band/grid damit
    # bei RR73 im naechsten Slot die Detection schon "warm" ist und der
    # 5-min-CQ-Filter uebersteuert wird.
    pre_staged_tail_ends: dict[str, dict] = field(default_factory=dict)
    # v0.17.0 Buddy-Seen-Tier — set von (call, band) die wir bereits
    # gearbeitet haben. Tier `buddy_seen` liefert 1 wenn call in worked
    # ABER (call, band) NICHT gearbeitet → "wir wissen er hoert uns,
    # nur Band ist neu".
    worked_call_band: set[tuple[str, str]] = field(default_factory=set)
    # v0.18.0 — Freq-Reputation: pro (band, audio_bin_hz) ein Tuple
    # (attempts, successes). Vom Orchestrator pro Slot gespiegelt.
    # Smart-CQ-Picker biased zu erfolgreichen Bins.
    freq_reputation: dict[tuple[str, int], tuple[int, int]] = field(
        default_factory=dict
    )
    # v0.19.0 — Pile-Up-Detection: Calls die wahrscheinlich in einem
    # Pile-Up stecken (rare DX mit vielen Callern auf der Frequenz).
    # Vom Orchestrator pro Slot aus Decode-Pattern erkannt. Tier
    # `not_in_pileup` liefert 0 fuer diese Calls = Picker pickt sie
    # nur wenn andere Tiers grün sind.
    pile_up_calls: set[str] = field(default_factory=set)
    # v0.22.0 — DX-Operating-Location. Wenn current_operating_country
    # gesetzt UND != home_country, wird der TX-Callsign zu
    # "<prefix>/<callsign>" (z.B. 9A/DK9XR). Beide aus OperatorConfig
    # gespiegelt vom Orchestrator pro Slot.
    home_country: str = "DL"
    current_operating_country: str | None = None
    # v0.29.0 — Modifier-Suffix (AM/MM/P/QRP). Wird mit dem DX-Prefix
    # kombiniert: 9A + AM → "9A/DK9XR/AM".
    current_operating_suffix: str | None = None

    @property
    def tx_callsign(self) -> str:
        """Effektiver TX-Callsign — mit DX-Prefix (Auslandsbetrieb) und/oder
        Modifier-Suffix (/AM /MM …), sonst nur der Heimat-Call. Wird von
        allen _emit_*-Helpers in der State-Machine benutzt.
        """
        call = self.callsign
        oc = self.current_operating_country
        if oc and oc != self.home_country:
            call = f"{oc}/{call}"
        if self.current_operating_suffix:
            call = f"{call}/{self.current_operating_suffix}"
        return call
