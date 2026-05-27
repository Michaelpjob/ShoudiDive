import { useEffect, useMemo, useState } from "react";
import { project } from "../lib/mapData.js";
import { dataPath } from "../lib/region.js";
import { simplifyGeometry, toleranceForZoom } from "../lib/vectorSimplify.js";

// Color/style by lease status, per kelp-MVP handover.
// CDFW Administrative Kelp Beds publish a STATUS field with values like
// open / leasable / leased / closed (case-insensitive — fetch_kelp.py
// lowercases for us). Outline 1.6 px to match MpaLayer; fill 10–14%
// opacity so kelp doesn't drown the SST raster underneath.
const KELP_STYLE = {
  open:       { stroke: "#2e7d32", fill: "rgba(46, 125, 50, 0.14)" },
  leasable:   { stroke: "#43a047", fill: "rgba(67, 160, 71, 0.12)" },
  leased:     { stroke: "#1b5e20", fill: "rgba(27, 94, 32, 0.16)" },
  closed:     { stroke: "#6d4c41", fill: "rgba(109, 76, 65, 0.14)" },
};
const DEFAULT_STYLE = { stroke: "#558b2f", fill: "rgba(85, 139, 47, 0.10)" };

export function styleForStatus(status) {
  if (!status) return DEFAULT_STYLE;
  return KELP_STYLE[String(status).toLowerCase()] || DEFAULT_STYLE;
}

// Single shared promise so React StrictMode + multiple toggles don't refetch.
let kelpPromise = null;
function loadKelpBeds() {
  if (kelpPromise) return kelpPromise;
  // Default cache mode so the browser revalidates with the CDN instead of
  // serving a permanently-pinned copy from disk.
  kelpPromise = fetch(dataPath("/data/kelp-beds.geojson"))
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null)
    .then(rememberKelpFc);
  return kelpPromise;
}

function ringToPath(ring, w, h) {
  if (!ring.length) return "";
  // Match Basemap.jsx: 3-decimal precision so kelp edges stay crisp at zoom.
  const [x0, y0] = project(ring[0][0], ring[0][1], w, h);
  let d = `M${x0.toFixed(3)} ${y0.toFixed(3)}`;
  for (let i = 1; i < ring.length; i++) {
    const [x, y] = project(ring[i][0], ring[i][1], w, h);
    d += `L${x.toFixed(3)} ${y.toFixed(3)}`;
  }
  return d + "Z";
}

// PR-K2-3 helpers: lng/lat-space geometry queries that don't depend on
// the projected (width, height) canvas. Used by the popup "Zoom to bed"
// action and by the saved-spot connector-line proximity check.

/**
 * Compute the lng/lat bounding box of a GeoJSON Polygon or MultiPolygon.
 * Returns { lngMin, lngMax, latMin, latMax } or null if geom is empty/unknown.
 */
export function geometryBounds(geom) {
  if (!geom || !geom.coordinates) return null;
  let lngMin = Infinity, lngMax = -Infinity, latMin = Infinity, latMax = -Infinity;
  const visit = (pt) => {
    const [lng, lat] = pt;
    if (lng < lngMin) lngMin = lng;
    if (lng > lngMax) lngMax = lng;
    if (lat < latMin) latMin = lat;
    if (lat > latMax) latMax = lat;
  };
  if (geom.type === "Polygon") {
    for (const ring of geom.coordinates) for (const pt of ring) visit(pt);
  } else if (geom.type === "MultiPolygon") {
    for (const poly of geom.coordinates) for (const ring of poly) for (const pt of ring) visit(pt);
  } else {
    return null;
  }
  if (!Number.isFinite(lngMin)) return null;
  return { lngMin, lngMax, latMin, latMax };
}

/**
 * Find the nearest kelp-bed feature to a (lng, lat) point and the
 * approximate nearest-vertex on its ring. Used by the saved-spot
 * connector line. Distance is squared-degrees for ranking (no sqrt);
 * returns null if no features are loaded or the nearest is past
 * `maxDegrees` (~0.2° ≈ 22 km at 36°N latitude).
 */
export function nearestKelpEdge(features, lng, lat, maxDegrees = 0.2) {
  if (!features || !features.length || !Number.isFinite(lng) || !Number.isFinite(lat)) {
    return null;
  }
  const maxSq = maxDegrees * maxDegrees;
  let best = null;
  for (const f of features) {
    const geom = f.geometry;
    if (!geom || !geom.coordinates) continue;
    const rings =
      geom.type === "Polygon" ? geom.coordinates :
      geom.type === "MultiPolygon" ? geom.coordinates.flat() :
      [];
    for (const ring of rings) {
      for (const pt of ring) {
        const dx = pt[0] - lng;
        const dy = pt[1] - lat;
        const d2 = dx * dx + dy * dy;
        if (d2 < maxSq && (best === null || d2 < best.d2)) {
          best = { d2, lng: pt[0], lat: pt[1], feature: f };
        }
      }
    }
  }
  return best;
}

/**
 * Synchronously return the cached kelp-bed FeatureCollection if the
 * fetch has already resolved, otherwise null. Used by the spot-pin
 * connector line — we don't want to trigger a fetch just to draw a
 * hint line; if the layer is off we silently skip the hint.
 */
let cachedKelpFc = null;
export function getCachedKelpFc() {
  return cachedKelpFc;
}
// Capture the resolved fc so synchronous consumers can read it.
function rememberKelpFc(fc) {
  if (fc) cachedKelpFc = fc;
  return fc;
}

function geometryToPath(geom, w, h) {
  if (!geom) return "";
  if (geom.type === "Polygon") {
    return geom.coordinates.map((ring) => ringToPath(ring, w, h)).join(" ");
  }
  if (geom.type === "MultiPolygon") {
    return geom.coordinates
      .flatMap((poly) => poly.map((ring) => ringToPath(ring, w, h)))
      .join(" ");
  }
  return "";
}

export default function KelpLayer({ width, height, active, zoomLevel, onSelect }) {
  const [features, setFeatures] = useState(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    loadKelpBeds().then((fc) => {
      if (cancelled || !fc) return;
      setFeatures(fc.features || []);
    });
    return () => {
      cancelled = true;
    };
  }, [active]);

  // Pre-project everything once per (width, height, features, tolerance).
  // PR-K2-3: include geometry in the row so onSelect can hand it back to
  // MapShell's zoomToFeature without re-walking the FeatureCollection.
  // PR-K3-2: simplify geometry by zoom tolerance before path building.
  // Memo key includes `tolerance` so we re-run simplification only when
  // zoom crosses a band boundary (12×/8×/4×/2×/1×). For the 87 admin
  // beds this is near-no-op since the vertex counts are already low —
  // the win is for the kelp canopy layer (Phase 3) which uses this
  // same simplifier against ~10k-vertex survey polygons.
  const tolerance = toleranceForZoom(zoomLevel);
  const paths = useMemo(() => {
    if (!features) return [];
    return features.map((f) => {
      const simplified = simplifyGeometry(f.geometry, tolerance);
      return {
        id: f.properties.id,
        props: f.properties,
        geometry: f.geometry, // unsimplified — kept for zoom-to-bed bounds calc
        d: geometryToPath(simplified, width, height),
        style: styleForStatus(f.properties.status),
      };
    });
  }, [features, width, height, tolerance]);

  if (!active || !paths.length) return null;

  // PR-K2-2: zoom-aware stroke + fill. Same math as MpaLayer so the
  // two overlays scale identically and don't visually diverge at high
  // zoom. See docs/kelp-roadmap.md § "Phase 2".
  const z = Number.isFinite(zoomLevel) && zoomLevel > 0 ? zoomLevel : 1;
  const strokeW = Math.max(0.4, 1.6 / Math.min(z, 4));
  const fillOpacityFactor = z <= 4 ? 1 : Math.max(0.35, 4 / z);

  return (
    <g className="kelp-layer">
      {paths.map((p) => (
        <path
          key={p.id}
          d={p.d}
          fill={p.style.fill}
          fillOpacity={fillOpacityFactor}
          stroke={p.style.stroke}
          strokeWidth={strokeW}
          strokeOpacity="0.85"
          style={{ cursor: "pointer", pointerEvents: "visiblePainted" }}
          onMouseDown={(e) => e.stopPropagation()}
          onTouchStart={(e) => e.stopPropagation()}
          onTouchMove={(e) => e.stopPropagation()}
          onTouchEnd={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            // Pass props + a non-enumerable-ish handle to geometry so the
            // popup can request a "zoom to bed" jump (PR-K2-3) without us
            // having to pipe a second arg through React's onClick prop.
            onSelect?.({ ...p.props, _geometry: p.geometry });
          }}
        >
          <title>{p.props.name}{p.props.status ? ` — ${p.props.status}` : ""}</title>
        </path>
      ))}
    </g>
  );
}
