"""Phase-locked audio slot extractor.

Implements ``architecture.md`` §8.1: the system clock (GPS-disciplined
via chrony) is the master; ALSA's sample stream is the slave.

The public surface is :class:`SlotBuffer`:

* :meth:`feed` appends incoming PCM frames (S16LE mono 12 kHz) with the
  POSIX timestamp at which the *first* sample of the chunk was captured
* :meth:`extract_slot` returns *exactly* ``SAMPLES_PER_SLOT`` samples
  whose POSIX-timestamps span ``[slot_start, slot_start + 15)``, with
  the *actual* number of samples that fell inside the slot logged so
  the orchestrator can surface drift to the guards.

Drift accumulates *only inside one slot* — the next slot picks a fresh
anchor and we never let an old offset leak forward.

For the *ALSA-less* dev workflow there is also a sync-style helper that
takes a pre-aligned bytes blob and returns it unchanged; the
orchestrator on the dev machine uses that against WAV files. The Pi
gets the real :class:`SlotBuffer`.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass

SAMPLE_RATE_HZ = 12_000
SLOT_SECONDS = 15
SAMPLES_PER_SLOT = SAMPLE_RATE_HZ * SLOT_SECONDS  # 180 000
BYTES_PER_SAMPLE = 2  # int16

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SlotExtraction:
    """Result of one :meth:`SlotBuffer.extract_slot` call."""

    pcm_s16le: bytes              # exactly SAMPLES_PER_SLOT * 2 bytes
    requested_start_posix: float  # slot boundary we asked for
    anchor_posix: float           # POSIX timestamp of the first emitted sample
    actual_samples: int           # how many real samples we found (rest zero-padded)
    drift_samples: int            # actual − expected (can be negative)

    @property
    def drift_ms(self) -> float:
        return 1000.0 * self.drift_samples / SAMPLE_RATE_HZ


@dataclass(slots=True)
class _Chunk:
    posix_start: float
    pcm: bytes  # one or more samples, S16LE mono


class SlotBuffer:
    """Append-only ring of timestamped PCM chunks.

    Memory is bounded by :attr:`max_age_s` — anything older than
    ``now - max_age_s`` is discarded on every feed. The default
    (35 s) keeps two slot's worth of history, plenty for retro-
    extraction or recovery.
    """

    def __init__(self, max_age_s: float = 35.0) -> None:
        self._chunks: deque[_Chunk] = deque()
        self.max_age_s = max_age_s

    # ------------------------------------------------------------------ ingest
    def feed(self, chunk_pcm: bytes, posix_start: float) -> None:
        if len(chunk_pcm) % BYTES_PER_SAMPLE != 0:
            raise ValueError("chunk size must be a multiple of 2 (S16LE)")
        if not chunk_pcm:
            return
        self._chunks.append(_Chunk(posix_start=posix_start, pcm=chunk_pcm))
        self._prune(posix_start)

    def _prune(self, now_posix: float) -> None:
        cutoff = now_posix - self.max_age_s
        while self._chunks:
            head = self._chunks[0]
            # chunk end timestamp
            end = head.posix_start + (len(head.pcm) // BYTES_PER_SAMPLE) / SAMPLE_RATE_HZ
            if end < cutoff:
                self._chunks.popleft()
            else:
                break

    # ------------------------------------------------------------------ extract
    def extract_slot(self, slot_start_posix: float) -> SlotExtraction:
        """Cut a 15-second window starting at *slot_start_posix*.

        Samples whose timestamp falls inside ``[start, start + 15)`` are
        emitted in order. Missing samples (gaps, late start) are
        zero-padded at the *end* of the buffer.
        """
        slot_end_posix = slot_start_posix + SLOT_SECONDS

        out = bytearray()
        out_samples = 0
        anchor_posix = slot_start_posix
        anchor_found = False

        for chunk in self._chunks:
            chunk_samples = len(chunk.pcm) // BYTES_PER_SAMPLE
            chunk_end = chunk.posix_start + chunk_samples / SAMPLE_RATE_HZ

            # Skip chunks that ended before the slot starts
            if chunk_end <= slot_start_posix:
                continue
            # Stop once we're past the slot end
            if chunk.posix_start >= slot_end_posix:
                break

            # Compute slice within this chunk that overlaps the slot
            if chunk.posix_start < slot_start_posix:
                skip_samples = int(round((slot_start_posix - chunk.posix_start) * SAMPLE_RATE_HZ))
            else:
                skip_samples = 0

            # samples available from the start of the relevant slice
            avail = chunk_samples - skip_samples

            # samples we may still take to reach SAMPLES_PER_SLOT
            need = SAMPLES_PER_SLOT - out_samples
            take = min(avail, need)

            if take <= 0:
                continue

            begin_byte = skip_samples * BYTES_PER_SAMPLE
            end_byte = begin_byte + take * BYTES_PER_SAMPLE
            out += chunk.pcm[begin_byte:end_byte]
            out_samples += take

            if not anchor_found:
                anchor_posix = chunk.posix_start + skip_samples / SAMPLE_RATE_HZ
                anchor_found = True

            if out_samples >= SAMPLES_PER_SLOT:
                break

        # Zero-pad any missing tail (late start, gap, premature audio loss)
        missing = SAMPLES_PER_SLOT - out_samples
        if missing > 0:
            out += b"\x00\x00" * missing
            log.warning(
                "slot %s: short by %d samples (%.1f ms), zero-padded",
                slot_start_posix,
                missing,
                1000.0 * missing / SAMPLE_RATE_HZ,
            )

        drift = out_samples - SAMPLES_PER_SLOT
        return SlotExtraction(
            pcm_s16le=bytes(out),
            requested_start_posix=slot_start_posix,
            anchor_posix=anchor_posix,
            actual_samples=out_samples,
            drift_samples=drift,
        )

    # ------------------------------------------------------------------ introspection
    def __len__(self) -> int:
        return sum(len(c.pcm) // BYTES_PER_SAMPLE for c in self._chunks)

    def samples_buffered(self) -> int:
        return len(self)

    def rms_dbfs_recent(self, samples: int = 3000) -> float | None:
        """RMS-Pegel der letzten *samples* aufgenommenen Audio-Samples.

        Hintergrund: Hamlib's STRENGTH ist beim IC-7300 in PKTUSB nicht
        zuverlässig (liefert konstant 0/-54). Stattdessen leiten wir
        einen "RX-Pegel" direkt aus dem Audio-Stream ab — das ist eh
        was wir hören und es zappelt mit echter Signal-Aktivität.

        Default 3000 Samples = 250 ms bei 12 kHz. Lang genug für ein
        FT8-Symbol (162 ms je 79-Symbol-Frame bei 6.25 baud), kurz
        genug für lebendige Bewegung im UI.

        Returns:
            dBFS-Wert (negativ, 0 = Full-Scale int16) oder None wenn
            weniger als 100 Samples im Buffer (Service gerade gebootet).
        """
        import math
        import struct

        # Sammle die letzten *samples* aus dem Chunk-Deque (rückwärts).
        needed = samples
        parts: list[bytes] = []
        for chunk in reversed(self._chunks):
            if needed <= 0:
                break
            chunk_samples = len(chunk.pcm) // BYTES_PER_SAMPLE
            if chunk_samples <= needed:
                parts.append(chunk.pcm)
                needed -= chunk_samples
            else:
                take_bytes = needed * BYTES_PER_SAMPLE
                parts.append(chunk.pcm[-take_bytes:])
                needed = 0

        if not parts:
            return None

        audio = b"".join(reversed(parts))
        n = len(audio) // BYTES_PER_SAMPLE
        if n < 100:
            return None

        # Decode int16 samples, accumulate sum-of-squares.
        samples_arr = struct.unpack(f"<{n}h", audio)
        sumsq = sum(s * s for s in samples_arr)
        rms = math.sqrt(sumsq / n)

        if rms < 1.0:
            # Effektive Stille — vermeide log10(0).
            return -120.0
        return 20.0 * math.log10(rms / 32767.0)
