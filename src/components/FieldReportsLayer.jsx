import { useEffect, useMemo, useState } from "react";
import { project } from "../lib/mapData.js";
import { dataPath } from "../lib/region.js";

// Field Reports — recent REAL observations (dive reports, buoys, turbidity
// sensors) rendered as pins. The honest counterpart to the modeled layers:
// these are measurements people actually took, not forecasts. Colour by
// source kind, opacity by freshness so the newest ground truth stands out.
const KIND_STYLE = {
  buoy:        { color: "#7dd3fc" }, // sky-300 — structured federal/CDIP feed
  turbidity:   { color: "#34d399" }, // emerald-400 — IOOS/ERDDAP sensor
  dive_report: { color: "#fbbf24" }, // amber-400 — community dive shop / forum
};
const DEFAULT_KIND_STYLE = { color: "#cbd5e1" };

export function styleForKind(kind) {
  return KIND_STYLE[kind] || DEFAULT_KIND_STYLE;
}

// Fade older observations toward the background. <=1 day reads solid;
// >=14 days reads faint. Keeps the freshest reports visually dominant.
export function freshnessOpacity(whenIso) {
  const t = whenIso ? Date.parse(whenIso) : NaN;
  if (!Number.isFinite(t)) return 0.5;
  const ageDays = (Date.now() - t) / 86400000;
  if (ageDays <= 1) return 1.0;
  if (ageDays >= 14) return 0.35;
  return 1.0 - (ageDays - 1) * (0.65 / 13);
}

let obsPromise = null;
export function loadRecentObservations() {
  if (obsPromise) return obsPromise;
  obsPromise = fetch(dataPath("/data/observations_recent.json"))
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return obsPromise;
}

export default function FieldReportsLayer({ width, height, active, zoomLevel, onSelect }) {
  const [obs, setObs] = useState(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    loadRecentObservations().then((list) => {
      if (cancelled || !Array.isArray(list)) return;
      setObs(list);
    });
    return () => { cancelled = true; };
  }, [active]);

  const points = useMemo(
    () => (obs || []).filter((o) => Number.isFinite(o?.lng) && Number.isFinite(o?.lat)),
    [obs]
  );

  if (!active || !points.length) return null;

  // Circles scale with the SVG viewBox, so divide by zoomLevel to keep a
  // constant on-screen marker size at any zoom (same trick as BathyLayer).
  const z = Number.isFinite(zoomLevel) && zoomLevel > 0 ? zoomLevel : 1;
  const tapR = 16 / z;
  const dotR = 5 / z;

  return (
    <g className="field-reports-layer">
      {points.map((o) => {
        const [x, y] = project(o.lng, o.lat, width, height);
        const sty = styleForKind(o.kind);
        const op = freshnessOpacity(o.when);
        return (
          <g
            key={o.id}
            style={{ cursor: "pointer", pointerEvents: "all" }}
            onMouseDown={(e) => e.stopPropagation()}
            onTouchStart={(e) => e.stopPropagation()}
            onTouchEnd={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onSelect?.(o); }}
          >
            {/* Tap target */}
            <circle cx={x} cy={y} r={tapR} fill="transparent" />
            {/* Marker — fixed on-screen size, faded by age */}
            <circle
              cx={x}
              cy={y}
              r={dotR}
              fill={sty.color}
              fillOpacity={op}
              stroke="var(--bg)"
              strokeWidth="1.6"
            />
          </g>
        );
      })}
    </g>
  );
}
