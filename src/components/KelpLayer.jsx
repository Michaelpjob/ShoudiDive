import { useEffect, useMemo, useState } from "react";
import { project } from "../lib/mapData.js";
import { dataPath } from "../lib/region.js";
import { simplifyGeometry, toleranceForZoom } from "../lib/vectorSimplify.js";

// Color/style by canopy class.
// 2026-05-27: switched the data source from CDFW Administrative Kelp
// Beds (ds3135 — lease boundaries, rectangular) to BIO_CA_Kelp2016
// (observed aerial-survey canopy). The new data carries a
// `className` field with two values: "Kelp Canopy" (surface canopy
// visible from air) and "Kelp Subsurface" (canopy detected below the
// water surface). Surface canopy is the diver-relevant one — it's
// what you see from the boat. Subsurface stays visible at lower
// opacity so the full kelp footprint reads in context.
const KELP_STYLE = {
  "kelp canopy":     { stroke: "#1b5e20", fill: "rgba(27, 94, 32, 0.36)" },
  "kelp subsurface": { stroke: "#388e3c", fill: "rgba(56, 142, 60, 0.20)" },
  // Legacy admin-bed statuses retained for fallback when a consumer
  // is still pointed at kelp-beds.geojson (admin source). Kept thin
  // so they don't compete visually with canopy when both layers ship.
  open:       { stroke: "#2e7d32", fill: "rgba(46, 125, 50, 0.10)" },
  leasable:   { stroke: "#43a047", fill: "rgba(67, 160, 71, 0.08)" },
  leased:     { stroke: "#1b5e20", fill: "rgba(27, 94, 32, 0.12)" },
  closed:     { stroke: "#6d4c41", fill: "rgba(109, 76, 65, 0.10)" },
};
const DEFAULT_STYLE = { stroke: "#558b2f", fill: "rgba(85, 139, 47, 0.18)" };

// Style chooser — keeps the original name (styleForStatus) so existing
// callsites in MapShell + KelpPopup keep working. Accepts either a
// canopy className ("Kelp Canopy" / "Kelp Subsurface") or a legacy
// admin-bed status ("open" / "closed" / etc.).
export function styleForStatus(key) {
  if (!key) return DEFAULT_STYLE;
  return KELP_STYLE[String(key).toLowerCase()] || DEFAULT_STYLE;
}

// Single shared promise so React StrictMode + multiple toggles don't refetch.
// 2026-05-27: data source flipped from kelp-beds.geojson (admin lease
// rectangles) to kelp-canopy.geojson (observed aerial-survey canopy).
// Same single-promise pattern; same shape; just a different feed.
let kelpPromise = null;
function loadKelpBeds() {
  if (kelpPromise) return kelpPromise;
  kelpPromise = fetch(dataPath("/data/kelp-canopy.geojson"))
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
  // Style key is `className` for canopy features (2026-05-27 source
  // swap) with `status` as the legacy fallback for admin-bed payloads.
  const tolerance = toleranceForZoom(zoomLevel);
  const paths = useMemo(() => {
    if (!features) return [];
    return features.map((f) => {
      const simplified = simplifyGeometry(f.geometry, tolerance);
      const styleKey = f.properties.className || f.properties.status;
      return {
        id: f.properties.id,
        props: f.properties,
        geometry: f.geometry,
        d: geometryToPath(simplified, width, height),
        style: styleForStatus(styleKey),
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
          <title>{p.props.name}{p.props.className ? ` — ${p.props.className}` : (p.props.status ? ` — ${p.props.status}` : "")}</title>
        </path>
      ))}
    </g>
  );
}
