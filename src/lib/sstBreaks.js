// Temperature-break (SST front) outline, derived client-side from the SST
// grid the map is ALREADY displaying. No pipeline change, no new artifact,
// and the SST pixels themselves are never modified — the outline renders on
// its own transparent canvas layered above the temperature field.
//
// Method: NaN-aware 3x3 gaussian smoothing (to suppress the 8-bit
// quantization steps of the published PNG — range 9..25 degC over 254
// levels = 0.063 degC/step), central-difference gradient converted to
// degC/km via the region bbox, thresholded at BREAK_THRESHOLD_C_PER_KM.
//
// The threshold is a user-facing knob → registered provisional in
// pipeline/validation/knobs_registry.json ("sst_breaks.threshold").
// Empirical anchor (2026-08-11, live prod MUR grids): median gradient
// 0.013 degC/km, p99 0.079, p99.9 0.116 — at 0.1 the mask covers ~0.2% of
// ocean pixels and 69% of masked pixels recur the next day (real fronts
// persist; daily wobble does not).
//
// Honesty (STRICT-SCIENCE): this derives from MUR L4, itself a gap-filled
// ANALYSIS — breaks are analysis-derived, not direct observations. On days
// SST falls back to the ~9 km blended source, gradients smear into mush;
// the caller must pass sourceFallback=true and we return null rather than
// draw confident lines from degraded input.

export const BREAK_THRESHOLD_C_PER_KM = 0.1;

// 1D binomial kernel; separable pass ≈ gaussian sigma ~0.85px. Cheap and
// enough to kill quantization noise without eating real fronts.
const K = [0.25, 0.5, 0.25];

function smoothNaNAware(data, w, h) {
  // Normalized convolution: smooth (value·mask) / smooth(mask) so NaN
  // cells contribute nothing instead of dragging neighbours toward zero.
  const val = new Float32Array(w * h);
  const wt = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) {
    if (Number.isFinite(data[i])) { val[i] = data[i]; wt[i] = 1; }
  }
  const pass = (src, horizontal) => {
    const out = new Float32Array(w * h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        let acc = 0;
        for (let k = -1; k <= 1; k++) {
          const xx = horizontal ? Math.min(w - 1, Math.max(0, x + k)) : x;
          const yy = horizontal ? y : Math.min(h - 1, Math.max(0, y + k));
          acc += K[k + 1] * src[yy * w + xx];
        }
        out[y * w + x] = acc;
      }
    }
    return out;
  };
  const sv = pass(pass(val, true), false);
  const sw = pass(pass(wt, true), false);
  const out = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) {
    // Cells with too little real support stay NaN (never invent ocean).
    out[i] = sw[i] > 0.3 ? sv[i] / sw[i] : NaN;
  }
  return out;
}

/**
 * Compute the break mask for an SST grid.
 *
 * @param grid  {data: Float32Array|number[], width, height} — decoded degC,
 *              NaN = no data. NOT modified.
 * @param bbox  {latMin, latMax, lngMin, lngMax} of the grid — the same
 *              object shape mapData.js exports as BBOX.
 * @param opts  {threshold?: degC/km, sourceFallback?: boolean}
 * @returns {mask: Uint8Array, width, height, breakPx, oceanPx} or null when
 *          the input can't support honest gradients (fallback source, tiny
 *          grid, or no finite cells).
 */
export function computeBreakMask(grid, bbox, opts = {}) {
  const threshold = opts.threshold ?? BREAK_THRESHOLD_C_PER_KM;
  if (opts.sourceFallback) return null;
  if (!grid || !grid.data || !grid.width || !grid.height) return null;
  const { width: w, height: h, data } = grid;
  // Below ~50 px across, one pixel spans >20 km and "a break" loses
  // meaning — refuse rather than draw coarse mush.
  if (w < 50 || h < 50) return null;

  const { latMin, latMax, lngMin, lngMax } = bbox || {};
  if (![latMin, latMax, lngMin, lngMax].every(Number.isFinite)) return null;
  const midLat = (latMin + latMax) / 2;
  const kmY = ((latMax - latMin) * 111.0) / h;
  const kmX = ((lngMax - lngMin) * 111.0 * Math.cos((midLat * Math.PI) / 180)) / w;

  const s = smoothNaNAware(data, w, h);

  const mask = new Uint8Array(w * h);
  let breakPx = 0;
  let oceanPx = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const c = y * w + x;
      if (!Number.isFinite(data[c])) continue;   // honest: never mark no-data
      oceanPx++;
      const l = s[c - 1], r = s[c + 1], u = s[c - w], d = s[c + w];
      if (!Number.isFinite(l) || !Number.isFinite(r) ||
          !Number.isFinite(u) || !Number.isFinite(d)) continue;
      const gx = (r - l) / (2 * kmX);
      const gy = (d - u) / (2 * kmY);
      if (Math.sqrt(gx * gx + gy * gy) >= threshold) {
        mask[c] = 1;
        breakPx++;
      }
    }
  }
  if (oceanPx === 0) return null;
  return { mask, width: w, height: h, breakPx, oceanPx };
}
