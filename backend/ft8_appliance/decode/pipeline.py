"""High-level FT8 decode source — the bridge between audio and state machine.

``DecodePipeline`` is the production :class:`DecodeSource` consumed by
the :class:`Orchestrator`. For every slot tick it:

1. extracts the matching 15-s window from a :class:`SlotBuffer`
2. feeds the PCM into ``ft8_lib`` via :func:`decode_slot`
3. parses the resulting message strings into :class:`DecodedMsg`
4. records the audio drift for the next round of guard checks

The actual audio capture (ALSA on Pi, WAV file in tests) lives
*outside* this class. The orchestrator owns the capture task and feeds
its samples into the SlotBuffer.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..audio.slot_sync import SlotBuffer, SlotExtraction
from ..runtime.slot_clock import SlotTick
from ..statemachine import DecodedMsg
from .ft8_native import (
    ShimDecode,
    decode_slot,
    decode_slot_ft4,
    decode_slot_ft4_v2,
    decode_slot_v2,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message parsers — FT8 messages have a stylised grammar, no AI needed.

# Grid: 2 letters A-R + 2 digits, optionally 2 subsquare letters.
# RR73 happens to match the 4-char form so we filter closing tokens explicitly.
_GRID_RE = re.compile(r"[A-R]{2}[0-9]{2}([a-x]{2})?")
_SIGNAL_REPORT_RE = re.compile(r"R?[+-]\d{1,2}")
_CLOSING_TOKENS = {"RR73", "RRR", "73"}
# Strikte Callsign-Heuristik (Audit F8 v0.3.4): mind. 1 Buchstabe und
# mind. 1 Ziffer, 3-11 Zeichen, nur A-Z/0-9/`/`. Filtert "73", "GL",
# "TU" etc. die in Free-Text-Messages wie "73 GL" als 1./2. Token
# vorkommen wuerden und sonst faelschlich als Call interpretiert
# wuerden. Erlaubt Compound-Calls (DL/W1AW, DK9XR/P) + Standard.
# Hashed-Calls "<...>" werden separat behandelt (s. _is_callsign_like).
_CALLSIGN_RE = re.compile(r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9/]{3,11}$")


def _is_callsign_like(token: str) -> bool:
    """True wenn der Token wie ein Callsign aussieht oder ein Hashed-
    Placeholder ist. Konservativ — bei False werden Tokens als Free-
    Text-Indikator gewertet (Audit F8 v0.3.4)."""
    if not token:
        return False
    if token == "<...>":
        return True
    return bool(_CALLSIGN_RE.fullmatch(token))


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    call_from: str | None
    call_to: str | None
    grid: str | None
    report: str | None  # e.g. "-10" or "R-10"
    is_cq: bool
    is_freetext: bool = False
    # Token nach "CQ", wenn es kein Call ist: DX, EU, NA, JA, POTA, TEST …
    # (2026-09-06: der Picker soll "CQ NA" nicht aus Europa beantworten.)
    cq_directed: str | None = None


def parse_message(text: str) -> ParsedMessage:
    """Best-effort parser for the common FT8 message shapes.

    Recognises:
      * ``CQ <call> <grid>``      → call_from + grid, is_cq=True
      * ``CQ DX <call> <grid>``   → same (directed CQ)
      * ``CQ EU <call> <grid>``   → same (continent CQ)
      * ``CQ POTA <call> <grid>`` → same (award)
      * ``<to> <from> <grid>``    → call_to + call_from + grid
      * ``<to> <from> <report>``  → call_to + call_from + report
      * ``<to> <from> RR73 / 73`` → call_to + call_from

    Free-Text Tx5/Tx6 (Audit F8 v0.3.4): Messages die nicht in diese
    Patterns passen werden mit ``is_freetext=True`` markiert. Beispiele:
    "73 GL", "TU JIM", "5W ENDFED". Die Tokens werden NICHT als
    Callsign-Felder weitergeleitet damit Junk nicht in der worked-Liste
    landet — call_from/call_to bleiben None.
    """
    tokens = text.strip().split()
    if not tokens:
        return ParsedMessage(None, None, None, None, False, is_freetext=False)

    # v0.6.4: angle-brackets entfernen aus call-tokens — Display-Konvention
    # vom Decoder fuer compound/hashed calls. State-Machine + Picker +
    # TX-Synth brauchen den nackten Call. Aber: literal "<...>" (unresolvable)
    # behalten wir damit Downstream-Logic weiss "Partner-Call ist hash,
    # nicht decode-able".
    def _strip_brackets(tok: str) -> str:
        if tok.startswith("<") and tok.endswith(">") and len(tok) > 2:
            inner = tok[1:-1]
            # Echte Resolution: alphanumerisch+/ → strip
            # Unresolvable "..." → behalten als "<...>"
            if inner == "...":
                return tok
            return inner
        return tok
    tokens = [_strip_brackets(t) for t in tokens]

    if tokens[0] == "CQ":
        # CQ <call> <grid> or CQ <REGION/AWARD> <call> <grid>.
        # The second token is a region/award (DX, EU, NA, POTA, SOTA, WW,
        # contest tags) when it contains no digits — real callsigns
        # always have at least one digit. That filter is more robust than
        # a length cap and admits 4-letter awards like POTA/SOTA.
        rest = tokens[1:]
        directed: str | None = None
        if (
            len(rest) >= 2
            and rest[0].isalpha()
            and rest[0] != "CQ"
        ):
            directed = rest[0].upper()
            rest = rest[1:]
        if not rest:
            return ParsedMessage(None, None, None, None, True, cq_directed=directed)
        call_from = rest[0]
        grid = rest[1] if len(rest) >= 2 and _GRID_RE.fullmatch(rest[1]) else None
        return ParsedMessage(
            call_from=call_from, call_to=None, grid=grid, report=None, is_cq=True,
            cq_directed=directed,
        )

    if len(tokens) >= 2:
        call_to, call_from = tokens[0], tokens[1]
        # Free-Text-Detection (Audit F8 v0.3.4): wenn weder call_to noch
        # call_from wie ein Callsign aussehen, ist's Free-Text. Wir
        # markieren is_freetext + lassen call_*/grid/report None damit
        # Downstream (Picker, worked-set) den Junk nicht aufgreift.
        if not (_is_callsign_like(call_to) or _is_callsign_like(call_from)):
            return ParsedMessage(
                call_from=None, call_to=None, grid=None, report=None,
                is_cq=False, is_freetext=True,
            )
        report = None
        grid = None
        if len(tokens) >= 3:
            tail = tokens[2]
            if tail in _CLOSING_TOKENS:
                pass  # RR73 / RRR / 73 — neither grid nor report
            elif _SIGNAL_REPORT_RE.fullmatch(tail):
                report = tail
            elif _GRID_RE.fullmatch(tail):
                grid = tail
        return ParsedMessage(
            call_from=call_from, call_to=call_to, grid=grid, report=report, is_cq=False
        )

    return ParsedMessage(None, None, None, None, False, is_freetext=True)


# ---------------------------------------------------------------------------
# Pipeline

SlotExtractor = Callable[[SlotTick], SlotExtraction]


@dataclass(slots=True)
class DecodePipelineMetrics:
    slots_decoded: int = 0
    decodes_total: int = 0
    last_drift_samples: int = 0
    last_decode_count: int = 0
    # Rolling-Fenster der letzten N Decode-Counts für /min-Schätzung.
    # 4 Slots ≈ 60 s, ergibt direkt "Decodes/min" wenn man summiert.
    recent_counts: list[int] = field(default_factory=list)
    RECENT_WINDOW: int = 4  # 4 × 15 s = 1 min
    # v0.6.0 Anti-WSJT-X-Audit Phase A1: Decoder-Timing.
    # Misst wie lange decode_slot pro Tick brauchte. Wenn das
    # konsistent gegen die Slot-Laenge laeuft (~12s bei FT8),
    # ueberlaeuft der naechste Slot bevor wir fertig sind →
    # WSJT-X-Aequivalent "decoder not keeping up". Late-Slot-
    # Watchdog kann darauf alerten.
    last_decode_duration_s: float = 0.0
    max_decode_duration_s: float = 0.0
    recent_durations_s: list[float] = field(default_factory=list)
    late_slot_count: int = 0  # mehr als 80% der Slot-Laenge
    # Zweistufiger Decoder (2026-09-06): Stufe 2 laeuft nach der
    # TX-Entscheidung. Was sie zusaetzlich fand, wie lange sie brauchte,
    # und wie oft sie uebersprungen wurde, weil die vorige noch lief.
    late_pass_last_count: int = 0
    late_decodes_total: int = 0
    late_pass_last_duration_s: float = 0.0
    late_pass_skipped: int = 0
    # Stufe 3 (jt9, 2026-09-08)
    jt9_last_count: int = 0
    jt9_total: int = 0
    jt9_last_duration_s: float = 0.0
    jt9_skipped: int = 0
    jt9_failed: int = 0
    jt9_last_depth: int = 0

    def note_late_pass(self, count: int, duration_s: float) -> None:
        self.late_pass_last_count = count
        self.late_decodes_total += count
        self.late_pass_last_duration_s = duration_s
        # Die adaptive LDPC-Steuerung schaut auf recent_durations_s. Der
        # teure Pass gehoert da hinein, sonst hielte sie den Slot fuer
        # fast leer und drehte die Iterationen hoch.
        if self.recent_durations_s and duration_s > self.recent_durations_s[-1]:
            self.recent_durations_s[-1] = duration_s
        if duration_s > self.max_decode_duration_s:
            self.max_decode_duration_s = duration_s

    def record_slot(self, count: int, duration_s: float = 0.0) -> None:
        """Updater used by the pipeline after each decode pass."""
        self.slots_decoded += 1
        self.decodes_total += count
        self.last_decode_count = count
        self.recent_counts.append(count)
        if len(self.recent_counts) > self.RECENT_WINDOW:
            del self.recent_counts[: len(self.recent_counts) - self.RECENT_WINDOW]
        # Timing-Stats
        self.last_decode_duration_s = duration_s
        if duration_s > self.max_decode_duration_s:
            self.max_decode_duration_s = duration_s
        self.recent_durations_s.append(duration_s)
        if len(self.recent_durations_s) > self.RECENT_WINDOW:
            del self.recent_durations_s[: len(self.recent_durations_s) - self.RECENT_WINDOW]

    @property
    def decodes_per_min(self) -> int:
        return sum(self.recent_counts)

    @property
    def avg_decode_duration_s(self) -> float:
        if not self.recent_durations_s:
            return 0.0
        return sum(self.recent_durations_s) / len(self.recent_durations_s)


@dataclass
class DecodePipeline:
    """A :class:`DecodeSource` backed by ``ft8_lib``.

    Audit F6 v0.4.0: mode-aware. ``mode="FT8"`` (default) verwendet
    den 15s-Decoder, ``mode="FT4"`` den 7.5s-Decoder mit halber
    Slot-Window. Beide Modi reichen denselben SlotBuffer, nur die
    Window-Groesse + Decoder-Funktion variieren.

    Sebastian-Bugfix v0.5.1: ``band_resolver`` (optional Callable)
    erlaubt live-Bestimmung des aktuellen Bands aus dem Rig-Snapshot
    statt statischer ``band_hint``-Konstante. Vorher fielen alle
    QSOs auf bands[0].name zurueck (= "20m" weil das erste konfig-
    Band 20m war) auch wenn das Rig auf 21.140 MHz (15m) stand.
    """

    slot_buffer: SlotBuffer
    band_hint: str = "20m"  # static fallback wenn band_resolver None
    band_resolver: callable | None = None  # () -> str | None, live-band-Lookup
    metrics: DecodePipelineMetrics = field(default_factory=DecodePipelineMetrics)
    mode: str = "FT8"  # "FT8" oder "FT4"
    # v0.7.0 Build 3: Auto-Notch fuer lokale QRM-Traeger.
    # NotchDetector wird per Slot mit Audio gefuettert + periodisch
    # analysiert. apply_notches strippt erkannte QRM-Linien bevor
    # decode_slot drueber laeuft. None = Auto-Notch deaktiviert
    # (Default ON in production.py).
    notch_detector: NotchDetector | None = None  # noqa: F821 — fwd ref
    # v0.8.0 Build B: DT-Auto-Kalibrierung. Wenn der Orchestrator-Watchdog
    # einen systemic Audio-Offset misst (Median-DT >0.3s ueber 100+ Decodes),
    # wird der Wert hier gesetzt. Pipeline shiftet slot_start_posix beim
    # extract um diesen Offset → Decoder sieht zentrierte DTs.
    dt_calibration_s: float = 0.0
    # v0.8.0 Build D: Adaptive LDPC-Iter-Factor. 1.0 = Standard. >1.0
    # bei CPU-Reserve (mehr Iter → mehr marginal-decodes recovered).
    # <1.0 bei CPU-Druck (weniger Iter → schneller, fewer marginal).
    # Wert wird dynamisch von metrics.avg_decode_duration_s abgeleitet
    # pro Slot (siehe __call__).
    ldpc_factor: float = 1.0
    # v0.6.0 Phase B/C: Decoder-Tuning. Standard = altes Verhalten (osr=2,
    # LDPC=25). Deep = osr=4/LDPC=50. Multi = Pass1+Pass2-Merge. FT4
    # nutzt immer Standard-Decoder (FT4-Decoder hat keine Deep-Variante).
    decoder_mode: str = "standard"  # "standard" | "deep" | "multi"
    _consecutive_late_slots: int = 0  # CPU-adaptive Fallback-Trigger
    _threads_logged: bool = False     # 2026-09-09: Thread-Zahl einmal ins Log
    # Zweistufiger Decoder (2026-09-06). Gemessen am Pi 4B: der Sendestart
    # lag im extreme-Modus 2,8 s nach der Slot-Grenze, weil der ganze
    # Decoder VOR der TX-Entscheidung laeuft — ausserhalb des ±2,5-s-
    # Fensters der Partner-Decoder. Der Standard-Pass allein braucht 0,25 s
    # und liefert ~94 % der Decodes (Messung Mai). Also: Stufe 1 (standard)
    # entscheidet ueber TX, Stufe 2 (der Rest des gewaehlten Modus) laeuft
    # in einem eigenen Thread nebenher und reicht nach, was sie zusaetzlich
    # findet — an late_pass_sink (Orchestrator: UI, DB, PSK, State-Machine).
    two_stage: bool = True
    late_pass_sink: Callable[[list[DecodedMsg], SlotTick], Awaitable[None]] | None = None
    # LDPC-Faktor (Prozent) fuer Stufe 2; Stufe 1 behaelt den adaptiven.
    late_ldpc_pct: int = 250
    # Stufe 3 (2026-09-08): WSJT-X' jt9 als Unterprozess, siehe decode/jt9.py.
    # Laeuft parallel zu Stufe 2 im eigenen Thread; Ergebnisse gehen ueber
    # denselben late_pass_sink. Auto-aus, wenn kein jt9 installiert ist.
    jt9_enabled: bool = True
    jt9_depth: int = 2
    jt9_timeout_s: float = 12.0
    # 2026-09-08: Tiefe 3, wenn wir im naechsten Slot ohnehin senden (QSO/CQ):
    # dann hat jt9 30 statt 15 s bis zur naechsten Entscheidung. Setzt der
    # Orchestrator pro Slot. Ueberlaeuft Tiefe 3 trotzdem, 10 min Sperre.
    jt9_depth_boost: bool = False
    jt9_ft4: bool = False          # erst nach Messung am Pi freigeben
    jt9_ap: bool = False           # -c/-G/-x/-g/-X an jt9 (AP) — erst nach Messung
    jt9_ap_flags: int = 0
    jt9_my_call: str | None = None
    jt9_my_grid: str | None = None
    jt9_his_call: str | None = None
    jt9_his_grid: str | None = None
    _jt9_boost_blocked_until: float = field(default=0.0, init=False)
    _jt9_task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _jt9_executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="jt9"),
        init=False, repr=False,
    )
    # Pro Slot: Nachrichten, die schon ausgeliefert wurden (Stufe 1/2/3) —
    # jt9 und Stufe 2 finden vieles doppelt, nur das Neue geht an den Sink.
    _slot_seen: dict[int, set[str]] = field(default_factory=dict, init=False, repr=False)
    _late_task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _late_overruns: int = field(default=0, init=False, repr=False)
    _late_executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="ft8-late"),
        init=False, repr=False,
    )
    # USB-Audio kommt in 1024-sample-periods (~85 ms). Wenn der Slot
    # genau zum Wallclock-Boundary endet, ist die letzte Period oft noch
    # nicht in den SlotBuffer geflossen → "short by X samples"-Logs +
    # Verlust der letzten Symbole. 150 ms Warten überbrückt eine volle
    # ALSA-Period plus Scheduling-Slack ohne den nächsten Slot zu blocken.
    extract_delay_s: float = 0.15

    async def __call__(self, tick: SlotTick) -> list[DecodedMsg]:
        from ..audio.slot_sync import (
            FT4_SLOT_SECONDS,
            FT4_TX_SECONDS,
            FT8_TX_SECONDS,
            SLOT_SECONDS,
        )

        # Mode-aware Slot-Window + Decoder-Funktion (Audit F6 v0.4.0).
        # v0.6.0 Phase C: FT8-Pfad waehlt Decoder-Variante via decoder_mode.
        # FT4 hat keine Deep-Variante (FT4-Decoder ist eigene C-Funktion).
        if self.mode == "FT4":
            slot_seconds = FT4_SLOT_SECONDS
            signal_seconds = FT4_TX_SECONDS
            # v0.8.0 Build I: FT4 mode-aware. Bei deep/multi/extreme
            # nutzt die v2-Variante des FT4-Shim (osr=4, kein Subtract).
            if self.decoder_mode in ("deep", "multi", "extreme"):
                _mode = self.decoder_mode
                decoder = lambda pcm: decode_slot_ft4_v2(pcm, mode=_mode)  # noqa: E731
            else:
                decoder = decode_slot_ft4
        else:
            slot_seconds = SLOT_SECONDS
            signal_seconds = FT8_TX_SECONDS
            if self.decoder_mode in ("deep", "multi", "extreme"):
                _mode = self.decoder_mode
                decoder = lambda pcm: decode_slot_v2(pcm, mode=_mode)  # noqa: E731
            else:
                decoder = decode_slot

        # Warte kurz damit die letzten Audio-Frames die Capture-Pipeline
        # erreichen — sonst zero-padden wir das Slot-Ende und der
        # Decoder sieht Stummheit wo eigentlich noch FT8-Symbole sind.
        if self.extract_delay_s > 0:
            await asyncio.sleep(self.extract_delay_s)
        slot_start_posix = tick.posix - slot_seconds
        # v0.8.0 Build B: DT-Auto-Kalibrierung anwenden. Positive
        # Kalibrierung (Audio kommt spaet) → wir extrahieren spaeter
        # vom Slot-Boundary aus → Decoder sieht Symbole zentriert.
        if self.dt_calibration_s != 0.0:
            slot_start_posix += self.dt_calibration_s
        extraction = self.slot_buffer.extract_slot(
            slot_start_posix, slot_seconds=slot_seconds, signal_seconds=signal_seconds
        )
        self.metrics.last_drift_samples = extraction.drift_samples

        # v0.7.0 Build 3: Auto-Notch — wenn Detector aktive QRM-Linien
        # gefunden hat, strippe sie aus dem Slot bevor Decoder drueber
        # laeuft. FFT-Spektral-Subtract, numpy-only, ~10ms pro Slot.
        pcm_for_decode = extraction.pcm_s16le
        if self.notch_detector is not None:
            from ..audio.notch import apply_notches
            self.notch_detector.feed(pcm_for_decode)
            self.notch_detector.maybe_update()
            notches = self.notch_detector.active_notches_hz
            if notches:
                pcm_for_decode = apply_notches(pcm_for_decode, notches)

        # decode_slot is a synchronous C call (~200-800 ms on a Pi). Running
        # it directly in the asyncio event loop would freeze the rig poll,
        # gpsd consumer, and SSE streams for the whole duration. Push it to
        # the default ThreadPoolExecutor so the loop stays responsive.
        # v0.8.0 Build D: Adaptive LDPC-Iter-Factor.
        # Pi-5-Power-Mode: bei CPU-Reserve hoeher (mehr Iter, mehr
        # marginal-decodes), bei CPU-Druck niedriger (Slot-Drop-Schutz).
        # Basis: avg_decode_duration_s relativ zur Slot-Laenge.
        if self.metrics.recent_durations_s:
            avg_load = self.metrics.avg_decode_duration_s / max(slot_seconds, 1)
            if avg_load < 0.15:    # <15% Slot-Last → CPU-frei
                factor = 200       # 2x Iter
            elif avg_load < 0.30:  # 15-30% → komfortabel
                factor = 150
            elif avg_load < 0.60:  # 30-60% → moderate
                factor = 100
            else:                  # >60% → CPU-Druck
                factor = 60
            try:
                from .ft8_native import lib as _ft8_lib
                _ft8_lib.ft8_shim_set_ldpc_factor(factor)
            except Exception:
                pass

        # Zweistufig: Stufe 1 ist immer der schnelle Standard-Pass, der
        # gewaehlte Modus laeuft als Stufe 2 nebenher (siehe two_stage).
        late_decoder = None
        if (
            self.two_stage
            and self.late_pass_sink is not None
            and self.decoder_mode in ("deep", "multi", "extreme")
        ):
            late_decoder = decoder
            decoder = decode_slot_ft4 if self.mode == "FT4" else decode_slot

        loop = asyncio.get_running_loop()
        # v0.6.0 Phase A1: Timing-Messung — kann der Decoder den Slot halten?
        import time as _time
        t0 = _time.monotonic()
        try:
            raw = await loop.run_in_executor(None, decoder, pcm_for_decode)
        except Exception as exc:
            log.warning("%s decode_slot failed for tick %s: %s", self.mode, tick.index, exc)
            return []
        duration_s = _time.monotonic() - t0
        if not self._threads_logged:
            self._threads_logged = True
            try:
                from .ft8_native import decoder_threads
                log.info(
                    "Decoder: %d Threads je Kandidatenschleife, Stufe 1 = %s (%.0f ms)",
                    decoder_threads(), "standard" if late_decoder is not None else self.decoder_mode,
                    duration_s * 1000,
                )
            except Exception:  # noqa: BLE001 — Diagnose darf nie den Slot kosten
                pass
        self._slot_seen[tick.index] = {r.message for r in raw}
        for old_idx in [i for i in self._slot_seen if i < tick.index - 3]:
            self._slot_seen.pop(old_idx, None)

        self.metrics.record_slot(len(raw), duration_s=duration_s)
        # Late-Slot-Detection: wenn der Decoder >80% der Slot-Laenge
        # braucht, ist er nahe am Limit. Bei FT8: >12s von 15s.
        # Bei FT4: >6s von 7.5s. Pi 5 sollte unter 1s bleiben, Pi 4
        # 1-3s. Wenn wir Richtung 80%+ kriechen, ist die CPU am Limit
        # und ggf. der naechste Slot wird verpasst.
        late_threshold_s = 0.8 * slot_seconds
        if duration_s > late_threshold_s:
            self.metrics.late_slot_count += 1
            self._consecutive_late_slots += 1
            log.warning(
                "%s decoder LATE: %.2fs (>%.0f%% von %ds Slot) — CPU am Limit?",
                self.mode, duration_s, late_threshold_s / slot_seconds * 100,
                slot_seconds,
            )
            # CPU-adaptive (Phase C): wenn 3+ Slots in Folge late UND
            # wir laufen im teureren Modus, automatisch zurueck zu
            # standard. Verhindert Slot-Drops bei wechselnder CPU-Last.
            if self._consecutive_late_slots >= 3 and self.decoder_mode in (
                "deep", "multi", "extreme",
            ):
                log.warning(
                    "decoder auto-fallback: %s → standard (3+ late slots)",
                    self.decoder_mode,
                )
                self.decoder_mode = "standard"
        else:
            self._consecutive_late_slots = 0

        # Live-Band-Resolve (Sebastian v0.5.1): falls Resolver konfiguriert,
        # nimm den aktuellen Wert aus dem Rig-Snapshot. None-Fallback auf
        # static band_hint damit Tests + Backward-Compat ohne Resolver
        # weiter funktionieren.
        band_for_decodes = self.band_hint
        if self.band_resolver is not None:
            try:
                resolved = self.band_resolver()
                if resolved:
                    band_for_decodes = resolved
            except Exception as exc:
                log.debug("band_resolver failed: %s, fallback band_hint=%s", exc, self.band_hint)

        out = [_to_decoded_msg(r, tick, band_for_decodes) for r in raw]
        if late_decoder is not None:
            self._schedule_late_pass(
                late_decoder, pcm_for_decode, tick, band_for_decodes, raw, slot_seconds,
            )
        # Stufe 3: jt9 (WSJT-X) parallel zu Stufe 2 — nur FT8, nur mit Sink.
        if (
            self.jt9_enabled
            and self.late_pass_sink is not None
            and (self.mode != "FT4" or self.jt9_ft4)
        ):
            from . import jt9 as _jt9
            if _jt9.available():
                self._schedule_jt9(pcm_for_decode, tick, band_for_decodes, raw, slot_seconds)
        return out

    # ------------------------------------------------------------ Stufe 2
    def _schedule_late_pass(
        self, late_decoder, pcm: bytes, tick: SlotTick, band: str,
        fast_raw: list[ShimDecode], slot_seconds: float,
    ) -> None:
        if self._late_task is not None and not self._late_task.done():
            # Die vorige Stufe 2 laeuft noch — dann ist der Modus fuer diese
            # CPU zu teuer. Diesen Slot ueberspringen (Stufe 1 ist ja durch)
            # und mitzaehlen; nach drei Ueberlaeufen eine Stufe runter.
            self.metrics.late_pass_skipped += 1
            self._late_overruns += 1
            if self._late_overruns >= 3:
                self._downgrade_mode("Stufe 2 laeuft laenger als ein Slot")
                self._late_overruns = 0
            return
        self._late_task = asyncio.get_running_loop().create_task(
            self._run_late_pass(late_decoder, pcm, tick, band, fast_raw, slot_seconds),
            name=f"decode-late-{tick.index}",
        )

    def _schedule_jt9(
        self, pcm: bytes, tick: SlotTick, band: str,
        fast_raw: list[ShimDecode], slot_seconds: float,
    ) -> None:
        if self._jt9_task is not None and not self._jt9_task.done():
            self.metrics.jt9_skipped += 1
            return
        self._jt9_task = asyncio.get_running_loop().create_task(
            self._run_jt9(pcm, tick, band, fast_raw, slot_seconds),
            name=f"decode-jt9-{tick.index}",
        )

    async def _run_jt9(
        self, pcm: bytes, tick: SlotTick, band: str,
        fast_raw: list[ShimDecode], slot_seconds: float,
    ) -> None:
        import time as _time

        from . import jt9 as _jt9
        loop = asyncio.get_running_loop()
        t0 = _time.monotonic()
        depth = int(self.jt9_depth)
        if self.jt9_depth_boost and t0 >= self._jt9_boost_blocked_until and self.mode != "FT4":
            depth = 3
        timeout_s = float(self.jt9_timeout_s) if depth < 3 else max(float(self.jt9_timeout_s), 2.0 * slot_seconds - 3.0)
        my_call = self.jt9_my_call if self.jt9_ap else None
        my_grid = self.jt9_my_grid if self.jt9_ap else None
        his_call = self.jt9_his_call if self.jt9_ap else None
        his_grid = self.jt9_his_grid if self.jt9_ap else None
        ap_flags = int(self.jt9_ap_flags) if self.jt9_ap else 0
        try:
            raw = await loop.run_in_executor(
                self._jt9_executor,
                lambda: _jt9.run_jt9(pcm, depth=depth, timeout_s=timeout_s, mode=self.mode,
                                     my_call=my_call, my_grid=my_grid, his_call=his_call,
                                     his_grid=his_grid, ap_flags=ap_flags),
            )
        except Exception as exc:
            self.metrics.jt9_failed += 1
            log.warning("jt9 pass failed for tick %s: %s", tick.index, exc)
            return
        duration_s = _time.monotonic() - t0
        seen = {r.message for r in fast_raw} | self._slot_seen.get(tick.index, set())
        new = [r for r in raw if r.message not in seen]
        self._slot_seen.setdefault(tick.index, set()).update(r.message for r in new)
        self.metrics.jt9_last_count = len(new)
        self.metrics.jt9_total += len(new)
        self.metrics.jt9_last_duration_s = duration_s
        self.metrics.jt9_last_depth = depth
        if depth >= 3 and duration_s > 2.0 * slot_seconds - 3.0:
            self._jt9_boost_blocked_until = _time.monotonic() + 600.0
            log.warning("jt9 Tiefe 3 brauchte %.1f s — 10 min zurueck auf Tiefe %d", duration_s, self.jt9_depth)
        elif depth < 3 and duration_s > slot_seconds:
            log.warning("jt9 brauchte %.1f s (Slot %.0f s) — Tiefe %d zu teuer?", duration_s, slot_seconds, depth)
        if not new or self.late_pass_sink is None:
            return
        msgs = [_to_decoded_msg(r, tick, band, late=True) for r in new]
        try:
            await self.late_pass_sink(msgs, tick)
        except Exception as exc:
            log.warning("jt9 sink failed for tick %s: %s", tick.index, exc)

    def _downgrade_mode(self, why: str) -> None:
        order = ["extreme", "multi", "deep", "standard"]
        try:
            nxt = order[order.index(self.decoder_mode) + 1]
        except (ValueError, IndexError):
            return
        log.warning("decoder auto-downgrade: %s → %s (%s)", self.decoder_mode, nxt, why)
        self.decoder_mode = nxt

    async def _run_late_pass(
        self, late_decoder, pcm: bytes, tick: SlotTick, band: str,
        fast_raw: list[ShimDecode], slot_seconds: float,
    ) -> None:
        import time as _time
        loop = asyncio.get_running_loop()
        t0 = _time.monotonic()

        def _late_call() -> list[ShimDecode]:
            # Stufe 2 darf teurer sein: eigener LDPC-Faktor, danach zurueck
            # auf den adaptiven Wert von Stufe 1. Laeuft im Late-Thread;
            # Stufe 1 des naechsten Slots kommt erst ~12 s spaeter.
            try:
                from .ft8_native import lib as _ft8_lib
                _ft8_lib.ft8_shim_set_ldpc_factor(int(self.late_ldpc_pct))
            except Exception:
                _ft8_lib = None
            try:
                return late_decoder(pcm)
            finally:
                if _ft8_lib is not None:
                    try:
                        _ft8_lib.ft8_shim_set_ldpc_factor(int(round(self.ldpc_factor * 100)))
                    except Exception:
                        pass

        try:
            raw = await loop.run_in_executor(self._late_executor, _late_call)
        except Exception as exc:
            log.warning("%s late pass failed for tick %s: %s", self.mode, tick.index, exc)
            return
        duration_s = _time.monotonic() - t0
        # Stufe 2 findet alles von Stufe 1 nochmal — nur das Neue zaehlt.
        seen = {r.message for r in fast_raw} | self._slot_seen.get(tick.index, set())
        new = [r for r in raw if r.message not in seen]
        self._slot_seen.setdefault(tick.index, set()).update(r.message for r in new)
        self.metrics.note_late_pass(len(new), duration_s)
        if duration_s > slot_seconds:
            self._late_overruns += 1
            if self._late_overruns >= 3:
                self._downgrade_mode(f"Stufe 2 brauchte {duration_s:.1f} s")
                self._late_overruns = 0
        else:
            self._late_overruns = 0
        if not new or self.late_pass_sink is None:
            return
        msgs = [_to_decoded_msg(r, tick, band, late=True) for r in new]
        try:
            await self.late_pass_sink(msgs, tick)
        except Exception as exc:
            log.warning("late_pass_sink failed for tick %s: %s", tick.index, exc)


def _to_decoded_msg(
    shim: ShimDecode, tick: SlotTick, band: str, *, late: bool = False,
) -> DecodedMsg:
    parsed = parse_message(shim.message)
    # The shim's freq_hz is the audio-band offset, not the on-air freq;
    # the orchestrator can add the rig dial later if needed.
    return DecodedMsg(
        ts=datetime.fromtimestamp(tick.posix, tz=UTC),
        call_from=parsed.call_from,
        call_to=parsed.call_to,
        grid=parsed.grid,
        message=shim.message,
        snr_db=shim.snr_db_est,
        dt_s=shim.dt_s,
        freq_offset_hz=int(round(shim.freq_hz)),
        band=band,
        is_freetext=parsed.is_freetext,
        late=late,
        cq_directed=parsed.cq_directed,
    )


__all__ = [
    "DecodePipeline",
    "DecodePipelineMetrics",
    "ParsedMessage",
    "parse_message",
]
