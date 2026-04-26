import { useEffect, useMemo, useState } from "react";
import { project } from "../lib/mapData.js";

// Color/style by MPA type, per design spec.
// Outline 2 px, fill 10–12% opacity.
const MPA_STYLE = {
  SMR:                { stroke: "#c0392b", fill: "rgba(192, 57, 43, 0.12)" },
  "SMCA (No-Take)":   { stroke: "#c0392b", fill: "rgba(192, 57, 43, 0.12)" },
  SMCA:               { stroke: "#e67e22", fill: "rgba(230, 126, 34, 0.12)" },
  SMP:                { stroke: "#f1c40f", fill: "rgba(241, 196, 15, 0.10)" },
  SMRMA:              { stroke: "#3498db", fill: "rgba(52, 152, 219, 0.10)" },
  FMR:                { stroke: "#8e44ad", fill: "rgba(142, 68, 173, 0.10)" },
  FMCA:               { stroke: "#8e44ad", fill: "rgba(142, 68, 173, 0.10)" },
  "Special Closure":  { stroke: "#7f8c8d", fill: "rgba(127, 140, 141, 0.16)" },
};
const DEFAULT_STYLE = { stroke: "#7f8c8d", fill: "rgba(127, 140, 141, 0.10)" };

export function styleForType(type) {
  return MPA_STYLE[type] || DEFAULT_STYLE;
}

// Single shared promise so React StrictMode + multiple toggles don't refetch.
let mpaPromise = null;
function loadMpaBoundaries() {
  if (mpaPromise) return mpaPromise;
  mpaPromise = fetch("/data/mpa-boundaries.geojson", { cache: "force-cache" })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return mpaPromise;
}

function ringToPath(ring, w, h) {
  if (!ring.length) return "";
  const [x0, y0] = project(ring[0][0], ring[0][1], w, h);
  let d = `M${x0.toFixed(1)} ${y0.toFixed(1)}`;
  for (let i = 1; i < ring.length; i++) {
    const [x, y] = project(ring[i][0], ring[i][1], w, h);
    d += `L${x.toFixed(1)} ${y.toFixed(1)}`;
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

export default function MpaLayer({ width, height, active, onSelect }) {
  const [features, setFeatures] = useState(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    loadMpaBoundaries().then((fc) => {
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
      style: styleForType(f.properties.type),
    }));
  }, [features, width, height]);

  if (!active || !paths.length) return null;

  return (
    <g className="mpa-layer">
      {paths.map((p) => (
        <path
          key={p.id}
          d={p.d}
          fill={p.style.fill}
          stroke={p.style.stroke}
          strokeWidth="1.6"
          strokeOpacity="0.85"
          style={{ cursor: "pointer", pointerEvents: "all" }}
          onClick={(e) => {
            e.stopPropagation();
            onSelect?.(p.props);
          }}
        >
          <title>{p.props.name} — {p.props.type}</title>
        </path>
      ))}
    </g>
  );
}
