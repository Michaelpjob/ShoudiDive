// WaterColumn — the depth-resolved visibility readout (PRD
// water-column V1 widget, V2 tap-to-slice rendering, V3 planned-depth
// marker, V5 diurnal strip).
//
// Pure presentational: the parent supplies a column profile (from
// dataSource.getColumnAt for tapped/hovered points, or
// getColumnSpot(id) for saved spots, which adds the 24 h cliff
// series). All slice math lives in src/lib/waterColumn.js so the
// checkpoint layer tests it without a DOM.
//
// Voice rule (PRD §6 ask-first): every string here DESCRIBES water;
// none of it advises whether to dive.
import { useEffect, useState } from "react";
import {
  columnGeometry,
  crossingCallout,
  diurnalStrip,
  vizRampColor,
} from "../../lib/waterColumn.js";
import { track } from "../../lib/analytics.js";

const W = 260;          // svg coordinate width
const COL_X = 74;       // column left edge
const COL_W = 112;      // column width
const COL_Y = 10;       // column top
const COL_H = 148;      // column pixel height

function depthY(geom, frac) {
  return COL_Y + frac * COL_H;
}

export default function WaterColumn({ col, title, series, compact = false }) {
  const [planOn, setPlanOn] = useState(false);
  const [plannedFt, setPlannedFt] = useState(40);

  // Fire once per mounted readout (panel open / section render).
  useEffect(() => {
    track("column_open", { has_series: !!series });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!col) {
    return (
      <div className="wc-hint">
        Hover or tap a point on the map — or pick a saved spot — to
        slice the water column there.
      </div>
    );
  }

  const maxDepthFt = col.bottom_ft != null ? Math.max(col.bottom_ft, 40) : 60;
  const geom = columnGeometry(col, { maxDepthFt: Math.min(maxDepthFt, 60) });
  if (!geom) {
    return <div className="wc-hint">No column data for this point.</div>;
  }

  const clearMidY = depthY(geom, (geom.clear.top + geom.clear.bottom) / 2);
  const murkMidY = geom.murk
    ? depthY(geom, (geom.murk.top + geom.murk.bottom) / 2)
    : null;
  const cliffY = geom.cliffFrac != null ? depthY(geom, geom.cliffFrac) : null;
  const planMax = Math.min(
    130,
    col.bottom_ft != null ? Math.round(col.bottom_ft) : 60,
  );
  const planFrac = Math.min(plannedFt / geom.drawnDepthFt, 1);
  const callout = planOn ? crossingCallout(plannedFt, col) : null;
  const strip = series ? diurnalStrip(series) : null;

  const ticks = [0, Math.round(geom.drawnDepthFt / 2), Math.round(geom.drawnDepthFt)];

  return (
    <div className={`wc-wrap${compact ? " wc-compact" : ""}`}>
      {title ? (
        <div className="wc-head">
          <span className="wc-title">{title}</span>
          <span className="lt-beta">BETA</span>
        </div>
      ) : null}

      <svg
        className="wc-svg"
        viewBox={`0 0 ${W} 178`}
        role="img"
        aria-label={
          col.no_cliff
            ? `Clear to the bottom, about ${Math.round(col.surface_ft)} feet visibility`
            : `About ${Math.round(col.surface_ft)} feet visibility above a cliff near ${Math.round(col.cliff_ft)} feet, about ${Math.round(col.below_ft)} feet below it`
        }
      >
        {/* depth ticks */}
        {ticks.map((d, i) => {
          const y = depthY(geom, d / geom.drawnDepthFt);
          return (
            <g key={i}>
              <line x1={COL_X - 26} y1={y} x2={COL_X - 4} y2={y}
                stroke="var(--line)" strokeWidth="1" />
              <text x={COL_X - 30} y={y + 3} textAnchor="end"
                className="wc-tick mono">{d} ft</text>
            </g>
          );
        })}

        {/* clear layer */}
        <rect x={COL_X} y={depthY(geom, geom.clear.top)} width={COL_W}
          height={(geom.clear.bottom - geom.clear.top) * COL_H}
          fill={vizRampColor(col.surface_ft)} opacity="0.88" />
        <text x={COL_X + COL_W + 8} y={clearMidY + 3} className="wc-val mono">
          ≈{Math.round(col.surface_ft)} ft vis
        </text>

        {/* tide swing band + cliff */}
        {geom.band ? (
          <rect x={COL_X} y={depthY(geom, geom.band.top)} width={COL_W}
            height={Math.max((geom.band.bottom - geom.band.top) * COL_H, 2)}
            fill="var(--ink)" opacity="0.14" />
        ) : null}
        {cliffY != null ? (
          <>
            <line x1={COL_X} y1={cliffY} x2={COL_X + COL_W} y2={cliffY}
              stroke="var(--ink)" strokeWidth="1.4" strokeDasharray="5 3" />
            <text x={COL_X + COL_W + 8} y={cliffY + 3} className="wc-cliff mono">
              cliff ~{Math.round(col.cliff_ft)} ft
            </text>
          </>
        ) : null}

        {/* murk layer */}
        {geom.murk ? (
          <>
            <rect x={COL_X} y={depthY(geom, geom.murk.top)} width={COL_W}
              height={(geom.murk.bottom - geom.murk.top) * COL_H}
              fill={vizRampColor(col.below_ft)} opacity="0.88" />
            <text x={COL_X + COL_W + 8} y={murkMidY + 3} className="wc-val mono">
              ≈{Math.round(col.below_ft)} ft vis
            </text>
          </>
        ) : null}

        {/* planned-depth marker + occupied slice */}
        {planOn ? (
          <>
            <rect x={COL_X} y={COL_Y} width={COL_W}
              height={Math.max(planFrac * COL_H, 0)}
              fill="var(--accent)" opacity="0.12" />
            <line x1={COL_X - 4} y1={depthY(geom, planFrac)}
              x2={COL_X + COL_W + 4} y2={depthY(geom, planFrac)}
              stroke="var(--accent)" strokeWidth="2" />
          </>
        ) : null}

        {/* bottom */}
        <rect x={COL_X} y={COL_Y + COL_H} width={COL_W} height="4"
          fill="var(--ink)" opacity={geom.clipped ? 0.18 : 0.45} />
        <text x={COL_X + COL_W / 2} y={COL_Y + COL_H + 16} textAnchor="middle"
          className="wc-bottom mono">
          {col.bottom_ft == null
            ? "depth unknown here"
            : geom.clipped
              ? `… continues to ${Math.round(col.bottom_ft)} ft`
              : `bottom ${Math.round(col.bottom_ft)} ft`}
        </text>
      </svg>

      {col.no_cliff ? (
        <div className="wc-note">
          Shallower than the cliff — the surface number applies all the
          way down.
        </div>
      ) : null}

      <div className="wc-plan-row">
        <label className="wc-plan-toggle">
          <input type="checkbox" checked={planOn}
            onChange={(e) => setPlanOn(e.target.checked)} />
          I’m diving to
        </label>
        <input type="range" min="10" max={planMax} step="5" value={plannedFt}
          disabled={!planOn} aria-label="Planned depth in feet"
          onChange={(e) => setPlannedFt(Number(e.target.value))}
          onPointerUp={() => planOn && track("column_depth_set", { ft: plannedFt })} />
        <span className="mono wc-plan-val">{plannedFt} ft</span>
      </div>
      {callout ? <div className="wc-callout">{callout}</div> : null}

      {strip ? (
        <div className="wc-strip">
          <div className="wc-strip-label">
            Cliff over the next 24 h
            <span className="mono">
              {Math.round(strip.minFt)}–{Math.round(strip.maxFt)} ft
            </span>
          </div>
          <svg viewBox={`0 0 ${W} 56`} className="wc-strip-svg" role="img"
            aria-label={`Cliff depth swings between ${Math.round(strip.minFt)} and ${Math.round(strip.maxFt)} feet over the next day`}>
            {strip.best ? (
              <rect
                x={12 + (strip.best.start / (strip.pts.length - 1)) * 236}
                y="4"
                width={Math.max(((strip.best.end - strip.best.start) / (strip.pts.length - 1)) * 236, 4)}
                height="40" fill="var(--accent)" opacity="0.12" rx="3" />
            ) : null}
            <polyline fill="none" stroke="var(--accent)" strokeWidth="1.6"
              points={strip.pts.map((p) => `${(12 + p.x * 236).toFixed(1)},${(8 + p.y * 32).toFixed(1)}`).join(" ")} />
            <circle cx="12" cy={8 + strip.pts[0].y * 32} r="3" fill="var(--accent)" />
            <text x="12" y="54" className="wc-tick mono">now</text>
            <text x="130" y="54" textAnchor="middle" className="wc-tick mono">+12 h</text>
            <text x="248" y="54" textAnchor="end" className="wc-tick mono">+23 h</text>
          </svg>
          <div className="wc-strip-hint">
            Deeper cliff = more clear water above it. Shaded band = the
            day’s deepest stretch.
          </div>
        </div>
      ) : null}

      <div className="wc-foot">
        Below-cliff vis is modeled (v1 heuristic) — uncalibrated until
        the ground-truth harness lands.
      </div>
    </div>
  );
}
