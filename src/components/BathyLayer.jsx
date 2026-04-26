import { useEffect, useMemo, useState } from "react";
import { project } from "../lib/mapData.js";

// Class-driven marker styling per design.
const CLASS_STYLE = {
  seamount:        { color: "#0d2540", glyph: "▲" },
  bank:            { color: "#0d2540", glyph: "▲" },
  reef:            { color: "#7c2d12", glyph: "◆" },
  basin:           { color: "#475569", glyph: "▽" },
  trough:          { color: "#475569", glyph: "▽" },
  anchorage:       { color: "#065f46", glyph: "⚓" },
  landmark:        { color: "#065f46", glyph: "⚓" },
  "community-spot":{ color: "#a16207", glyph: "✚" },
};
const DEFAULT_STYLE = { color: "#475569", glyph: "•" };

export function styleForClass(cls) {
  return CLASS_STYLE[cls] || DEFAULT_STYLE;
}

// Importance tier → minimum zoom factor at which the marker becomes visible.
// We approximate "zoom" as the ratio of full-extent viewBox to current vbW.
const TIER_MIN_ZOOM = {
  marquee: 0,    // always visible
  major:   1.4,
  minor:   2.5,
  detail:  4.0,
};

let bathyPromise = null;
function loadBathyFeatures() {
  if (bathyPromise) return bathyPromise;
  bathyPromise = fetch("/data/bathy-features.geojson", { cache: "force-cache" })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return bathyPromise;
}

export default function BathyLayer({ width, height, active, zoomLevel, onSelect }) {
  const [features, setFeatures] = useState(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    loadBathyFeatures().then((fc) => {
      if (cancelled || !fc) return;
      setFeatures(fc.features || []);
    });
    return () => {
      cancelled = true;
    };
  }, [active]);

  const visible = useMemo(() => {
    if (!features) return [];
    const z = Number.isFinite(zoomLevel) ? zoomLevel : 1;
    return features.filter((f) => {
      const tier = f.properties.importanceTier || "minor";
      const minZ = TIER_MIN_ZOOM[tier] ?? 0;
      return z >= minZ;
    });
  }, [features, zoomLevel]);

  if (!active || !visible.length) return null;

  return (
    <g className="bathy-layer">
      {visible.map((f) => {
        const [lng, lat] = f.geometry.coordinates;
        const [x, y] = project(lng, lat, width, height);
        const props = f.properties;
        const sty = styleForClass(props.class);
        return (
          <g
            key={props.id}
            className="bathy-feature"
            style={{ cursor: "pointer", pointerEvents: "all" }}
            onClick={(e) => {
              e.stopPropagation();
              onSelect?.(props);
            }}
          >
            {/* Tap target */}
            <circle cx={x} cy={y} r="14" fill="transparent" />
            {/* Glyph marker */}
            <text
              x={x}
              y={y + 4}
              fontSize="12"
              fill={sty.color}
              textAnchor="middle"
              fontFamily="Inter, sans-serif"
              style={{
                paintOrder: "stroke",
                stroke: "var(--bg)",
                strokeWidth: 3,
                strokeLinejoin: "round",
              }}
            >
              {sty.glyph}
            </text>
            {/* Label to the right */}
            <text
              x={x + 9}
              y={y + 3}
              fontSize="10.5"
              fill={sty.color}
              fontFamily="Inter, sans-serif"
              fontWeight="500"
              style={{
                paintOrder: "stroke",
                stroke: "var(--bg)",
                strokeWidth: 3,
                strokeLinejoin: "round",
              }}
            >
              {props.shortName || props.name}
            </text>
          </g>
        );
      })}
    </g>
  );
}
