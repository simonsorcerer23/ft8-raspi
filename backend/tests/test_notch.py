"""v0.7.0 Build 3: Auto-Notch tests."""
from __future__ import annotations

import numpy as np

from ft8_appliance.audio.notch import NotchDetector, apply_notches


def _gen_pcm_with_qrm(qrm_freq_hz: float, duration_s: float, fs: int = 12000) -> bytes:
    n = int(fs * duration_s)
    t = np.arange(n) / fs
    sig = 0.05 * np.random.randn(n) + 0.5 * np.sin(2 * np.pi * qrm_freq_hz * t)
    pcm = (sig * 16384).astype(np.int16)
    return pcm.tobytes()


def test_apply_notches_eliminates_qrm():
    """Spectral notch entfernt Energy am Stör-Punkt, andere Frequenzen bleiben."""
    pcm = _gen_pcm_with_qrm(1234.0, 4.0)
    notched = apply_notches(pcm, [1234.0])

    orig = np.frombuffer(pcm, dtype=np.int16).astype(float)
    notched_arr = np.frombuffer(notched, dtype=np.int16).astype(float)

    o_spec = np.abs(np.fft.rfft(orig))
    n_spec = np.abs(np.fft.rfft(notched_arr))
    freqs = np.fft.rfftfreq(len(orig), 1.0 / 12000)

    qrm_idx = int(np.argmin(np.abs(freqs - 1234)))
    other_idx = int(np.argmin(np.abs(freqs - 1500)))

    assert n_spec[qrm_idx] < o_spec[qrm_idx] * 0.01, "QRM nicht hinreichend reduziert"
    # Andere Frequenzen unverändert
    assert abs(n_spec[other_idx] - o_spec[other_idx]) / max(o_spec[other_idx], 1) < 0.05


def test_apply_notches_noop_on_empty():
    """Leere Notch-Liste → unveränderte PCM."""
    pcm = _gen_pcm_with_qrm(1500.0, 2.0)
    assert apply_notches(pcm, []) == pcm


def test_detector_findet_persistent_peak():
    """Detector erkennt stationäre QRM nach Persistenz-Schwelle."""
    det = NotchDetector(update_interval_s=0.0, persistence_required=2)
    pcm = _gen_pcm_with_qrm(1234.0, 4.0)
    # 2 feeds + updates → erst nach 2ter Analyse persistent
    det.feed(pcm)
    det.maybe_update()
    det.feed(pcm)
    det.maybe_update()
    notches = det.active_notches_hz
    # Erwartet einen Notch nahe 1234 Hz (10-Hz-Grid-Rounding)
    assert any(abs(n - 1234) < 15 for n in notches), f"erwartete Notch ~1234Hz, got {notches}"


def test_detector_skips_transient_peak():
    """Ein einzelner Peak ohne Persistenz wird NICHT in Notch-Liste übernommen."""
    det = NotchDetector(update_interval_s=0.0, persistence_required=3)
    pcm_qrm = _gen_pcm_with_qrm(1234.0, 4.0)
    pcm_clean = _gen_pcm_with_qrm(99999.0, 4.0)  # Quasi-leerer Bin
    det.feed(pcm_qrm)
    det.maybe_update()
    det.feed(pcm_clean)
    det.maybe_update()
    det.feed(pcm_clean)
    det.maybe_update()
    notches = det.active_notches_hz
    assert not any(abs(n - 1234) < 15 for n in notches), \
        f"transient Peak sollte nicht persistieren, got {notches}"
