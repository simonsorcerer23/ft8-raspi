"""ALSA capture + playback wrappers for the IC-705 USB audio device.

Only loadable on the Pi (or any system with ``libasound2``); the
``pyalsaaudio`` import lives behind a try/except so dev workstations
without ALSA headers can still import the rest of the audio package.

The capture side runs in a dedicated thread (ALSA reads are blocking)
and feeds :class:`SlotBuffer.feed` via a thread-safe handoff. The
playback side is also blocking — the orchestrator schedules it via
``loop.run_in_executor`` for TX.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

try:
    import alsaaudio  # pyalsaaudio
    _ALSA_AVAILABLE = True
except ImportError:
    alsaaudio = None  # type: ignore[assignment]
    _ALSA_AVAILABLE = False


SAMPLE_RATE_HZ = 12_000
CHANNELS = 1
PERIOD_FRAMES = 1024  # ~85 ms per period @ 12 kHz — comfortable for asyncio handoff


def alsa_available() -> bool:
    """Return True if the ALSA bindings are importable on this host."""
    return _ALSA_AVAILABLE


# ---------------------------------------------------------------------------
class AlsaCapture:
    """Blocking ALSA capture from the IC-705 USB sound card.

    Run :meth:`run` in a daemon thread; it pumps chunks into the
    *sink* callback with a wall-clock timestamp marking when the FIRST
    sample of the chunk hit the kernel buffer.

    On error or device disconnect, the thread restarts after a short
    cool-off — typical of USB hot-unplug scenarios in the field.
    """

    def __init__(
        self,
        sink: Callable[[bytes, float], None],
        *,
        device: str = "default",
        sample_rate: int = SAMPLE_RATE_HZ,
        period_frames: int = PERIOD_FRAMES,
    ) -> None:
        if not _ALSA_AVAILABLE:
            raise RuntimeError("pyalsaaudio is not installed on this system")
        self.sink = sink
        self.device = device
        self.sample_rate = sample_rate
        self.period_frames = period_frames
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="alsa-capture")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ------------------------------------------------------------------ inner
    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                pcm = alsaaudio.PCM(  # type: ignore[union-attr]
                    type=alsaaudio.PCM_CAPTURE,  # type: ignore[union-attr]
                    mode=alsaaudio.PCM_NORMAL,  # type: ignore[union-attr]
                    device=self.device,
                    channels=CHANNELS,
                    rate=self.sample_rate,
                    format=alsaaudio.PCM_FORMAT_S16_LE,  # type: ignore[union-attr]
                    periodsize=self.period_frames,
                )
                log.info("ALSA capture open device=%s rate=%d", self.device, self.sample_rate)
                backoff = 1.0
                while not self._stop.is_set():
                    length, data = pcm.read()
                    if length <= 0:
                        # underrun / overrun
                        continue
                    # POSIX timestamp of *now* approximates the end of the
                    # period; shift back by length/rate to estimate the
                    # first-sample timestamp.
                    now = time.time()
                    chunk_start = now - length / self.sample_rate
                    try:
                        self.sink(data, chunk_start)
                    except Exception:
                        log.exception("capture sink raised")
                pcm.close()
            except Exception as exc:
                log.warning("ALSA capture failed: %s — retrying in %.1fs", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 10.0)


# ---------------------------------------------------------------------------
class AlsaPlayback:
    """Blocking ALSA playback for TX.

    Use :meth:`play` from a thread-pool executor — it blocks until the
    last sample has been handed to the kernel. The PTT toggle is the
    caller's job (the orchestrator wraps this with rig.set_ptt true/false).
    """

    def __init__(
        self,
        *,
        device: str = "default",
        sample_rate: int = SAMPLE_RATE_HZ,
        period_frames: int = PERIOD_FRAMES,
    ) -> None:
        if not _ALSA_AVAILABLE:
            raise RuntimeError("pyalsaaudio is not installed on this system")
        self.device = device
        self.sample_rate = sample_rate
        self.period_frames = period_frames

    def play(self, pcm_s16le: bytes) -> None:
        out = alsaaudio.PCM(  # type: ignore[union-attr]
            type=alsaaudio.PCM_PLAYBACK,  # type: ignore[union-attr]
            mode=alsaaudio.PCM_NORMAL,  # type: ignore[union-attr]
            device=self.device,
            channels=CHANNELS,
            rate=self.sample_rate,
            format=alsaaudio.PCM_FORMAT_S16_LE,  # type: ignore[union-attr]
            periodsize=self.period_frames,
        )
        # Write in chunks of period_frames * 2 bytes
        chunk_size = self.period_frames * 2
        try:
            for i in range(0, len(pcm_s16le), chunk_size):
                out.write(pcm_s16le[i : i + chunk_size])
        finally:
            out.close()
