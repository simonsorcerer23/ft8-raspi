"""In-process mock for the ALSA capture / playback side.

Idea: the real :mod:`ft8_appliance.audio.capture` runs ALSA on the Pi and
hands 16-bit mono PCM frames to the decode loop. In tests we don't have
ALSA, so we expose the same interface backed by a pre-recorded WAV file
(or synthesised silence).

The mock provides both a *frame iterator* (for unit tests that just want
"some samples") and a *slot iterator* aligned to a virtual UTC clock,
which the state-machine tests rely on.
"""

from __future__ import annotations

import asyncio
import struct
import wave
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE_HZ = 12_000
SLOT_SECONDS = 15
SAMPLES_PER_SLOT = SAMPLE_RATE_HZ * SLOT_SECONDS  # 180 000


@dataclass(frozen=True)
class AudioSlot:
    """One FT8 slot worth of PCM samples plus its anchor timestamp."""

    slot_start_utc: float  # POSIX timestamp of the slot's nominal start
    pcm_s16le: bytes  # 16-bit little-endian mono, exactly SAMPLES_PER_SLOT samples


class MockAudio:
    """Replay a WAV file as a stream of FT8 slots.

    The WAV is read once into memory and then chunked into 15-second
    slices. If the WAV is shorter than one slot, it is zero-padded.
    """

    def __init__(self, wav_path: Path | None = None) -> None:
        self._wav_path = wav_path
        self._frames = self._load(wav_path) if wav_path else b"\x00\x00" * SAMPLES_PER_SLOT

    # ------------------------------------------------------------------ loader
    @staticmethod
    def _load(path: Path) -> bytes:
        with wave.open(str(path), "rb") as wf:
            if wf.getframerate() != SAMPLE_RATE_HZ:
                raise ValueError(
                    f"WAV sample rate {wf.getframerate()} != expected {SAMPLE_RATE_HZ}"
                )
            if wf.getnchannels() != 1:
                raise ValueError(f"WAV channel count {wf.getnchannels()} != 1")
            if wf.getsampwidth() != 2:
                raise ValueError(f"WAV sample width {wf.getsampwidth()} != 2 bytes")
            return wf.readframes(wf.getnframes())

    # ------------------------------------------------------------------ iteration
    def slots(self, start_ts: float = 0.0) -> Iterator[AudioSlot]:
        """Synchronous iterator over fixed-size slots."""
        total_samples = len(self._frames) // 2
        slot_idx = 0
        while slot_idx * SAMPLES_PER_SLOT < total_samples:
            begin = slot_idx * SAMPLES_PER_SLOT * 2
            end = begin + SAMPLES_PER_SLOT * 2
            chunk = self._frames[begin:end]
            if len(chunk) < SAMPLES_PER_SLOT * 2:
                # zero-pad the last slot
                chunk = chunk + b"\x00\x00" * ((SAMPLES_PER_SLOT * 2 - len(chunk)) // 2)
            yield AudioSlot(
                slot_start_utc=start_ts + slot_idx * SLOT_SECONDS,
                pcm_s16le=chunk,
            )
            slot_idx += 1

    async def slots_async(
        self, start_ts: float = 0.0, real_time: bool = False
    ) -> AsyncIterator[AudioSlot]:
        """Async iterator. If *real_time* is True, awaits 15 s between slots
        — useful for integration-style tests; default is fast-forward.
        """
        for slot in self.slots(start_ts):
            yield slot
            if real_time:
                await asyncio.sleep(SLOT_SECONDS)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def make_silent_slot(slot_start_utc: float = 0.0) -> AudioSlot:
        return AudioSlot(
            slot_start_utc=slot_start_utc,
            pcm_s16le=b"\x00\x00" * SAMPLES_PER_SLOT,
        )

    @staticmethod
    def make_tone_slot(
        slot_start_utc: float = 0.0,
        freq_hz: float = 1500.0,
        amplitude: float = 0.25,
    ) -> AudioSlot:
        """Pure sine tone — handy as a non-decodable but non-zero stimulus."""
        import math

        amp = int(amplitude * 32767)
        samples = bytearray()
        for n in range(SAMPLES_PER_SLOT):
            v = int(amp * math.sin(2 * math.pi * freq_hz * n / SAMPLE_RATE_HZ))
            samples += struct.pack("<h", v)
        return AudioSlot(slot_start_utc=slot_start_utc, pcm_s16le=bytes(samples))
