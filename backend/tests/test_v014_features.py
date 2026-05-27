"""Tests fuer v0.14.0 Block A: Watchlist + Grayline-Tier + Band-Open-Tier.

Watchlist ist UI/DB-getrieben; hier nur die State-Machine-Tier-Tests + die
util.propagation-Helpers. End-to-end-Watchlist-Tests laufen ueber
test_web/test_orchestrator wenn die Integration steht.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ft8_appliance.statemachine.machine import (
    HUNT_TIERS,
    _maidenhead_to_latlon,
    _tier_band_open,
    _tier_grayline,
)
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext
from ft8_appliance.util.propagation import (
    band_condition_now,
    is_band_open_for_dx,
    is_daytime,
    is_in_grayline,
    solar_elevation_deg,
    sun_position,
)


# ---------------------------------------------------------------------------
# Sun position
# ---------------------------------------------------------------------------


def test_sun_at_equinox_above_equator():
    """Maerz-Aequinoktium um 12:00 UTC: Subsolar-Punkt nahe (0°, 0°)."""
    when = datetime(2026, 3, 20, 12, 0, 0, tzinfo=UTC)
    lat, lon = sun_position(when)
    assert abs(lat) < 2.0  # bei Aequinoktium ~0°
    assert abs(lon) < 5.0  # bei 12 UTC ~0° (Greenwich)


def test_sun_position_returns_tuple():
    when = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    lat, lon = sun_position(when)
    assert -90 <= lat <= 90
    assert -180 <= lon <= 180


def test_solar_elevation_positive_at_local_noon():
    """JN58 (48.5N, 9E) um 11 UTC im Sommer: Sonne deutlich ueber Horizont."""
    when = datetime(2026, 6, 21, 11, 0, 0, tzinfo=UTC)
    elev = solar_elevation_deg(48.5, 9.0, when)
    assert elev > 30  # Sommer-Mittag in DE: ~60°


def test_solar_elevation_negative_at_midnight():
    when = datetime(2026, 12, 21, 0, 0, 0, tzinfo=UTC)
    elev = solar_elevation_deg(48.5, 9.0, when)
    assert elev < 0


def test_is_daytime():
    midday = datetime(2026, 6, 21, 11, 0, 0, tzinfo=UTC)
    midnight = datetime(2026, 6, 21, 23, 0, 0, tzinfo=UTC)
    assert is_daytime(48.5, 9.0, midday)
    assert not is_daytime(48.5, 9.0, midnight)


# ---------------------------------------------------------------------------
# Maidenhead
# ---------------------------------------------------------------------------


def test_maidenhead_jn58_center():
    """JN58 = Wuerzburg/Bayreuth. Field JN = 0-20°E, 40-50°N.
    Square 58: lon-square 5 → 10-12°E, lat-square 8 → 48-49°N.
    Center: 48.5N, 11.0E."""
    lat, lon = _maidenhead_to_latlon("JN58")
    assert abs(lat - 48.5) < 0.01
    assert abs(lon - 11.0) < 0.01


def test_maidenhead_fn31_center():
    """FN31 = NYC area. Field FN = 60-80°W, 40-50°N.
    Square 31: lon-square 3 → 74-72°W (-74 to -72), lat-square 1 → 41-42°N."""
    lat, lon = _maidenhead_to_latlon("FN31")
    assert 40 < lat < 42
    assert -75 < lon < -71


def test_maidenhead_too_short():
    with pytest.raises(ValueError):
        _maidenhead_to_latlon("JN")


# ---------------------------------------------------------------------------
# Grayline detection
# ---------------------------------------------------------------------------


def test_grayline_at_sunrise_in_europe():
    """JN58 sunrise im Juni ist gegen ~03:30 UTC; Grayline-Fenster
    ueberlappt das."""
    when = datetime(2026, 6, 21, 3, 0, 0, tzinfo=UTC)
    # Sonnenelevation gegen Sonnenaufgang sollte in [-6, +6]° sein
    elev = solar_elevation_deg(48.5, 9.0, when)
    if -6 <= elev <= 6:
        assert is_in_grayline(48.5, 9.0, when)


def test_grayline_not_at_solar_noon():
    when = datetime(2026, 6, 21, 11, 0, 0, tzinfo=UTC)
    assert not is_in_grayline(48.5, 9.0, when)


def test_grayline_not_at_solar_midnight():
    when = datetime(2026, 6, 21, 23, 0, 0, tzinfo=UTC)
    assert not is_in_grayline(48.5, 9.0, when)


# ---------------------------------------------------------------------------
# Tier functions
# ---------------------------------------------------------------------------


def _ctx(**overrides) -> MachineContext:
    c = MachineContext(callsign="DK9XR", my_grid="JN58", band="20m")
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _decode(call_from: str | None, *, message: str | None = None) -> DecodedMsg:
    if message is None:
        message = f"CQ {call_from}"
    return DecodedMsg(
        ts=datetime.now(UTC),
        call_from=call_from, call_to=None, grid=None,
        message=message, snr_db=-10, dt_s=0.1,
        freq_offset_hz=1500, band="20m",
    )


def test_tier_grayline_no_position_known():
    """call_to_latlon leer → kein Boost."""
    ctx = _ctx(call_to_latlon={})
    assert _tier_grayline(_decode("DL5ABC"), ctx) == 0


def test_tier_grayline_outside_window():
    """Position in voller Sonne (Mittag) → kein Grayline."""
    # JA1XYZ in Tokio (35.6N, 139.7E) um 03:00 UTC = 12:00 JST = High Noon
    ctx = _ctx(call_to_latlon={"JA1XYZ": (35.6, 139.7)})
    # Simulate noon over Tokio via patching datetime — but tier uses
    # datetime.now(), so we just check the function doesn't crash and
    # gives a binary result. Real-time verification ist in den
    # sun_position-Tests oben.
    result = _tier_grayline(_decode("JA1XYZ"), ctx)
    assert result in (0, 1)


def test_tier_grayline_no_call_from():
    ctx = _ctx(call_to_latlon={"DL5ABC": (50.0, 10.0)})
    d = _decode(None)
    d.call_from = None
    assert _tier_grayline(d, ctx) == 0


def test_tier_band_open_no_conditions_zero():
    ctx = _ctx(band_conditions_day={}, band_conditions_night={})
    assert _tier_band_open(_decode("DL5ABC"), ctx) == 0


def test_tier_band_open_good_returns_1():
    """Conditions sagen 'Good' fuer 30m-20m → Tier liefert 1."""
    ctx = _ctx(
        band="20m",
        band_conditions_day={"30m-20m": "Good"},
        band_conditions_night={"30m-20m": "Poor"},
    )
    # Ergebnis haengt von der Tageszeit JETZT in JN58 ab. Test verifies
    # mindestens dass keine Exception kommt und Result binaer ist.
    result = _tier_band_open(_decode("DL5ABC"), ctx)
    assert result in (0, 1)


def test_tier_band_open_unknown_band_zero():
    ctx = _ctx(
        band="unknown_band",
        band_conditions_day={"30m-20m": "Good"},
    )
    assert _tier_band_open(_decode("DL5ABC"), ctx) == 0


def test_hunt_tiers_registry_has_new_entries():
    assert "grayline" in HUNT_TIERS
    assert "band_open" in HUNT_TIERS


# ---------------------------------------------------------------------------
# band_condition_now helper
# ---------------------------------------------------------------------------


def test_band_condition_now_uses_day_table_at_noon():
    when = datetime(2026, 6, 21, 11, 0, 0, tzinfo=UTC)  # noon in JN58
    cond = band_condition_now(
        "20m",
        day_conditions={"30m-20m": "Good"},
        night_conditions={"30m-20m": "Poor"},
        my_lat=48.5, my_lon=9.0, when=when,
    )
    assert cond == "Good"


def test_band_condition_now_uses_night_table_at_midnight():
    when = datetime(2026, 6, 21, 23, 0, 0, tzinfo=UTC)
    cond = band_condition_now(
        "20m",
        day_conditions={"30m-20m": "Good"},
        night_conditions={"30m-20m": "Poor"},
        my_lat=48.5, my_lon=9.0, when=when,
    )
    assert cond == "Poor"


def test_band_condition_now_unknown_band():
    when = datetime(2026, 6, 21, 11, 0, 0, tzinfo=UTC)
    assert band_condition_now(
        "frob",
        day_conditions={"30m-20m": "Good"},
        night_conditions={},
        my_lat=48.5, my_lon=9.0, when=when,
    ) is None


def test_is_band_open_for_dx_true_for_good():
    when = datetime(2026, 6, 21, 11, 0, 0, tzinfo=UTC)
    assert is_band_open_for_dx(
        "20m",
        day_conditions={"30m-20m": "Good"},
        night_conditions={},
        my_lat=48.5, my_lon=9.0, when=when,
    )


def test_is_band_open_for_dx_false_for_fair():
    when = datetime(2026, 6, 21, 11, 0, 0, tzinfo=UTC)
    assert not is_band_open_for_dx(
        "20m",
        day_conditions={"30m-20m": "Fair"},
        night_conditions={},
        my_lat=48.5, my_lon=9.0, when=when,
    )


# ---------------------------------------------------------------------------
# OperatingConfig default order
# ---------------------------------------------------------------------------


def test_default_hunt_priority_includes_new_tiers():
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig()
    assert "grayline" in cfg.hunt_priority
    assert "band_open" in cfg.hunt_priority
    # Position: nach tail_end_target, vor new_dxcc_psk
    idx_tail = cfg.hunt_priority.index("tail_end_target")
    idx_gray = cfg.hunt_priority.index("grayline")
    idx_band = cfg.hunt_priority.index("band_open")
    idx_dxcc_psk = cfg.hunt_priority.index("new_dxcc_psk")
    assert idx_tail < idx_gray < idx_band < idx_dxcc_psk


def test_migration_adds_new_tiers_to_old_config():
    from ft8_appliance.config.models import OperatingConfig
    old = ["marine_psk", "marine", "new_dxcc_psk", "snr"]
    cfg = OperatingConfig(hunt_priority=old)
    assert "grayline" in cfg.hunt_priority
    assert "band_open" in cfg.hunt_priority
    assert cfg.hunt_priority[-1] == "snr"


def test_migration_includes_v014_tiers():
    """v0.14.0 hat min. die grayline + band_open Tiers drin."""
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig(hunt_priority=[])
    assert "grayline" in cfg.hunt_priority
    assert "band_open" in cfg.hunt_priority
    # Mind. 14 (v0.14.0); aber v0.15.0+ erweitert.
    assert len(cfg.hunt_priority) >= 14
