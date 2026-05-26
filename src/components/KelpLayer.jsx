import { useEffect, useMemo, useState } from "react";
import { project } from "../lib/mapData.js";
import { dataPath } from "../lib/region.js";

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
    .catch(() => null);
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

export default function KelpLayer({ width, height, active, onSelect }) {
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

  // Pre-project everything once per (width, height, features) — avoids
  // re-projecting on every viewBox change since viewBox handles zoom for us.
  const paths = useMemo(() => {
    if (!features) return [];
    return features.map((f) => ({
      id: f.properties.id,
      props: f.properties,
      d: geometryToPath(f.geometry, width, height),
      style: styleForStatus(f.properties.status),
    }));
  }, [features, width, height]);

  if (!active || !paths.length) return null;

  return (
    <g className="kelp-layer">
      {paths.map((p) => (
        <path
          key={p.id}
          d={p.d}
          fill={p.style.fill}
          stroke={p.style.stroke}
          strokeWidth="1.6"
          strokeOpacity="0.85"
          style={{ cursor: "pointer", pointerEvents: "visiblePainted" }}
          onMouseDown={(e) => e.stopPropagation()}
          onTouchStart={(e) => e.stopPropagation()}
          onTouchMove={(e) => e.stopPropagation()}
          onTouchEnd={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            onSelect?.(p.props);
          }}
        >
          <title>{p.props.name}{p.props.status ? ` — ${p.props.status}` : ""}</title>
        </path>
      ))}
    </g>
  );
}
