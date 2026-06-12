// Water-column UI logic (PRD water-column V1-V3) — pure functions.
//
// Geometry + formatting for the WaterColumn widget, kept free of React
// and DOM so the checkpoint layer can test the slice math directly
// (tests/checkpoints/water-column.test.js). The component owns only
// rendering; everything decidable from numbers lives here.
//
// All depths/visibilities in FEET (the viz layer contract unit).

// Visibility classes mirror the legend ticks the viz layer already
// uses in copy (0 / 10 / 20 / 30 / 50+): "good" is 25 ft+ water,
// "poor" is under 10 ft.
export const VIS_CLASS_THRESHOLDS_FT = { good: 25, fair: 10 };

export function visClass(visFt) {
  if (visFt == null || !Number.isFinite(visFt)) return null;
  if (visFt >= VIS_CLASS_THRESHOLDS_FT.good) return "good";
  if (visFt >= VIS_CLASS_THRESHOLDS_FT.fair) return "fair";
  return "poor";
}

// Exact color stops + thresholds of the existing viz legend ramp
// (shell.css .legend-bar.viz / DesktopLayout's Poor→Excellent
// buckets) so the column slices read consistently with the map.
export function vizRampColor(visFt) {
  if (visFt == null || !Number.isFinite(visFt)) return "rgb(120,120,120)";
  if (visFt < 10) return "rgb(194,65,12)";   // Poor
  if (visFt < 20) return "rgb(34,197,94)";   // Fair
  if (visFt < 30) return "rgb(6,182,212)";   // Good
  if (visFt < 50) return "rgb(3,105,161)";   // Very good
  return "rgb(31,77,117)";                   // Excellent
}

// Saved-spots row upgrade (V4): one number when the column is clear to
// the bottom (or data is missing below), two when there's a cliff.
export function formatColumnSummary(col) {
  if (!col || col.surface_ft == null) return null;
  const surf = `~${Math.round(col.surface_ft)} ft`;
  if (col.no_cliff || col.below_ft == null) return surf;
  return `${surf} → ~${Math.round(col.below_ft)} ft below`;
}

// V3 — planned-depth crossing callout. Voice stays descriptive (the
// PRD's safety rule: describe water, never advise diving).
export function crossingCallout(plannedFt, col) {
  if (!col || plannedFt == null || !Number.isFinite(plannedFt)) return null;
  if (col.no_cliff || col.cliff_ft == null) {
    return null; // clear to the bottom — nothing to cross
  }
  if (plannedFt < col.cliff_ft) {
    return `Above the cliff the whole way — ~${Math.round(col.surface_ft)} ft vis`;
  }
  return `Vis drops to ~${Math.round(col.below_ft)} ft around ` +
    `${Math.round(col.cliff_ft)} ft on the way down`;
}

/**
 * V1/V2 — slice geometry. Maps a column profile onto normalized [0,1]
 * vertical fractions the SVG scales into pixels. Anchored to the
 * point's bottom depth: a shallow shelf (bottom above the cliff)
 * yields no murk slice; deep bottoms are clipped at maxDepthFt with
 * `clipped: true` so the widget can draw a fade + "continues to N ft".
 *
 * Input col: { surface_ft, cliff_ft, below_ft, bottom_ft, no_cliff,
 *              cliff_swing_ft } (the viz_column_spots.json shape; ad
 *              hoc tapped points build the same shape from rasters).
 * Returns null when there's nothing to draw, else:
 *   { drawnDepthFt, clipped, frac(d), clear: {top, bottom},
 *     murk: {top, bottom} | null, band: {top, bottom} | null,
 *     cliffFrac | null }
 */
export function columnGeometry(col, { maxDepthFt = 60 } = {}) {
  if (!col || col.bottom_ft == null || col.bottom_ft <= 0) return null;
  const bottom = col.bottom_ft;
  const clipped = bottom > maxDepthFt;
  const drawnDepthFt = clipped ? maxDepthFt : bottom;
  const frac = (d) => Math.max(0, Math.min(1, d / drawnDepthFt));

  const hasCliff = !col.no_cliff &&
    col.cliff_ft != null && col.cliff_ft < bottom;
  if (!hasCliff) {
    return {
      drawnDepthFt, clipped, frac,
      clear: { top: 0, bottom: 1 },
      murk: null, band: null, cliffFrac: null,
    };
  }

  const cliffFrac = frac(col.cliff_ft);
  const half = (col.cliff_swing_ft || 0) / 2;
  const band = half > 0
    ? { top: frac(col.cliff_ft - half), bottom: frac(col.cliff_ft + half) }
    : null;
  return {
    drawnDepthFt, clipped, frac,
    clear: { top: 0, bottom: cliffFrac },
    murk: { top: cliffFrac, bottom: 1 },
    band,
    cliffFrac,
  };
}

// V5 — diurnal strip geometry: normalize the 24 h cliff series into
// x/y fractions + locate the "best window" (the longest run of hours
// within tolFt of the day's deepest cliff = most clear water above).
export function diurnalStrip(seriesFt, { tolFt = 1.5 } = {}) {
  if (!Array.isArray(seriesFt) || seriesFt.length < 2) return null;
  const min = Math.min(...seriesFt);
  const max = Math.max(...seriesFt);
  const span = Math.max(max - min, 0.1);
  const pts = seriesFt.map((d, i) => ({
    x: i / (seriesFt.length - 1),
    y: (d - min) / span, // 0 = shallowest cliff, 1 = deepest
    depthFt: d,
  }));
  let best = null;
  let runStart = null;
  for (let i = 0; i <= seriesFt.length; i++) {
    const inRun = i < seriesFt.length && seriesFt[i] >= max - tolFt;
    if (inRun && runStart == null) runStart = i;
    if (!inRun && runStart != null) {
      if (!best || i - runStart > best.end - best.start) {
        best = { start: runStart, end: i - 1 };
      }
      runStart = null;
    }
  }
  return { pts, minFt: min, maxFt: max, best };
}
