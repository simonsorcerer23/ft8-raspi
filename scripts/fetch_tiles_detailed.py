#!/usr/bin/env python3
"""Detailed OSM-tile downloader for the FT8 appliance.

Complements the existing fetch_offline_tiles.sh (global zoom 0-6 +
broad EU/NA/JA zoom 7-8) with finer zoom levels for the regions Dad
actually operates from:

  * Zoom 9-10: Europe (35-71°N, -12-42°E) — ~30k tiles, ~600 MB
  * Zoom 11:   DACH-Region (47-55°N, 6-18°E) — ~5k tiles, ~150 MB

Respects OSM's usage policy: single-threaded, User-Agent set, ~6 req/s.
Re-runs are safe: existing tiles are skipped (idempotent).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

import httpx

UA = "ft8-hochgericht-appliance/0.1 (operator: DK9XR; contact: github.com/simonsorcerer23)"
TILE_SERVER = os.environ.get("TILE_SERVER", "https://tile.openstreetmap.org")
RATE_LIMIT_S = 0.15  # ~6 req/sec


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Convert a (lon, lat) to its Web-Mercator tile coordinates at zoom z."""
    n = 1 << z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int(
        (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi)
        / 2.0 * n
    )
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tile_range(bbox: tuple[float, float, float, float], z: int):
    """Yield (x, y) for every tile inside the given (S, W, N, E) bbox."""
    s, w, n_, e = bbox
    x0, y1 = lonlat_to_tile(w, s, z)  # south-west
    x1, y0 = lonlat_to_tile(e, n_, z)  # north-east
    xa, xb = min(x0, x1), max(x0, x1)
    ya, yb = min(y0, y1), max(y0, y1)
    for x in range(xa, xb + 1):
        for y in range(ya, yb + 1):
            yield x, y


def fetch(client: httpx.Client, z: int, x: int, y: int, tiles_dir: Path) -> bool:
    """Download one tile if missing. Returns True if fetched, False if skipped."""
    out = tiles_dir / str(z) / str(x) / f"{y}.png"
    if out.exists() and out.stat().st_size > 0:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = client.get(f"{TILE_SERVER}/{z}/{x}/{y}.png", timeout=10.0)
        if r.status_code == 200 and r.content:
            out.write_bytes(r.content)
        else:
            # 404 or rate-limit — skip silently
            return False
    except Exception as exc:
        print(f"  tile {z}/{x}/{y} failed: {exc}", file=sys.stderr)
        return False
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tiles-dir", default="/var/lib/ft8-appliance/tiles",
                   help="Output directory (default /var/lib/ft8-appliance/tiles)")
    args = p.parse_args()
    tiles_dir = Path(args.tiles_dir)
    tiles_dir.mkdir(parents=True, exist_ok=True)

    # (south, west, north, east) — slightly enlarged to cover edge tiles
    europe = (35.0, -12.0, 71.0, 42.0)
    dach   = (46.0,   5.0, 56.0, 19.0)

    targets = [
        # (label, bbox, zooms)
        ("Europe",     europe, (9, 10)),
        ("DACH",       dach,   (11, 12)),
    ]

    client = httpx.Client(headers={"User-Agent": UA})
    total_fetched = 0
    total_skipped = 0
    for label, bbox, zooms in targets:
        for z in zooms:
            tiles = list(tile_range(bbox, z))
            print(f"→ {label} zoom {z}: {len(tiles)} tiles")
            for i, (x, y) in enumerate(tiles, 1):
                if fetch(client, z, x, y, tiles_dir):
                    total_fetched += 1
                    time.sleep(RATE_LIMIT_S)
                else:
                    total_skipped += 1
                if i % 500 == 0:
                    print(f"  …{i}/{len(tiles)} ({total_fetched} fetched, "
                          f"{total_skipped} skipped)")
    print(f"\nDone. fetched={total_fetched} skipped={total_skipped}")
    print(f"Total size: {sum(p.stat().st_size for p in tiles_dir.rglob('*.png')) / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
