#!/usr/bin/env python3
"""Erzeugt backend/ft8_appliance/data/cept_borders.json aus Natural Earth.

Quelle: Natural Earth 1:50m Admin-0 Countries (Public Domain),
  https://github.com/nvkelso/natural-earth-vector
    /raw/master/geojson/ne_50m_admin_0_countries.geojson

Pro Land werden die aeusseren Ringe (ohne Loecher) extrahiert, kleine
Inseln verworfen und der Rest per Ramer-Douglas-Peucker vereinfacht
(~250 KB Gesamtgroesse). Das Ergebnis dient `cept.detect_from_gps` als
Point-in-Polygon-Datenbasis zur Disambiguierung von bbox-Overlaps.

Aufruf (einmalig / bei Datensatz-Update):
    curl -sL <ne-url> -o /tmp/ne.geojson
    python3 scripts/build_cept_borders.py /tmp/ne.geojson

Die JSON-Datei ist im Repo eingecheckt — dieses Skript muss NICHT zur
Laufzeit oder im Build laufen. Es dokumentiert nur die Herkunft.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# Unsere CEPT-Country-Codes → ISO-3166-A2 (Natural Earth ISO_A2/_EH).
# Achtung: unsere Codes kollidieren teils mit ISO (ES=Estland, EA=Spanien;
# UR=Ukraine, UA=Russland) — deshalb explizites Mapping, nicht raten.
# USA (W) + Hawaii (KH6) bewusst ausgelassen: ihre bbox ueberlappt keine
# andere → bbox allein genuegt, kein Polygon noetig.
CODE2ISO = {
    "DL": "DE", "9A": "HR", "OE": "AT", "ON": "BE", "E7": "BA", "OK": "CZ",
    "OZ": "DK", "OH": "FI", "4L": "GE", "HA": "HU", "TF": "IS", "YL": "LV",
    "HB0": "LI", "LY": "LT", "LX": "LU", "ER": "MD", "4O": "ME", "PA": "NL",
    "Z3": "MK", "SP": "PL", "CT": "PT", "YO": "RO", "OM": "SK", "S5": "SI",
    "HB9": "CH", "UR": "UA", "LZ": "BG", "5B": "CY", "ES": "EE", "F": "FR",
    "SV": "GR", "EI": "IE", "I": "IT", "9H": "MT", "3A": "MC", "LA": "NO",
    "YU": "RS", "EA": "ES", "SM": "SE", "TA": "TR", "EW": "BY", "UA": "RU",
}

OUT_PATH = (Path(__file__).resolve().parent.parent
            / "backend" / "ft8_appliance" / "data" / "cept_borders.json")
MIN_ISLAND_DIAG = 0.25   # Grad — kleinere Ring-Inseln verwerfen
MAX_EPS = 0.012          # RDP-Toleranz-Deckel (Grad, ~1.3 km)


def _ring_diag(ring: list[list[float]]) -> float:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def _rdp_open(points: list[list[float]], eps: float) -> list[list[float]]:
    if len(points) < 3:
        return points[:]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        s, e = stack.pop()
        x1, y1 = points[s]
        x2, y2 = points[e]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1e-12
        dmax, idx = 0.0, -1
        for i in range(s + 1, e):
            x0, y0 = points[i]
            dist = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / norm
            if dist > dmax:
                dmax, idx = dist, i
        if dmax > eps and idx != -1:
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    return [p for p, k in zip(points, keep) if k]


def _rdp_ring(ring: list[list[float]], eps: float) -> list[list[float]]:
    """RDP auf einen geschlossenen Ring: am entferntesten Punkt splitten,
    damit der degenerate erste/letzte-Punkt-Fall nicht kollabiert."""
    pts = ring[:-1] if ring and ring[0] == ring[-1] else ring[:]
    if len(pts) < 4:
        return pts
    x0, y0 = pts[0]
    m = max(range(len(pts)), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2)
    a = _rdp_open(pts[0:m + 1], eps)
    b = _rdp_open(pts[m:] + [pts[0]], eps)
    return a[:-1] + b[:-1]


def _outer_rings(geom: dict) -> list[list[list[float]]]:
    t = geom["type"]
    c = geom["coordinates"]
    if t == "Polygon":
        return [c[0]]
    if t == "MultiPolygon":
        return [poly[0] for poly in c]
    return []


def main(src: str) -> None:
    data = json.loads(Path(src).read_text(encoding="utf-8"))
    iso2geom: dict[str, dict] = {}
    for f in data["features"]:
        pr = f["properties"]
        for key in (pr.get("ISO_A2"), pr.get("ISO_A2_EH")):
            if key and key not in iso2geom:
                iso2geom[key] = f["geometry"]

    out: dict[str, list[list[list[float]]]] = {}
    total = 0
    for code, iso in CODE2ISO.items():
        geom = iso2geom.get(iso)
        if not geom:
            print(f"!! kein Polygon fuer {code} ({iso})", file=sys.stderr)
            continue
        rings = sorted(_outer_rings(geom), key=_ring_diag, reverse=True)
        kept = [rings[0]] + [r for r in rings[1:] if _ring_diag(r) > MIN_ISLAND_DIAG]
        simp = []
        for r in kept:
            eps = min(MAX_EPS, _ring_diag(r) / 150)
            sr = [[round(p[0], 4), round(p[1], 4)] for p in _rdp_ring(r, eps)]
            if len(sr) >= 3:
                simp.append(sr)
                total += len(sr)
        out[code] = simp

    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"{len(out)} Laender, {total} Punkte → {OUT_PATH} "
          f"({OUT_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: build_cept_borders.py <ne_50m_admin_0_countries.geojson>")
    main(sys.argv[1])
