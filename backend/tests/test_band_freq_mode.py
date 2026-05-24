"""BandConfig.freq_for_mode() Tests — Sebastian Audit v0.4.2."""
from __future__ import annotations

from ft8_appliance.config.models import FT4_DEFAULT_DIALS, BandConfig


def test_ft8_mode_returns_freq_khz() -> None:
    b = BandConfig(name="20m", freq_khz=14074)
    assert b.freq_for_mode("FT8") == 14074


def test_ft4_mode_explicit_override() -> None:
    b = BandConfig(name="20m", freq_khz=14074, freq_khz_ft4=14080)
    assert b.freq_for_mode("FT4") == 14080


def test_ft4_mode_default_fallback_for_known_band() -> None:
    """Wenn kein freq_khz_ft4 gesetzt → fallback auf FT4_DEFAULT_DIALS."""
    b = BandConfig(name="15m", freq_khz=21074)
    assert b.freq_for_mode("FT4") == 21140  # IARU-Standard


def test_ft4_mode_unknown_band_falls_back_to_ft8() -> None:
    """Wenn Bandname nicht in FT4_DEFAULT_DIALS → FT8-Freq als
    absolute Fallback (besser als gar nichts zu funken)."""
    b = BandConfig(name="custom_band", freq_khz=14074)
    assert b.freq_for_mode("FT4") == 14074


def test_ft4_defaults_table_completeness() -> None:
    """Alle gaengigen HF-Baender + 6m+2m sind in den FT4-Defaults."""
    must_have = ["80m", "40m", "30m", "20m", "17m", "15m", "12m", "10m"]
    for band in must_have:
        assert band in FT4_DEFAULT_DIALS, f"{band} muss in FT4_DEFAULT_DIALS sein"


def test_user_explicit_overrides_default() -> None:
    """Wenn User custom freq_khz_ft4 setzt, schlaegt das den Default."""
    # 15m default is 21140, user setzt 21145
    b = BandConfig(name="15m", freq_khz=21074, freq_khz_ft4=21145)
    assert b.freq_for_mode("FT4") == 21145
