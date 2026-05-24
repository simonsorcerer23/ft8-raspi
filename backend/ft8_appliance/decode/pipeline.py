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
from .ft8_native import ShimDecode, decode_slot

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message parsers — FT8 messages have a stylised grammar, no AI needed.

# Grid: 2 letters A-R + 2 digits, optionally 2 subsquare letters.
# RR73 happens to match the 4-char form so we filter closing tokens explicitly.
_GRID_RE = re.compile(r"[A-R]{2}[0-9]{2}([a-x]{2})?")
_SIGNAL_REPORT_RE = re.compile(r"R?[+-]\d{1,2}")
_CLOSING_TOKENS = {"RR73", "RRR", "73"}


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    call_from: str | None
    call_to: str | None
    grid: str | None
    report: str | None  # e.g. "-10" or "R-10"
    is_cq: bool


def parse_message(text: str) -> ParsedMessage:
    """Best-effort parser for the common FT8 message shapes.

    Recognises:
      * ``CQ <call> <grid>``      → call_from + grid, is_cq=True
      * ``CQ DX <call> <grid>``   → same
      * ``CQ EU <call> <grid>``   → same (continent CQ)
      * ``<to> <from> <grid>``    → call_to + call_from + grid
      * ``<to> <from> <report>``  → call_to + call_from + report
      * ``<to> <from> RR73 / 73`` → call_to + call_from
    """
    tokens = text.strip().split()
    if not tokens:
        return ParsedMessage(None, None, None, None, False)

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

    return ParsedMessage(None, None, None, None, False)


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

    def record_slot(self, count: int) -> None:
        """Updater used by the pipeline after each decode pass."""
        self.slots_decoded += 1
        self.decodes_total += count
        self.last_decode_count = count
        self.recent_counts.append(count)
        if len(self.recent_counts) > self.RECENT_WINDOW:
            del self.recent_counts[: len(self.recent_counts) - self.RECENT_WINDOW]

    @property
    def decodes_per_min(self) -> int:
        return sum(self.recent_counts)


@dataclass
class DecodePipeline:
    """A :class:`DecodeSource` backed by ``ft8_lib``."""

    slot_buffer: SlotBuffer
    band_hint: str = "20m"  # which band we're listening on (for DB rows)
    metrics: DecodePipelineMetrics = field(default_factory=DecodePipelineMetrics)
    # USB-Audio kommt in 1024-sample-periods (~85 ms). Wenn der Slot
    # genau zum Wallclock-Boundary endet, ist die letzte Period oft noch
    # nicht in den SlotBuffer geflossen → "short by X samples"-Logs +
    # Verlust der letzten Symbole. 150 ms Warten überbrückt eine volle
    # ALSA-Period plus Scheduling-Slack ohne den nächsten Slot zu blocken.
    extract_delay_s: float = 0.15

    async def __call__(self, tick: SlotTick) -> list[DecodedMsg]:
        # The slot's nominal start is `tick.posix - SLOT_SECONDS` (the slot
        # has just *ended*, we decode the audio captured during it).
        from ..audio.slot_sync import SLOT_SECONDS

        # Warte kurz damit die letzten Audio-Frames die Capture-Pipeline
        # erreichen — sonst zero-padden wir das Slot-Ende und der
        # Decoder sieht Stummheit wo eigentlich noch FT8-Symbole sind.
        if self.extract_delay_s > 0:
            await asyncio.sleep(self.extract_delay_s)
        slot_start_posix = tick.posix - SLOT_SECONDS
        extraction = self.slot_buffer.extract_slot(slot_start_posix)
        self.metrics.last_drift_samples = extraction.drift_samples

        # decode_slot is a synchronous C call (~200-800 ms on a Pi). Running
        # it directly in the asyncio event loop would freeze the rig poll,
        # gpsd consumer, and SSE streams for the whole duration. Push it to
        # the default ThreadPoolExecutor so the loop stays responsive.
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(None, decode_slot, extraction.pcm_s16le)
        except Exception as exc:
            log.warning("decode_slot failed for tick %s: %s", tick.index, exc)
            return []

        self.metrics.record_slot(len(raw))

        out = [_to_decoded_msg(r, tick, self.band_hint) for r in raw]
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
    )


__all__ = [
    "DecodePipeline",
    "DecodePipelineMetrics",
    "ParsedMessage",
    "parse_message",
]
