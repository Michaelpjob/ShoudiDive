import { useEffect, useMemo, useState } from "react";
import { project, BBOX } from "../lib/mapData.js";

// ---- Coastline + island GeoJSON (clipped Natural Earth 10 m) ---------------

let landPromise = null;
function loadLand() {
  if (landPromise) return landPromise;
  // Default cache mode (not force-cache) so the browser revalidates against
  // the CDN's ETag instead of pinning the first-fetched copy forever.
  // force-cache used to make sense when the coastline never changed; with
  // OSM-derived geometry that's no longer true.
  landPromise = fetch("/data/land.geojson")
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return landPromise;
}

function ringToPath(ring, w, h) {
  if (!ring.length) return "";
  // 3-decimal precision (0.001 base-units) so the path stays sharp even at
  // ~30× zoom. At toFixed(1) the path snapped to 0.1 px which looked fine
  // when the basemap was Natural Earth 10 m (200 verts) but visibly
  // re-blockified the OSM coastline once zoom > 4×.
  const [x0, y0] = project(ring[0][0], ring[0][1], w, h);
  let d = `M${x0.toFixed(3)} ${y0.toFixed(3)}`;
  for (let i = 1; i < ring.length; i++) {
    const [x, y] = project(ring[i][0], ring[i][1], w, h);
    d += `L${x.toFixed(3)} ${y.toFixed(3)}`;
  }
  return d + "Z";
}

function geomToPath(geom, w, h) {
  if (!geom) return "";
  if (geom.type === "Polygon") {
    return geom.coordinates.map((r) => ringToPath(r, w, h)).join(" ");
  }
  if (geom.type === "MultiPolygon") {
    return geom.coordinates
      .flatMap((poly) => poly.map((r) => ringToPath(r, w, h)))
      .join(" ");
  }
  return "";
}

// Approximate-area (in screen px²) for sorting label placement / culling.
function geomBoundsArea(geom, w, h) {
  let pts = [];
  if (geom.type === "Polygon") {
    for (const r of geom.coordinates) pts = pts.concat(r);
  } else if (geom.type === "MultiPolygon") {
    for (const poly of geom.coordinates) for (const r of poly) pts = pts.concat(r);
  }
  if (!pts.length) return 0;
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const [x0] = project(Math.min(...xs), Math.max(...ys), w, h);
  const [x1] = project(Math.max(...xs), Math.max(...ys), w, h);
  const [, y0] = project(0, Math.max(...ys), w, h);
  const [, y1] = project(0, Math.min(...ys), w, h);
  return Math.abs(x1 - x0) * Math.abs(y1 - y0);
}

// ---- Place labels (independent of geometry) --------------------------------

// Place labels exported for the screen-space MapLabels overlay; we don't
// render them in SVG anymore (they grew too big at zoom and overlapped).
// `priority`: bigger ones win collisions (cities > regions > water).
export const PLACE_LABELS = [
  // ---- NorCal (added 2026-05-09 with bbox extension to 42°N) ----
  // Coordinates are nominal city centroids; each label renders at
  // the projected (lng, lat) by MapLabels.jsx. Priority follows the
  // same convention as SoCal labels: cities ≥ 5, regions/water ≤ 4.
  { key: "lbl-crescent-city",     text: "CRESCENT CITY",      lng: -124.20, lat: 41.76, fontSize: 11, priority: 5 },
  { key: "lbl-eureka",            text: "EUREKA",             lng: -124.16, lat: 40.81, fontSize: 11, weight: 500, priority: 6 },
  { key: "lbl-cape-mendocino",    text: "CAPE MENDOCINO",     lng: -124.41, lat: 40.44, fontSize: 11, weight: 500, priority: 6 },
  { key: "lbl-fort-bragg",        text: "FORT BRAGG",         lng: -123.81, lat: 39.45, fontSize: 11, priority: 4 },
  { key: "lbl-mendocino",         text: "MENDOCINO",          lng: -123.80, lat: 39.30, fontSize: 11, priority: 5 },
  { key: "lbl-bodega-bay",        text: "BODEGA BAY",         lng: -123.05, lat: 38.33, fontSize: 11, priority: 4 },
  { key: "lbl-pt-reyes",          text: "PT. REYES",          lng: -123.00, lat: 38.02, fontSize: 11, priority: 5 },
  { key: "lbl-san-francisco",     text: "SAN FRANCISCO",      lng: -122.42, lat: 37.77, fontSize: 12, weight: 500, priority: 7 },
  { key: "lbl-half-moon-bay",     text: "HALF MOON BAY",      lng: -122.43, lat: 37.46, fontSize: 11, priority: 4 },
  { key: "lbl-santa-cruz",        text: "SANTA CRUZ",         lng: -122.03, lat: 36.97, fontSize: 11, priority: 5 },

  // ---- Central + SoCal (existing) ----
  { key: "lbl-monterey-bay",      text: "MONTEREY BAY",       lng: -121.95, lat: 36.78, fontSize: 11, weight: 500, priority: 6 },
  { key: "lbl-big-sur",           text: "BIG SUR",            lng: -121.65, lat: 36.10, fontSize: 11, priority: 5 },
  { key: "lbl-morro-bay",         text: "MORRO BAY",          lng: -120.82, lat: 35.36, fontSize: 11, priority: 5 },
  { key: "lbl-pt-conception",     text: "PT. CONCEPTION",     lng: -120.42, lat: 34.46, fontSize: 11, weight: 500, priority: 6 },
  { key: "lbl-santa-barbara",     text: "SANTA BARBARA",      lng: -119.70, lat: 34.46, fontSize: 11, priority: 5 },
  { key: "lbl-los-angeles",       text: "LOS ANGELES",        lng: -118.20, lat: 34.10, fontSize: 12, weight: 500, priority: 7 },
  { key: "lbl-la-jolla",          text: "LA JOLLA",           lng: -117.20, lat: 32.86, fontSize: 11, priority: 5 },
  { key: "lbl-san-diego",         text: "SAN DIEGO",          lng: -117.10, lat: 32.65, fontSize: 11, weight: 500, priority: 6 },
  { key: "lbl-tijuana",           text: "TIJUANA",            lng: -116.95, lat: 32.50, fontSize: 11, priority: 5 },
  { key: "lbl-las-coronados",     text: "LAS CORONADOS",      lng: -117.32, lat: 32.30, fontSize: 10, italic: true, color: "var(--ink-3)", priority: 4 },
  { key: "lbl-channel-islands",   text: "CHANNEL ISLANDS",    lng: -119.85, lat: 33.78, fontSize: 10, italic: true, color: "var(--ink-3)", priority: 4 },
  { key: "lbl-socal-bight",       text: "SOUTHERN CA BIGHT",  lng: -118.95, lat: 33.20, fontSize: 11, italic: true, color: "var(--ink-3)", priority: 3 },
  // Region labels for the new NorCal area.
  { key: "lbl-gulf-farallones",   text: "GULF OF THE FARALLONES", lng: -122.95, lat: 37.78, fontSize: 10, italic: true, color: "var(--ink-3)", priority: 3 },
  { key: "lbl-mendocino-ridge",   text: "MENDOCINO RIDGE",    lng: -125.00, lat: 40.20, fontSize: 10, italic: true, color: "var(--ink-3)", priority: 3 },
  // Pacific moves north-ish to recenter once the bbox is taller.
  { key: "lbl-pacific",           text: "PACIFIC OCEAN",      lng: -123.50, lat: 38.50, fontSize: 13, italic: true, color: "var(--ink-3)", letterSpacing: "0.2em", priority: 2 },
];

// ---- Sea (drawn UNDER the data overlay) ------------------------------------

export function SeaBasemap({ width, height }) {
  const graticule = useMemo(() => {
    const lines = [];
    for (let lat = Math.ceil(BBOX.latMin); lat <= Math.floor(BBOX.latMax); lat++) {
      const [, y] = project(BBOX.lngMin, lat, width, height);
      lines.push({ kind: "lat", v: lat, y });
    }
    for (let lng = Math.ceil(BBOX.lngMin); lng <= Math.floor(BBOX.lngMax); lng++) {
      const [x] = project(lng, BBOX.latMin, width, height);
      lines.push({ kind: "lng", v: lng, x });
    }
    return lines;
  }, [width, height]);

  return (
    <g className="basemap basemap-sea">
      <defs>
        <linearGradient id="seaBands" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="var(--sea-deeper)" />
          <stop offset="60%" stopColor="var(--sea-deep)" />
          <stop offset="100%" stopColor="var(--sea)" />
        </linearGradient>
        <pattern id="oceanTexture" x="0" y="0" width="60" height="60" patternUnits="userSpaceOnUse">
          <rect width="60" height="60" fill="transparent" />
          <circle cx="30" cy="30" r="0.5" fill="var(--bathy-line)" opacity="0.6" />
        </pattern>
      </defs>

      <rect x="0" y="0" width={width} height={height} fill="var(--sea-deeper)" />
      <rect x="0" y="0" width={width} height={height} fill="url(#seaBands)" opacity="0.85" />
      <rect x="0" y="0" width={width} height={height} fill="url(#oceanTexture)" />

      <g className="graticule" stroke="var(--grid)" strokeWidth="0.6">
        {graticule.map((g, i) =>
          g.kind === "lat" ? (
            <g key={"lat" + i}>
              <line x1="0" y1={g.y} x2={width} y2={g.y} strokeDasharray="2 4" />
              <text
                x={8}
                y={g.y - 3}
                fontSize="9"
                fill="var(--ink-3)"
                fontFamily="JetBrains Mono, monospace"
                opacity="0.7"
              >
                {g.v}°N
              </text>
            </g>
          ) : (
            <g key={"lng" + i}>
              <line x1={g.x} y1="0" x2={g.x} y2={height} strokeDasharray="2 4" />
              <text
                x={g.x + 4}
                y={height - 8}
                fontSize="9"
                fill="var(--ink-3)"
                fontFamily="JetBrains Mono, monospace"
                opacity="0.7"
              >
                {g.v}°W
              </text>
            </g>
          )
        )}
      </g>
    </g>
  );
}

// ---- Land (drawn ON TOP of data overlay so land naturally clips it) --------

// ---- Ocean mask (defs only) ---------------------------------------------
//
// SVG mask that hides land cells inside any group it's applied to.
// White = visible (ocean), black = hidden (land). Used by App.jsx to
// constrain the data-overlay canvas + wind-particle foreignObject so
// neither paints over land.
//
// This exists because iOS Safari has a long-standing compositing bug:
// `<foreignObject>` content paints in its own layer that ignores
// subsequent SVG sibling z-order. Without this mask, a heatmap canvas
// or particle canvas drawn via foreignObject renders ABOVE the
// LandBasemap that comes after it in the document, even though normal
// SVG painting order should put land on top. The mask is applied at
// the parent `<g>` level, which is one of the few z-ordering primitives
// Safari does respect across foreignObject boundaries.
//
// Loads the same /data/land.geojson that LandBasemap loads — the
// fetch is module-level memoised in `loadLand()` so this is a free
// reuse, not a duplicate network round-trip.
export function OceanMaskDefs({ width, height }) {
  const [features, setFeatures] = useState(null);

  useEffect(() => {
    let cancelled = false;
    loadLand().then((fc) => {
      if (cancelled || !fc) return;
      setFeatures(fc.features);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const paths = useMemo(() => {
    if (!features) return [];
    return features.map((f, i) => ({
      id: `ocean-mask-land-${i}`,
      d: geomToPath(f.geometry, width, height),
    }));
  }, [features, width, height]);
  const oceanClipPath = useMemo(() => {
    const outer = `M0 0H${width}V${height}H0Z`;
    return `${outer} ${paths.map((p) => p.d).join(" ")}`;
  }, [paths, width, height]);

  return (
    <>
      <clipPath id="ocean-clip" clipPathUnits="userSpaceOnUse">
        <path d={oceanClipPath} clipRule="evenodd" />
      </clipPath>
      <mask
        id="ocean-mask"
        x={0}
        y={0}
        width={width}
        height={height}
        maskUnits="userSpaceOnUse"
        maskContentUnits="userSpaceOnUse"
      >
        {/* White over the entire stage = visible by default. */}
        <rect x={0} y={0} width={width} height={height} fill="white" />
        {/* Land polygons in black = hidden inside the masked group. */}
        {paths.map((p) => (
          <path key={p.id} d={p.d} fill="black" />
        ))}
      </mask>
    </>
  );
}


export function LandBasemap({ width, height }) {
  const [features, setFeatures] = useState(null);

  useEffect(() => {
    let cancelled = false;
    loadLand().then((fc) => {
      if (cancelled || !fc) return;
      // Sort larger-area features first so big mainland goes under island groups.
      const sorted = [...fc.features].sort(
        (a, b) => geomBoundsArea(b.geometry, width, height) - geomBoundsArea(a.geometry, width, height)
      );
      setFeatures(sorted);
    });
    return () => {
      cancelled = true;
    };
  }, [width, height]);

  const paths = useMemo(() => {
    if (!features) return [];
    return features.map((f, i) => ({
      id: `land-${i}`,
      d: geomToPath(f.geometry, width, height),
    }));
  }, [features, width, height]);

  return (
    <g className="basemap basemap-land">
      {paths.map((p) => (
        <path
          key={p.id}
          d={p.d}
          fill="var(--land)"
          stroke="var(--land-edge)"
          strokeWidth="1"
        />
      ))}
    </g>
  );
}

// Default export keeps backward compatibility (renders both layers stacked,
// useful in places that don't need the data overlay sandwiched between them).
export default function Basemap({ width, height }) {
  return (
    <>
      <SeaBasemap width={width} height={height} />
      <LandBasemap width={width} height={height} />
    </>
  );
}
