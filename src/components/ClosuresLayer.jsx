// Navy Closures overlay — San Clemente Island military safety zones +
// offshore operations areas, recolored by the day selected on the closures
// day-strip. Mirrors MpaLayer (polygon -> SVG path, click -> popup) and adds
// per-day status coloring. Data: public/data/navy-closures.geojson, written by
// pipeline/fetch_sci_closures.py (scraped from scisland.org). CA region only.

import { useEffect, useMemo, useState } from "react";
import { project } from "../lib/mapData.js";
import { dataPath } from "../lib/region.js";

// Status -> color. open = clear, scheduled = timed ops (dashed amber),
// restricted = closed (solid red), unknown = no data for that day (grey).
const STATUS_STYLE = {
  open:       { stroke: "#16a34a", fill: "rgba(22, 163, 74, 0.08)" },
  scheduled:  { stroke: "#f59e0b", fill: "rgba(245, 158, 11, 0.20)" },
  restricted: { stroke: "#dc2626", fill: "rgba(220, 38, 38, 0.24)" },
  unknown:    { stroke: "#7f8c8d", fill: "rgba(127, 140, 141, 0.06)" },
};
export function styleForStatus(status) {
  return STATUS_STYLE[status] || STATUS_STYLE.unknown;
}
export function isClosed(status) {
  return status === "restricted" || status === "scheduled";
}

// Single shared promise so the layer + the day-strip fetch the file once.
let closuresPromise = null;
export function loadClosures() {
  if (closuresPromise) return closuresPromise;
  closuresPromise = fetch(dataPath("/data/navy-closures.geojson"))
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return closuresPromise;
}

function ringToPath(ring, w, h) {
  if (!ring.length) return "";
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

export default function ClosuresLayer({ width, height, active, selectedDay = 0, onSelect }) {
  const [fc, setFc] = useState(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    loadClosures().then((data) => {
      if (!cancelled && data) setFc(data);
    });
    return () => { cancelled = true; };
  }, [active]);

  const dates = fc?.dates || [];
  const selectedDate = dates[selectedDay] ?? dates[0];

  // Project geometry once per (features, size) — it's day-invariant. The fill
  // recomputes per render from selectedDate, which is cheap (a dict lookup).
  const geom = useMemo(() => {
    const feats = fc?.features || [];
    return feats.map((f) => ({
      id: f.properties.id,
      props: f.properties,
      d: geometryToPath(f.geometry, width, height),
    }));
  }, [fc, width, height]);

  if (!active || !geom.length || !selectedDate) return null;

  return (
    <g className="closures-layer">
      {geom.map((g) => {
        const status = g.props.statusByDate?.[selectedDate]?.status || "unknown";
        const style = styleForStatus(status);
        const closed = isClosed(status);
        return (
          <path
            key={g.id}
            d={g.d}
            fill={style.fill}
            stroke={style.stroke}
            strokeWidth={closed ? 2 : 1.3}
            strokeOpacity="0.92"
            strokeDasharray={status === "scheduled" ? "5 3" : undefined}
            style={{ cursor: "pointer", pointerEvents: "visiblePainted" }}
            onMouseDown={(e) => e.stopPropagation()}
            onTouchStart={(e) => e.stopPropagation()}
            onTouchMove={(e) => e.stopPropagation()}
            onTouchEnd={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onSelect?.({
                zone: g.props,
                date: selectedDate,
                meta: {
                  generated_at: fc.generated_at,
                  source_updated: fc.source_updated,
                  published_window: fc.published_window,
                },
              });
            }}
          >
            <title>{g.props.label} — {status}</title>
          </path>
        );
      })}
    </g>
  );
}
