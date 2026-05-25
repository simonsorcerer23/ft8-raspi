"""v0.7.0 Build 3: Auto-Notch fuer lokale QRM-Traeger.

Stationäre Stör-Frequenzen (Schaltnetzteile, PLC, Lampen-Träger usw.)
hinterlassen schmale Linien im Audio-Spektrum die einzelne FT8-Bins
zerstören und schwache Decodes verhindern. WSJT-X hat keine sowas eingebaut.

Hier zwei Stufen:

1) :class:`NotchDetector` — analysiert rolling 30s Audio-Buffer, findet
   persistente Peaks die > 15 dB über dem Median liegen UND in mehreren
   aufeinander folgenden Analysen wieder auftauchen.

2) :func:`apply_notches` — FFT-basierter Spektral-Notch pro 15s-Slot
   (numpy-only, kein scipy). Bei FT8 läuft das per-slot statt im Echtzeit-
   Audio-Pfad — gibt uns volle Block-Latency aber Decode wird sauberer.

Mathematisch: FFT(slot), zero-out bins in ±notch_width/2 um jede QRM-
Frequenz, IFFT zurück. Sub-1ms CPU pro Notch pro Slot auf Pi 5.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

DEFAULT_NOTCH_WIDTH_HZ = 8.0     # tight enough to not eat FT8-Töne (Spacing 6.25 Hz)
DEFAULT_MIN_PEAK_DB = 15.0       # peak gegen lokalen Median floor
DEFAULT_MAX_NOTCHES = 4          # CPU + artefact limit


@dataclass
class NotchDetector:
    """Rolling spectrum analysis → QRM-Peak-Liste."""

    fs: int = 12000
    analysis_window_s: float = 4.0   # FFT-Größe in s
    update_interval_s: float = 30.0  # wie oft neu analysieren
    persistence_required: int = 2    # in N aufeinander folgenden Analysen Peak sehen
    min_peak_db: float = DEFAULT_MIN_PEAK_DB
    max_notches: int = DEFAULT_MAX_NOTCHES

    # Internal state
    _last_analysis_at: float = field(default=0.0, init=False)
    _recent_peak_sets: list[set[int]] = field(default_factory=list, init=False)
    _active_notches: list[float] = field(default_factory=list, init=False)
    _audio_buffer: list[bytes] = field(default_factory=list, init=False)
    _MAX_BUFFER_BYTES: int = field(default=12000 * 2 * 30, init=False)  # 30 s @ 12kHz s16

    def feed(self, pcm_chunk_s16le: bytes) -> None:
        """Audio-Chunk anhängen; alte Daten abschneiden."""
        self._audio_buffer.append(pcm_chunk_s16le)
        total = sum(len(c) for c in self._audio_buffer)
        # Trim from front
        while total > self._MAX_BUFFER_BYTES and len(self._audio_buffer) > 1:
            removed = self._audio_buffer.pop(0)
            total -= len(removed)

    def maybe_update(self) -> bool:
        """Wenn Update-Intervall um, neu analysieren. Return True wenn geändert."""
        now = time.monotonic()
        if now - self._last_analysis_at < self.update_interval_s:
            return False
        self._last_analysis_at = now

        # Concat buffer to np.int16
        if not self._audio_buffer:
            return False
        joined = b"".join(self._audio_buffer)
        pcm = np.frombuffer(joined, dtype=np.int16)
        if len(pcm) < int(self.fs * self.analysis_window_s):
            return False

        # FFT auf den letzten analysis_window_s
        n_samples = int(self.fs * self.analysis_window_s)
        window = pcm[-n_samples:].astype(np.float32)
        # Hann window damit Leakage gering ist
        win = np.hanning(len(window))
        spectrum = np.fft.rfft(window * win)
        magnitudes_db = 20.0 * np.log10(np.abs(spectrum) + 1e-9)
        freqs = np.fft.rfftfreq(len(window), 1.0 / self.fs)

        # FT8-Passband (200-3000 Hz)
        passband_mask = (freqs >= 200) & (freqs <= 3000)
        if not np.any(passband_mask):
            return False
        passband_db = magnitudes_db[passband_mask]
        passband_freqs = freqs[passband_mask]

        # Median noise floor (robust gegen einzelne Peaks)
        floor_db = float(np.median(passband_db))

        # Peaks oberhalb floor + min_peak_db
        peak_thresh = floor_db + self.min_peak_db
        peak_mask = passband_db > peak_thresh
        # Bin-IDs (gerundet auf 10 Hz Grid damit Persistenz-Check stabil)
        peak_freqs_round = set(int(round(f / 10.0)) * 10 for f in passband_freqs[peak_mask])

        # Persistence-Tracking
        self._recent_peak_sets.append(peak_freqs_round)
        if len(self._recent_peak_sets) > self.persistence_required + 2:
            del self._recent_peak_sets[0]

        # Intersection der letzten N Analysen = persistent
        if len(self._recent_peak_sets) < self.persistence_required:
            return False
        persistent = self._recent_peak_sets[0].copy()
        for s in self._recent_peak_sets[1:self.persistence_required]:
            persistent &= s

        # Sortiere nach Magnitude (lautester Peak zuerst), trim auf max_notches
        # Wir können nicht direkt magnitudes-lookup machen weil die rounded
        # bin IDs nicht 1:1 sind — re-evaluieren per peak in passband_db
        if not persistent:
            new_notches = []
        else:
            scored = []
            for pf_round in persistent:
                # finde alle bins im 10-Hz-Rounded-Bucket
                bin_mask = np.abs(passband_freqs - pf_round) < 5.0
                if np.any(bin_mask):
                    peak_db = float(passband_db[bin_mask].max())
                    scored.append((peak_db, float(pf_round)))
            scored.sort(reverse=True)
            new_notches = [freq for _, freq in scored[: self.max_notches]]

        changed = sorted(new_notches) != sorted(self._active_notches)
        if changed:
            log.info(
                "auto-notch update: %d active notches: %s",
                len(new_notches), [f"{f:.0f}Hz" for f in new_notches],
            )
        self._active_notches = new_notches
        return changed

    @property
    def active_notches_hz(self) -> list[float]:
        return list(self._active_notches)


def apply_notches(
    pcm_s16le: bytes,
    notches_hz: list[float],
    fs: int = 12000,
    width_hz: float = DEFAULT_NOTCH_WIDTH_HZ,
) -> bytes:
    """Spektral-Notch: FFT → zero-out QRM-Bins → IFFT. Numpy-only."""
    if not notches_hz:
        return pcm_s16le
    pcm = np.frombuffer(pcm_s16le, dtype=np.int16).astype(np.float32)
    spectrum = np.fft.rfft(pcm)
    freqs = np.fft.rfftfreq(len(pcm), 1.0 / fs)
    for qf in notches_hz:
        mask = np.abs(freqs - qf) < (width_hz / 2.0)
        spectrum[mask] = 0.0
    out_float = np.fft.irfft(spectrum, n=len(pcm))
    # Clipping-Schutz (Notch kann theoretisch Amplitude reduzieren, nicht
    # erhoehen, aber float→int16 braucht sicheres Clipping)
    out_clipped = np.clip(out_float, -32768, 32767).astype(np.int16)
    return out_clipped.tobytes()
