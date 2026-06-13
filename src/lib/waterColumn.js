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

// ---------------------------------------------------------------------------
// Shallow-water resuspension (mirror of pipeline/viz_column/{model,config}.py)
//
// Where the bottom sits above the cliff there's no clear/murk split, but
// swell still reaches the bottom and clouds the whole column — a shallow
// sandy shelf under groundswell is the murkiest water around. The grid
// raster can't resolve the shallow strip (its bottom depth is a coarse
// ~10 km cell), so the detail view recomputes this at the click point
// where it knows the precise local depth. Constants mirror config.py;
// keep them in lockstep.
// ---------------------------------------------------------------------------

const GRAVITY = 9.81; // m/s^2
const ORBITAL_VEL_CRITICAL_MS = 0.08;
const ORBITAL_VEL_SCALE_MS = 0.25;
const SHALLOW_RESUS_STRENGTH = 0.55;
const BELOW_VIS_FLOOR_FT = 3.0;
export const FT_PER_M = 3.28084;

// Hunt (1979) direct wavenumber approximation (mirror of model._wavenumber).
function wavenumber(periodS, depthM) {
  const d = Math.max(depthM, 0.1);
  const omega = (2 * Math.PI) / periodS;
  const y = (omega * omega * d) / GRAVITY;
  const c = [0.666, 0.355, 0.1608465608, 0.0632098765, 0.0217540484, 0.0065407983];
  let poly = 0;
  for (let n = 0; n < c.length; n++) poly += c[n] * Math.pow(y, n + 1);
  const kd = Math.sqrt(y * y + y / (1 + poly));
  return kd / d;
}

// Near-bottom wave orbital velocity u_b (m/s), linear theory.
export function bottomOrbitalVelocity(hsM, periodS, depthM) {
  const d = Math.max(depthM, 0.1);
  const kd = Math.min(wavenumber(periodS, d) * d, 50);
  return (Math.PI * hsM) / (periodS * Math.sinh(kd));
}

// Normalized near-bottom resuspension in [0,1]. 0 when swell can't reach
// the bottom; ramps to 1 as orbital velocity passes the stirring threshold.
export function resuspensionIndex(hsM, periodS, depthM) {
  if (!Number.isFinite(hsM) || !Number.isFinite(periodS) || !(periodS > 0)) return 0;
  if (!Number.isFinite(depthM) || depthM <= 0) return 0;
  const ub = bottomOrbitalVelocity(hsM, periodS, depthM);
  return Math.max(0, Math.min(1, (ub - ORBITAL_VEL_CRITICAL_MS) / ORBITAL_VEL_SCALE_MS));
}

// Whole-column vis in shallow (no-cliff) water: surface clarity reduced by
// bottom resuspension. Mirror of model.shallow_column_vis_ft.
export function shallowColumnVisFt(surfaceFt, resus) {
  if (!Number.isFinite(surfaceFt)) return surfaceFt;
  const atten = 1 - SHALLOW_RESUS_STRENGTH * resus;
  return Math.min(surfaceFt, Math.max(surfaceFt * atten, BELOW_VIS_FLOOR_FT));
}

// The single number a diver reads for a column: the stirred whole-column
// value in shallow (no-cliff) water, else the open-water surface clarity.
export function effectiveColumnVisFt(col) {
  if (!col) return null;
  if (col.no_cliff && col.below_ft != null) return col.below_ft;
  return col.surface_ft;
}

// Saved-spots row upgrade (V4): one number when the column is clear to
// the bottom (or data is missing below), two when there's a cliff. In
// shallow no-cliff water the single number is the stirred whole-column
// value, which can be well below the open-water surface clarity.
export function formatColumnSummary(col) {
  if (!col || col.surface_ft == null) return null;
  if (col.no_cliff) return `~${Math.round(effectiveColumnVisFt(col))} ft`;
  if (col.below_ft == null) return `~${Math.round(col.surface_ft)} ft`;
  return `~${Math.round(col.surface_ft)} ft → ~${Math.round(col.below_ft)} ft below`;
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
