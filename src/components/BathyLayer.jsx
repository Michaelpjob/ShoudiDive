import { useEffect, useMemo, useState } from "react";
import { project } from "../lib/mapData.js";

// Class-driven marker styling. Colours are tuned for the dark map
// background — every hex below clears WCAG AA contrast against the
// deep-navy sea so labels stay readable.
const CLASS_STYLE = {
  seamount:        { color: "#7dd3fc", glyph: "▲" },  // sky-300
  bank:            { color: "#7dd3fc", glyph: "▲" },
  ridge:           { color: "#7dd3fc", glyph: "▲" },
  reef:            { color: "#fb923c", glyph: "◆" },  // orange-400
  rock:            { color: "#fb923c", glyph: "◆" },
  basin:           { color: "#cbd5e1", glyph: "▽" },  // slate-300
  trough:          { color: "#cbd5e1", glyph: "▽" },
  canyon:          { color: "#cbd5e1", glyph: "▽" },
  anchorage:       { color: "#34d399", glyph: "⚓" }, // emerald-400
  landmark:        { color: "#34d399", glyph: "⚓" },
  islands:         { color: "#34d399", glyph: "⚓" },
  "community-spot":{ color: "#fbbf24", glyph: "✚" }, // amber-400
};
const DEFAULT_STYLE = { color: "#cbd5e1", glyph: "•" };

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

const TIER_PRIORITY = {
  marquee: 100,
  major:   60,
  minor:   30,
  detail:  10,
};

let bathyPromise = null;
export function loadBathyFeatures() {
  if (bathyPromise) return bathyPromise;
  bathyPromise = fetch("/data/bathy-features.geojson", { cache: "force-cache" })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return bathyPromise;
}

export function visibleBathyFeatures(features, zoomLevel) {
  if (!features) return [];
  const z = Number.isFinite(zoomLevel) ? zoomLevel : 1;
  return features.filter((f) => {
    const tier = f.properties.importanceTier || "minor";
    const minZ = TIER_MIN_ZOOM[tier] ?? 0;
    return z >= minZ;
  });
}

// Produces label entries for the screen-space MapLabels overlay.
// Anchored "left" so the label sits to the right of the marker glyph.
export function bathyLabels(features) {
  return features.map((f) => {
    const sty = styleForClass(f.properties.class);
    const tier = f.properties.importanceTier || "minor";
    return {
      key: "bathy-" + f.properties.id,
      lng: f.geometry.coordinates[0],
      lat: f.geometry.coordinates[1],
      text: f.properties.shortName || f.properties.name,
      fontSize: tier === "marquee" ? 11 : 10,
      weight: tier === "marquee" ? 600 : 500,
      color: sty.color,
      priority: TIER_PRIORITY[tier] || 30,
      anchor: "left",
      offsetX: 9,
      offsetY: -4,
    };
  });
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

  const visible = useMemo(
    () => visibleBathyFeatures(features, zoomLevel),
    [features, zoomLevel]
  );

  if (!active || !visible.length) return null;

  // SVG circles scale with viewBox, so a fixed `r="3.2"` blooms to ~26 px
  // at 8× zoom. Divide by zoomLevel to keep markers the intended on-screen
  // size at any zoom. (vector-effect: non-scaling-stroke handles the outline.)
  const z = Number.isFinite(zoomLevel) && zoomLevel > 0 ? zoomLevel : 1;
  const tapR = 18 / z;
  const dotR = 5 / z;

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
            <circle cx={x} cy={y} r={tapR} fill="transparent" />
            {/* Marker dot — fixed on-screen size at any zoom */}
            <circle
              cx={x}
              cy={y}
              r={dotR}
              fill={sty.color}
              stroke="var(--bg)"
              strokeWidth="1.6"
            />
          </g>
        );
      })}
    </g>
  );
}
