"""Sun-position + Grayline + Band-Condition Helpers (v0.14.0).

Pure functions, kein State, kein I/O — werden vom Picker pro Slot
aufgerufen, also muessen sie billig sein. Keine externen Deps; alles
mit ``math`` aus stdlib.

Hintergrund:
- **Grayline-Propagation**: an der Terminator-Linie (Sonnenaufgang/-untergang)
  bricht die D-Schicht der Ionosphaere zusammen waehrend die F-Schicht
  noch aktiv ist → besonders gute Bedingungen fuer Lowband-DX. Wenn ein
  CQ-Rufer in seinem Grayline-Fenster ist, ist die Verbindung dorthin
  ueberproportional erfolgreich.
- **Solar-Conditions-Bucketing**: hamqsl liefert per-Band-Conditions
  separat fuer Tag und Nacht. Wir picken die richtige Spalte basierend
  auf der Sonne ueber UNSEREM QTH.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime


# ---------------------------------------------------------------------------
# Sun position
# ---------------------------------------------------------------------------


def _julian_day(when: datetime) -> float:
    """Julian Day Number incl. fractional UT."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    when_utc = when.astimezone(UTC)
    y = when_utc.year
    m = when_utc.month
    d = when_utc.day
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    jdn = (
        int(365.25 * (y + 4716))
        + int(30.6001 * (m + 1))
        + d + b - 1524.5
    )
    frac = (when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600) / 24
    return jdn + frac


def sun_position(when: datetime) -> tuple[float, float]:
    """Subsolar point (lat, lon) in degrees fuer ``when``.

    Vereinfachte NOAA-Formel — ausreichend fuer Grayline-Naehe (±0.5°
    reicht uns voll, Terminator-Breite ist ~2°). Reference:
    https://gml.noaa.gov/grad/solcalc/solareqns.PDF
    """
    jd = _julian_day(when)
    n = jd - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360  # mean longitude
    g = math.radians((357.528 + 0.9856003 * n) % 360)  # mean anomaly
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)  # obliquity
    # Subsolar lat = declination, Subsolar lon = -GHA = -(GMST - alpha)
    decl = math.degrees(math.asin(math.sin(eps) * math.sin(lam)))
    # Right ascension
    alpha = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360
    # GMST in hours, then degrees
    t_utc = (jd - 2451545.0) / 36525
    gmst_h = (
        6.697374558
        + 0.06570982441908 * (jd - 2451545.0)
        + 1.00273790935 * (when.astimezone(UTC).hour
                           + when.astimezone(UTC).minute / 60
                           + when.astimezone(UTC).second / 3600)
        + 0.000026 * t_utc * t_utc
    ) % 24
    gmst_deg = gmst_h * 15
    sub_lon = (alpha - gmst_deg + 540) % 360 - 180
    return decl, sub_lon


def solar_elevation_deg(lat: float, lon: float, when: datetime) -> float:
    """Sun elevation (degrees above horizon) am Ort (lat, lon) zur Zeit
    ``when``. Negative = unter dem Horizont (Nacht)."""
    sub_lat, sub_lon = sun_position(when)
    # Angular distance from subsolar point → 90° - elevation
    phi1 = math.radians(lat)
    phi2 = math.radians(sub_lat)
    dlon = math.radians(lon - sub_lon)
    cos_z = (
        math.sin(phi1) * math.sin(phi2)
        + math.cos(phi1) * math.cos(phi2) * math.cos(dlon)
    )
    cos_z = max(-1.0, min(1.0, cos_z))
    return 90 - math.degrees(math.acos(cos_z))


def is_in_grayline(
    lat: float, lon: float, when: datetime, half_width_deg: float = 6.0
) -> bool:
    """True wenn die Position ``(lat, lon)`` gerade im Grayline-Fenster ist.

    ``half_width_deg`` = Sonnenelevation ± um den Horizont, in der die
    Grayline-Enhancement greift. 6° (Civil Twilight) ist Standard-
    Definition, deckt das praktische Grayline-Fenster ab.
    """
    elev = solar_elevation_deg(lat, lon, when)
    return -half_width_deg <= elev <= half_width_deg


def is_daytime(lat: float, lon: float, when: datetime) -> bool:
    """True wenn die Sonne ueber dem Horizont steht (Day vs Night)."""
    return solar_elevation_deg(lat, lon, when) > 0


# ---------------------------------------------------------------------------
# Band conditions
# ---------------------------------------------------------------------------


# Band-Name → hamqsl-Bucket-Key. hamqsl liefert je 2 Buckets pro band-range,
# wir mappen jedes konkrete Band auf seinen Bucket.
_BAND_TO_HAMQSL_BUCKET: dict[str, str] = {
    "160m": "80m-40m",
    "80m":  "80m-40m",
    "60m":  "80m-40m",
    "40m":  "80m-40m",
    "30m":  "30m-20m",
    "20m":  "30m-20m",
    "17m":  "17m-15m",
    "15m":  "17m-15m",
    "12m":  "12m-10m",
    "10m":  "12m-10m",
    "6m":   "12m-10m",  # nicht in hamqsl-Daten, Fallback
    "2m":   "12m-10m",  # dito
}


def band_condition_now(
    band: str,
    day_conditions: dict[str, str],
    night_conditions: dict[str, str],
    *,
    my_lat: float,
    my_lon: float,
    when: datetime,
) -> str | None:
    """Liefert die hamqsl-Condition fuer ``band`` JETZT (Day/Night-Auswahl
    via Sonne ueber unserem QTH). Werte: "Good"/"Fair"/"Poor"/None.

    None wenn Band-Bucket unbekannt oder hamqsl-Daten nicht verfuegbar.
    """
    bucket = _BAND_TO_HAMQSL_BUCKET.get(band)
    if bucket is None:
        return None
    table = day_conditions if is_daytime(my_lat, my_lon, when) else night_conditions
    return table.get(bucket)


def is_band_open_for_dx(
    band: str,
    day_conditions: dict[str, str],
    night_conditions: dict[str, str],
    *,
    my_lat: float,
    my_lon: float,
    when: datetime,
) -> bool:
    """True wenn die hamqsl-Condition fuer das Band jetzt ``Good`` ist.

    Wenn Conditions unbekannt → False (kein Boost, aber auch keine
    Strafe — der Picker faellt einfach auf andere Tiers zurueck).
    """
    cond = band_condition_now(
        band, day_conditions, night_conditions,
        my_lat=my_lat, my_lon=my_lon, when=when,
    )
    return cond == "Good"
