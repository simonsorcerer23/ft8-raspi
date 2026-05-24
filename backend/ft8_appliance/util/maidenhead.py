"""Maidenhead locator <-> lat/lon helpers + Great-Circle Geometrie.

Used for:
* Auto-QTH from GPS (lat/lon -> 6-char locator), §6.4
* Map markers from QSO log grids
* Coverage-Envelope-Polygon: pro Azimut-Bin maximale Reception-Distance
"""

from __future__ import annotations

import math


def latlon_to_locator(lat: float, lon: float, *, precision: int = 6) -> str:
    """Convert decimal lat/lon to a Maidenhead locator.

    ``precision`` must be 4 (large square only) or 6 (with sub-square).
    """
    if precision not in (4, 6):
        raise ValueError("precision must be 4 or 6")
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError(f"lat/lon out of range: {lat}, {lon}")

    lon180 = lon + 180.0
    lat90 = lat + 90.0

    # Field (A..R)
    f_lon = int(lon180 / 20)
    f_lat = int(lat90 / 10)
    field = chr(ord("A") + f_lon) + chr(ord("A") + f_lat)

    # Square (0..9)
    rem_lon = lon180 - f_lon * 20
    rem_lat = lat90 - f_lat * 10
    s_lon = int(rem_lon / 2)
    s_lat = int(rem_lat)
    square = str(s_lon) + str(s_lat)

    if precision == 4:
        return field + square

    # Sub-square (a..x)
    rem_lon -= s_lon * 2
    rem_lat -= s_lat
    sub_lon = int(rem_lon * 12)  # 1/12° per sub-square in longitude
    sub_lat = int(rem_lat * 24)  # 1/24° per sub-square in latitude
    sub = chr(ord("a") + sub_lon) + chr(ord("a") + sub_lat)
    return field + square + sub

def locator_to_latlon(grid: str) -> tuple[float, float]:
    """Inverse of :func:`latlon_to_locator`. Returns center of the square.

    Akzeptiert 4-stellige (Feld+Square, "JN58") oder 6-stellige (mit
    Sub-Square, "JN58td") Locator. Längere Eingaben werden auf 6
    gestutzt; kürzere als 4 ergeben ValueError.

    Resultat ist die Mitte des aufgelösten Squares — bei 4-stellig
    ist das +/- 1° Lat × 2° Lon Toleranz, bei 6-stellig +/- 1.25' × 2.5'.
    """
    g = grid.strip().upper()
    if len(g) < 4:
        raise ValueError(f"locator too short: {grid!r}")
    if len(g) >= 6:
        g6 = g[:4] + g[4:6].lower()
    else:
        g6 = g[:4]

    f_lon = ord(g6[0]) - ord("A")
    f_lat = ord(g6[1]) - ord("A")
    s_lon = int(g6[2])
    s_lat = int(g6[3])

    lon = -180.0 + f_lon * 20.0 + s_lon * 2.0
    lat = -90.0 + f_lat * 10.0 + s_lat * 1.0

    if len(g6) == 6:
        sub_lon = ord(g6[4]) - ord("a")
        sub_lat = ord(g6[5]) - ord("a")
        lon += sub_lon * (2.0 / 24.0)  # 5' per sub-square
        lat += sub_lat * (1.0 / 24.0)  # 2.5'
        # Center of the sub-square
        lon += (2.0 / 24.0) / 2.0
        lat += (1.0 / 24.0) / 2.0
    else:
        # Center of the 4-char square
        lon += 1.0
        lat += 0.5

    return (lat, lon)


# Haversine + Vincenti-Bearing — ausreichend genau für Funk-Distanzen,
# kein numpy/geographiclib nötig (Pi-CPU-Budget).
_EARTH_R_KM = 6371.0


def great_circle(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> tuple[float, float]:
    """Großkreis-Distanz [km] + Initial-Bearing [deg, 0..360] von P1 nach P2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)

    # Haversine
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    d_km = 2 * _EARTH_R_KM * math.asin(math.sqrt(a))

    # Forward Azimuth
    y = math.sin(dlam) * math.cos(phi2)
    x = (
        math.cos(phi1) * math.sin(phi2)
        - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    )
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    return (d_km, bearing)


def destination_point(
    lat: float, lon: float, bearing_deg: float, distance_km: float
) -> tuple[float, float]:
    """Großkreis-Endpunkt: gegeben Start + Bearing + Distanz → Zielpunkt.

    Used für die Coverage-Envelope-Polygon-Knoten — pro Azimut-Bin
    legen wir einen Punkt in *bearing_deg* Richtung im *distance_km*
    Abstand vom Station-Locator ab.
    """
    phi1 = math.radians(lat)
    lam1 = math.radians(lon)
    theta = math.radians(bearing_deg)
    delta = distance_km / _EARTH_R_KM  # angular distance in radians

    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    phi2 = math.asin(sin_phi2)

    y = math.sin(theta) * math.sin(delta) * math.cos(phi1)
    x = math.cos(delta) - math.sin(phi1) * sin_phi2
    lam2 = lam1 + math.atan2(y, x)

    # Normalize longitude to [-180, 180]
    lon2 = ((math.degrees(lam2) + 540.0) % 360.0) - 180.0
    return (math.degrees(phi2), lon2)
