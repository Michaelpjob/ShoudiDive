// Small read-only widgets shown next to each saved-spot row when the
// SST layer is active. Both render off the d-N..d0 grids the
// fetch.py history pipeline already publishes — no new fetches.
//
//   <SstTrendChip lng={...} lat={...} units="F"/>
//     A compact pill: "▲ 1.2°F / 3d" green or "▼ 0.8°F / 3d" blue.
//     Returns null when there isn't enough history to compute.
//
//   <SstSparkline lng={...} lat={...} units="F"/>
//     7 tiny dots colored by SST_TREND palette, anchored to the per-cell
//     ΔT vs the 7-day mean. Hovering reads the day + value out via
//     a native <title>.

import { sstTrendColor, SST_TREND_RANGE_C } from "../lib/mapData.js";
import {
  getSstTrend,
  getSstSparkline,
  getSstHistorySummary,
  SST_TREND_DAYS,
} from "../lib/dataSource.js";


/** °C → display unit (°F or °C) for the chip. */
function dispDelta(deltaC, units) {
  return units === "F" ? deltaC * 9 / 5 : deltaC;
}

function dispUnit(units) {
  return units === "F" ? "°F" : "°C";
}


/** "▲ 1.2°F / 3d" — colored green warming / blue cooling / grey steady. */
export function SstTrendChip({ lng, lat, units = "F", days = SST_TREND_DAYS }) {
  const { deltaC } = getSstTrend(lng, lat, days);
  if (!Number.isFinite(deltaC)) return null;

  // Below the noise floor — show "steady" rather than tiny noise readings.
  // 0.2 °C ≈ 0.36 °F, slightly looser than typical satellite per-pixel
  // accuracy so a real ±0.5 °F trend still reads as a direction.
  const NOISE_FLOOR_C = 0.2;
  const isSteady = Math.abs(deltaC) < NOISE_FLOOR_C;

  const arrow = isSteady ? "·" : (deltaC > 0 ? "▲" : "▼");
  const v = dispDelta(deltaC, units);
  const u = dispUnit(units);
  const sign = isSteady ? "" : (v > 0 ? "+" : "");
  const text = `${arrow} ${sign}${v.toFixed(1)}${u} / ${days}d`;

  // Use the same diverging palette as the map for visual consistency.
  // The chip also uses background + foreground to read on every theme.
  const bg = isSteady
    ? "color-mix(in srgb, var(--ink-3) 22%, transparent)"
    : sstTrendColor(deltaC);

  return (
    <span
      className="sst-trend-chip mono"
      style={{
        background: bg,
        color: isSteady ? "var(--ink-2)" : "rgba(15, 23, 42, 0.92)",
      }}
      title={isSteady
        ? `Within ±${(NOISE_FLOOR_C * (units === "F" ? 9 / 5 : 1)).toFixed(1)}${u} of ${days}-day baseline`
        : `Now − ${days} days ago: ${sign}${v.toFixed(2)}${u}`}
    >
      {text}
    </span>
  );
}


/** 7 tiny circles arrayed left-to-right, color-coded by ΔT vs the 7-day
 *  mean at this cell. NaN slots render dimmed grey so a coverage gap
 *  reads as "no satellite that day", not "missing component". */
export function SstSparkline({ lng, lat, units = "F", width = 56, height = 12 }) {
  const summary = getSstHistorySummary();
  const samples = getSstSparkline(lng, lat);
  if (!summary?.days?.length || !samples?.length) return null;

  // Anchor coloring to the local 7-day mean so each spot's sparkline
  // is read against ITS OWN baseline (rather than the bbox average,
  // which would make low-temp Monterey look "always cold" + high-temp
  // Coronados look "always warm"). Effect: a flat sparkline reads as
  // "stable for this spot," matching the chip's read.
  const valid = samples.filter((v) => Number.isFinite(v));
  if (valid.length < 2) return null;
  const mean = valid.reduce((s, v) => s + v, 0) / valid.length;

  const N = samples.length;
  const r = Math.min(width / N / 2.4, height / 2.4);
  const cy = height / 2;

  return (
    <svg
      className="sst-sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`SST trend over ${N} days`}
    >
      {samples.map((v, i) => {
        // Distribute centers evenly with a half-step inset so the
        // first/last dots aren't clipped by the SVG edge.
        const cx = ((i + 0.5) / N) * width;
        if (!Number.isFinite(v)) {
          return (
            <circle key={i} cx={cx} cy={cy} r={r * 0.6}
                    fill="rgb(180,180,180)" opacity="0.35">
              <title>{summary.days[i]?.date}: no data</title>
            </circle>
          );
        }
        const fill = sstTrendColor(v - mean);
        const dispV = units === "F" ? v * 9 / 5 + 32 : v;
        return (
          <circle key={i} cx={cx} cy={cy} r={r} fill={fill}>
            <title>
              {summary.days[i]?.date}: {dispV.toFixed(1)}{dispUnit(units)}
            </title>
          </circle>
        );
      })}
    </svg>
  );
}
