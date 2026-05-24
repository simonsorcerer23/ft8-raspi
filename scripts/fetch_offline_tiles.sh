#!/usr/bin/env bash
# fetch_offline_tiles.sh — download OSM tiles for offline FT8-Appliance use.
#
# Strategy (intentional, see architecture.md §4.4 / §6.7):
#   * Zoom 0-3: full world. ~85 tiles, ~5 MB. Always shows continents.
#   * Zoom 4-6: north of 60°S only. ~5 000 tiles, ~50 MB. Skips empty ocean.
#   * Zoom 7-8: Europe + USA + Japan (the FT8 traffic centres). ~50 000 tiles, ~250 MB.
#
# Total ~300 MB which fits on the 1 TB NVMe many times over. We use OSM's
# public tile servers — be a good citizen and rate-limit, comply with their
# tile usage policy (<10 req/sec, real User-Agent).
#
# Tiles land in $TILES_DIR (default /opt/ft8-appliance/tiles), structure
# is {z}/{x}/{y}.png so Leaflet's url-template works directly.

set -euo pipefail

TILES_DIR="${TILES_DIR:-/opt/ft8-appliance/tiles}"
USER_AGENT="${USER_AGENT:-ft8-hochgericht-appliance/0.1 (operator: DK9XR)}"
TILE_SERVER="${TILE_SERVER:-https://tile.openstreetmap.org}"
RATE_LIMIT_SLEEP="${RATE_LIMIT_SLEEP:-0.15}"  # ~6 req/sec

mkdir -p "$TILES_DIR"

fetch_tile() {
    local z=$1 x=$2 y=$3
    local out="${TILES_DIR}/${z}/${x}/${y}.png"
    [ -f "$out" ] && return 0
    mkdir -p "$(dirname "$out")"
    curl -s -A "$USER_AGENT" -o "$out" "${TILE_SERVER}/${z}/${x}/${y}.png" || true
    sleep "$RATE_LIMIT_SLEEP"
}

count_tiles() {
    local z=$1
    echo $(( (2 ** z) * (2 ** z) ))
}

echo "→ Zoom 0-3: full world"
for z in 0 1 2 3; do
    n=$((2 ** z))
    for x in $(seq 0 $((n - 1))); do
        for y in $(seq 0 $((n - 1))); do
            fetch_tile "$z" "$x" "$y"
        done
    done
done

echo "→ Zoom 4-6: north of 60°S"
# y bounds for tiles north of 60°S at zoom z: y from 0 to ~ 2^z * (1 - mercator(60°S)) / 2
# practical heuristic: skip the bottom ~10% of tiles
for z in 4 5 6; do
    n=$((2 ** z))
    y_max=$(( n - n / 10 ))
    for x in $(seq 0 $((n - 1))); do
        for y in $(seq 0 $y_max); do
            fetch_tile "$z" "$x" "$y"
        done
    done
done

echo "→ Zoom 7-8: EU + NA + JA (rough bounding boxes)"
# Bounding boxes given as (x_min, x_max, y_min, y_max) per zoom.
# Approx for z=7:  EU (62-72, 38-48), NA (28-50, 38-54), JA (108-114, 50-56)
# Approx for z=8:  scaled 2x
for z in 7 8; do
    case $z in
        7) BOXES=("62 72 38 48" "28 50 38 54" "108 114 50 56") ;;
        8) BOXES=("124 144 76 96" "56 100 76 108" "216 228 100 112") ;;
    esac
    for box in "${BOXES[@]}"; do
        read -r xmin xmax ymin ymax <<< "$box"
        for x in $(seq "$xmin" "$xmax"); do
            for y in $(seq "$ymin" "$ymax"); do
                fetch_tile "$z" "$x" "$y"
            done
        done
    done
done

echo "Done. Tiles cached in $TILES_DIR ($(du -sh "$TILES_DIR" | cut -f1))"
