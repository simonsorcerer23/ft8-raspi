"""Production wiring — build a fully-connected :class:`Orchestrator`.

This is the headless equivalent of what the test suite assembles by hand:
real :class:`RigctldClient`, :class:`GpsdClient`, decode source backed by
ALSA capture + ``ft8_lib``, real :class:`SlotClock`. It boots best-effort:
each hardware adapter that fails (rigctld down, no USB audio device) falls
back to a safe disconnected state so the controller still serves the web
UI and the first-boot wizard can fix the config.
"""

from __future__ import annotations

import logging
import subprocess

from ..config import AppConfig
from ..gps import GpsdClient
from ..rig import RigctldClient
from ..statemachine import DecodedMsg
from .orchestrator import Orchestrator
from .slot_clock import SlotClock, SlotTick

log = logging.getLogger(__name__)


async def _noop_decode_source(_: SlotTick) -> list[DecodedMsg]:
    """Empty :class:`DecodeSource` — yields zero decodes.

    Used when ALSA isn't available (dev workstation) or when no USB audio
    device is plugged in yet (rig hasn't been connected). The orchestrator
    still ticks per slot; only the decode side is silent.
    """
    return []


def _resolve_capture_device(hint: str) -> str | None:
    """Pick the ALSA device string for the rig's USB sound card.

    Strategy:
    1. Read ``arecord -L`` (Hamlib-style names: ``plughw:CARD=CODEC,DEV=0``)
    2. If *hint* is non-empty, match it case-insensitively against card names
       (so ``CODEC`` matches ``plughw:CARD=CODEC,DEV=0``).
    3. Empty hint = pick the first plughw:CARD=* device that isn't vc4hdmi.
    Returns ``None`` when nothing usable is found.
    """
    try:
        out = subprocess.run(
            ["arecord", "-L"], capture_output=True, text=True, timeout=2, check=True
        ).stdout
    except Exception as exc:
        log.warning("arecord -L failed: %s — cannot resolve capture device", exc)
        return None

    candidates: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("plughw:CARD="):
            candidates.append(line)

    needle = hint.strip().lower()
    if needle:
        for c in candidates:
            if needle in c.lower():
                return c
        log.warning("audio_card_hint %r not found among %s", hint, candidates)

    # Fall back to first non-HDMI device
    for c in candidates:
        if "hdmi" not in c.lower() and "vc4" not in c.lower():
            return c
    return None


def _build_decode_source(config: AppConfig):
    """Wire ALSA capture → SlotBuffer → DecodePipeline.

    Falls back to a noop source if pyalsaaudio isn't importable or no input
    device is available. Capture runs in a daemon thread, so the orchestrator
    can hold a reference to it via the closure and let GC tear it down on
    process exit — adequate for the appliance's single-instance lifecycle.
    """
    try:
        from ..audio.alsa_io import AlsaCapture, alsa_available
        from ..audio.slot_sync import SlotBuffer
        from ..decode.pipeline import DecodePipeline
    except Exception as exc:
        log.warning("ALSA stack import failed (%s) — decode source disabled", exc)
        return _noop_decode_source

    if not alsa_available():
        log.warning("pyalsaaudio not available — decode source disabled (noop)")
        return _noop_decode_source

    device = _resolve_capture_device(config.rig.audio_card_hint)
    if device is None:
        log.warning("no usable ALSA capture device — decode source disabled (noop)")
        return _noop_decode_source

    # Determine band hint from the first configured band (single-band
    # appliance for now; band-switching is a later sweep).
    band_hint = config.bands[0].name if config.bands else "20m"

    slot_buffer = SlotBuffer()
    pipeline = DecodePipeline(slot_buffer=slot_buffer, band_hint=band_hint)
    capture = AlsaCapture(sink=slot_buffer.feed, device=device)
    capture.start()
    log.info("ALSA capture started: device=%s band_hint=%s", device, band_hint)

    # Keep capture alive by attaching to the pipeline (so it isn't GC'd).
    pipeline._capture = capture  # type: ignore[attr-defined]
    return pipeline


def _build_playback(config: AppConfig):
    """Try to instantiate :class:`AlsaPlayback` on the same USB sound card
    as the capture side. Returns ``None`` when ALSA isn't available so the
    orchestrator falls back to log-only TX (used on the dev workstation)."""
    try:
        from ..audio.alsa_io import AlsaPlayback, alsa_available
    except Exception as exc:
        log.warning("AlsaPlayback import failed (%s) — TX disabled", exc)
        return None
    if not alsa_available():
        return None
    device = _resolve_capture_device(config.rig.audio_card_hint)
    if device is None:
        log.warning("no usable ALSA playback device — TX disabled")
        return None
    log.info("ALSA playback adapter ready: device=%s", device)
    return AlsaPlayback(device=device)


async def build_production_orchestrator(config: AppConfig) -> Orchestrator:
    """Assemble the production :class:`Orchestrator` from the loaded config.

    Caller still has to ``await orch.start()``. Returning the un-started
    instance lets the FastAPI lifespan attach it to ``app.state`` and start
    it as part of the shared startup phase.
    """
    rig = RigctldClient(host="127.0.0.1", port=4532)
    gps = GpsdClient(host="127.0.0.1", port=2947)
    decode_source = _build_decode_source(config)
    playback = _build_playback(config)
    slot_clock = SlotClock()  # real-time 15-s clock; FT4 swap happens in start()

    return Orchestrator(
        config=config,
        rig=rig,
        gps=gps,
        decode_source=decode_source,
        playback=playback,
        slot_clock=slot_clock,
    )
