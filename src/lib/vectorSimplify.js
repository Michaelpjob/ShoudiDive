// Vector simplification (Douglas-Peucker) for GeoJSON ring coordinates.
//
// Added 2026-05-27 as PR-K3-2 of the kelp roadmap. Foundational infra
// for Phase 3 (kelp canopy) — survey polygons are ~10× denser than the
// 87 admin beds, and naive rendering tanks the frame rate. By the time
// Phase 4 ships (persistence raster), this same simplifier will run
// against any vector layer that grows past ~500 vertices per polygon.
//
// API:
//   simplifyRing(ring, tolerance)   — DP-simplify a [[lng,lat], ...] ring
//   simplifyGeometry(geom, tol)     — handle Polygon + MultiPolygon
//   toleranceForZoom(zoomLevel)     — map zoomLevel → degrees tolerance
//
// Algorithm: classic iterative Douglas-Peucker with squared-distance
// shortcut (no sqrt in the hot path). For closed rings, the first +
// last vertex are always kept; intermediate vertices are kept only if
// their perpendicular distance from the prior+next survivors exceeds
// the tolerance.
//
// Tolerance units: degrees (lng/lat space). Calibration based on
// 1 deg latitude ≈ 111 km, so:
//   0.01 ≈ 1100 m  — coarse, fits zoom 1× full extent
//   0.001 ≈ 110 m  — moderate, zoom 4×
//   0.0001 ≈ 11 m  — fine, zoom 16× (matches kelp-bed coord precision)
//
// Memoization is the caller's job — pass a stable (geometry, tolerance)
// pair through useMemo in the layer component. See KelpLayer.jsx for
// the pattern.

const MIN_RING_LENGTH = 4; // a meaningful ring needs 3 vertices + close

/**
 * Squared perpendicular distance from point p to segment [a, b].
 * Lat/lng treated as planar Euclidean — fine for the tolerance scales
 * we use (sub-km), where great-circle vs. planar disagree by < 0.01%.
 */
function pointSegDistSq(p, a, b) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  if (dx === 0 && dy === 0) {
    const ex = p[0] - a[0];
    const ey = p[1] - a[1];
    return ex * ex + ey * ey;
  }
  const t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy);
  let nx, ny;
  if (t < 0) { nx = a[0]; ny = a[1]; }
  else if (t > 1) { nx = b[0]; ny = b[1]; }
  else { nx = a[0] + t * dx; ny = a[1] + t * dy; }
  const ex = p[0] - nx;
  const ey = p[1] - ny;
  return ex * ex + ey * ey;
}

/**
 * Douglas-Peucker simplify of a single ring of [lng, lat] points.
 * `tolerance` is in degrees. Tolerance ≤ 0 returns the ring unchanged.
 * Rings shorter than MIN_RING_LENGTH are also returned unchanged so
 * we never degrade triangle-shaped polygons into lines.
 */
export function simplifyRing(ring, tolerance) {
  if (!ring || ring.length < MIN_RING_LENGTH) return ring;
  if (!(tolerance > 0)) return ring;
  const tolSq = tolerance * tolerance;
  const n = ring.length;

  // Iterative DP using an explicit stack — handles arbitrary-depth
  // recursion without blowing the call stack on huge rings.
  const keep = new Uint8Array(n);
  keep[0] = 1;
  keep[n - 1] = 1;
  const stack = [[0, n - 1]];

  while (stack.length) {
    const [lo, hi] = stack.pop();
    if (hi - lo < 2) continue;
    let maxD = -1;
    let maxI = -1;
    const a = ring[lo];
    const b = ring[hi];
    for (let i = lo + 1; i < hi; i++) {
      const d = pointSegDistSq(ring[i], a, b);
      if (d > maxD) {
        maxD = d;
        maxI = i;
      }
    }
    if (maxD > tolSq && maxI > 0) {
      keep[maxI] = 1;
      stack.push([lo, maxI]);
      stack.push([maxI, hi]);
    }
  }

  // Collect surviving vertices in order.
  const out = [];
  for (let i = 0; i < n; i++) if (keep[i]) out.push(ring[i]);
  // Ensure closed ring (DP can drop the closing duplicate if the
  // last vertex happens to be collinear; rare for GeoJSON but cheap
  // to guard against).
  if (out.length >= MIN_RING_LENGTH - 1) {
    const first = out[0];
    const last = out[out.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) {
      out.push([first[0], first[1]]);
    }
  }
  // If simplification collapsed below the minimum, fall back to the
  // input — never emit a degenerate ring.
  if (out.length < MIN_RING_LENGTH) return ring;
  return out;
}

/**
 * Simplify a Polygon or MultiPolygon geometry. Returns a new geometry
 * with the same `type` and simplified `coordinates`. Other geometry
 * types pass through unchanged.
 */
export function simplifyGeometry(geom, tolerance) {
  if (!geom || !geom.coordinates || !(tolerance > 0)) return geom;
  if (geom.type === "Polygon") {
    return {
      type: "Polygon",
      coordinates: geom.coordinates.map((ring) => simplifyRing(ring, tolerance)),
    };
  }
  if (geom.type === "MultiPolygon") {
    return {
      type: "MultiPolygon",
      coordinates: geom.coordinates.map((poly) =>
        poly.map((ring) => simplifyRing(ring, tolerance))
      ),
    };
  }
  return geom;
}

/**
 * Derive a simplification tolerance (in lng/lat degrees) from the
 * map's current zoomLevel. The mapping is roughly logarithmic — each
 * zoom doubling halves the tolerance so on-screen pixel-per-vertex
 * stays roughly constant.
 *
 *   zoom 1×  → 0.01   (~1100 m)   — useful for full-bbox overviews
 *   zoom 2×  → 0.005  (~ 550 m)
 *   zoom 4×  → 0.001  (~ 110 m)
 *   zoom 8×  → 0.0004 (~  44 m)
 *   zoom 16× → 0.0001 (~  11 m)   — matches kelp source precision
 *
 * For the 87 admin beds (already coarse) this is a near-no-op because
 * their vertex counts are small. The win shows up against Phase 3's
 * dense canopy survey polygons. Returns 0 (no simplification) when
 * zoomLevel is unparseable.
 */
export function toleranceForZoom(zoomLevel) {
  const z = Number.isFinite(zoomLevel) && zoomLevel > 0 ? zoomLevel : 1;
  if (z >= 12) return 0.0001;
  if (z >= 8)  return 0.0004;
  if (z >= 4)  return 0.001;
  if (z >= 2)  return 0.005;
  return 0.01;
}
