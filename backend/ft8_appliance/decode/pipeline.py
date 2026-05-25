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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..audio.slot_sync import SlotBuffer, SlotExtraction
from ..runtime.slot_clock import SlotTick
from ..statemachine import DecodedMsg
from .ft8_native import (
    SAMPLES_PER_SLOT_FT4,
    ShimDecode,
    decode_slot,
    decode_slot_ft4,
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
        if (
            len(rest) >= 2
            and rest[0].isalpha()
            and rest[0] != "CQ"
        ):
            rest = rest[1:]
        if not rest:
            return ParsedMessage(None, None, None, None, True)
        call_from = rest[0]
        grid = rest[1] if len(rest) >= 2 and _GRID_RE.fullmatch(rest[1]) else None
        return ParsedMessage(
            call_from=call_from, call_to=None, grid=grid, report=None, is_cq=True
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
    notch_detector: "NotchDetector | None" = None  # noqa: F821 — fwd ref
    # v0.6.0 Phase B/C: Decoder-Tuning. Standard = altes Verhalten (osr=2,
    # LDPC=25). Deep = osr=4/LDPC=50. Multi = Pass1+Pass2-Merge. FT4
    # nutzt immer Standard-Decoder (FT4-Decoder hat keine Deep-Variante).
    decoder_mode: str = "standard"  # "standard" | "deep" | "multi"
    _consecutive_late_slots: int = 0  # CPU-adaptive Fallback-Trigger
    # USB-Audio kommt in 1024-sample-periods (~85 ms). Wenn der Slot
    # genau zum Wallclock-Boundary endet, ist die letzte Period oft noch
    # nicht in den SlotBuffer geflossen → "short by X samples"-Logs +
    # Verlust der letzten Symbole. 150 ms Warten überbrückt eine volle
    # ALSA-Period plus Scheduling-Slack ohne den nächsten Slot zu blocken.
    extract_delay_s: float = 0.15

    async def __call__(self, tick: SlotTick) -> list[DecodedMsg]:
        from ..audio.slot_sync import FT4_SLOT_SECONDS, SLOT_SECONDS

        # Mode-aware Slot-Window + Decoder-Funktion (Audit F6 v0.4.0).
        # v0.6.0 Phase C: FT8-Pfad waehlt Decoder-Variante via decoder_mode.
        # FT4 hat keine Deep-Variante (FT4-Decoder ist eigene C-Funktion).
        if self.mode == "FT4":
            slot_seconds = FT4_SLOT_SECONDS
            decoder = decode_slot_ft4
        else:
            slot_seconds = SLOT_SECONDS
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
        extraction = self.slot_buffer.extract_slot(
            slot_start_posix, slot_seconds=slot_seconds
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
            if self._consecutive_late_slots >= 3 and self.decoder_mode in ("deep", "multi"):
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
        return out


def _to_decoded_msg(shim: ShimDecode, tick: SlotTick, band: str) -> DecodedMsg:
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
    )


__all__ = [
    "DecodePipeline",
    "DecodePipelineMetrics",
    "ParsedMessage",
    "parse_message",
]
