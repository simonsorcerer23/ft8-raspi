// Great-circle helpers — Mercator projections lie about long-distance lines.
// A QSO Germany→Japan crosses the polar region, not the south Asian sea.
// These return arrays of [lat, lon] pairs that Leaflet's L.polyline draws
// as a curved arc on a flat map (or correctly along the geodesic on a globe).

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

/**
 * Interpolate *steps* points along the great-circle between two coords.
 * Handles antimeridian crossing by splitting into two polylines if needed.
 *
 * Returns either:
 *   [[ [lat,lon], ... ]]                  — one continuous segment
 *   [[ [lat,lon], ... ], [ [lat,lon], ... ]] — split at antimeridian
 */
export function greatCircle(p1, p2, steps = 64) {
  const lat1 = p1[0] * D2R, lon1 = p1[1] * D2R;
  const lat2 = p2[0] * D2R, lon2 = p2[1] * D2R;

  // Angular distance via haversine
  const dlat = lat2 - lat1, dlon = lon2 - lon1;
  const a = Math.sin(dlat / 2) ** 2 +
            Math.cos(lat1) * Math.cos(lat2) * Math.sin(dlon / 2) ** 2;
  const d = 2 * Math.asin(Math.min(1, Math.sqrt(a)));

  if (d < 1e-9) return [[p1]];  // same point

  const out = [];
  let prevLon = null;
  let current = [];
  for (let i = 0; i <= steps; i++) {
    const f = i / steps;
    const A = Math.sin((1 - f) * d) / Math.sin(d);
    const B = Math.sin(f * d) / Math.sin(d);
    const x = A * Math.cos(lat1) * Math.cos(lon1) + B * Math.cos(lat2) * Math.cos(lon2);
    const y = A * Math.cos(lat1) * Math.sin(lon1) + B * Math.cos(lat2) * Math.sin(lon2);
    const z = A * Math.sin(lat1) + B * Math.sin(lat2);
    const lat = Math.atan2(z, Math.sqrt(x * x + y * y)) * R2D;
    const lon = Math.atan2(y, x) * R2D;

    // Antimeridian split: jump > 180° → start a new segment
    if (prevLon !== null && Math.abs(lon - prevLon) > 180) {
      out.push(current);
      current = [];
    }
    current.push([lat, lon]);
    prevLon = lon;
  }
  if (current.length) out.push(current);
  return out;
}

/** Maidenhead 4/6 char locator → centroid (lat, lon). null if unparseable. */
export function gridToLatLon(grid) {
  if (!grid || grid.length < 4) return null;
  const g = grid.toUpperCase();
  try {
    let lon = (g.charCodeAt(0) - 65) * 20 - 180;
    let lat = (g.charCodeAt(1) - 65) * 10 - 90;
    lon += parseInt(g[2]) * 2;
    lat += parseInt(g[3]) * 1;
    if (g.length >= 6) {
      lon += (g.charCodeAt(4) - 65) * (5 / 60) + 2.5 / 60;
      lat += (g.charCodeAt(5) - 65) * (2.5 / 60) + 1.25 / 60;
    } else {
      lon += 1; lat += 0.5;
    }
    return [lat, lon];
  } catch {
    return null;
  }
}
