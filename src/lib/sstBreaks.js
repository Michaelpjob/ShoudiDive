// Temperature-break (SST front) LINES, derived client-side from the SST
// grid the map is ALREADY displaying. No pipeline change, no new artifact,
// and the SST pixels themselves are never modified — the lines render on
// their own transparent canvas layered above the temperature field.
//
// v2 (2026-08-12): threshold-only masking drew BLOBS — locally-steep
// patches that read as "dark spots". A real break is a long edge: the
// boundary of a warm tongue pushing between Catalina and San Clemente can
// run tens of miles to the coast, strong in places and faint in others,
// but it is ONE line. So this now traces fronts the way Canny traces
// edges:
//
//   1. smooth (NaN-aware) + gradient in degC/km          — same as v1
//   2. non-maximum suppression along the gradient        — thin the steep
//      direction                                            band to its
//                                                           1-px crest
//   3. hysteresis: pixels >= THRESHOLD seed a front; the front CONTINUES
//      through connected crest pixels >= THRESHOLD_LOW — a strong segment
//      carries the faded middle of the same edge, so one physical front
//      stays one line instead of splitting into fragments
//   4. span filter: keep only fronts whose endpoints are >= MIN_SPAN_KM
//      apart — a locally-steep patch that doesn't RUN anywhere is noise,
//      not a break
//
// All three numbers are user-facing knobs → registered provisional in
// pipeline/validation/knobs_registry.json. Empirical anchor (2026-08-11,
// live prod MUR grids): gradient p50 0.013 / p99 0.079 / p99.9 0.116
// degC/km; 69% of >=0.1 pixels recur next day (real fronts persist).
//
// Honesty (STRICT-SCIENCE): derived from MUR L4, itself a gap-filled
// ANALYSIS — lines are analysis-derived, not direct observations. On days
// SST falls back to the ~9 km blended source, gradients smear; the caller
// passes sourceFallback=true and we return null rather than draw
// confident lines from degraded input.

export const BREAK_THRESHOLD_C_PER_KM = 0.1;   // seeds a front
export const BREAK_THRESHOLD_LOW_C_PER_KM = 0.05; // continues one
export const BREAK_MIN_SPAN_KM = 20;           // endpoints this far apart

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
 * Trace temperature-break lines on an SST grid.
 *
 * @param grid  {data: Float32Array|number[], width, height} — decoded degC,
 *              NaN = no data. NOT modified.
 * @param bbox  {latMin, latMax, lngMin, lngMax} of the grid — the same
 *              object shape mapData.js exports as BBOX.
 * @param opts  {threshold?, thresholdLow?, minSpanKm?, sourceFallback?}
 * @returns {mask, width, height, breakPx, oceanPx,
 *           fronts: [{px, spanKm, points: [[gx,gy],...]}]} where `points`
 *          is the front's main stem as an ORDERED, simplified polyline in
 *          grid coordinates (endpoint to endpoint — what the UI renders
 *          as an SVG path and what the GPS popup reads start/end from).
 *          Returns null when the input can't support honest gradients
 *          (fallback source, tiny grid, or no finite cells).
 */
export function computeBreakMask(grid, bbox, opts = {}) {
  const thHigh = opts.threshold ?? BREAK_THRESHOLD_C_PER_KM;
  const thLow = opts.thresholdLow ?? BREAK_THRESHOLD_LOW_C_PER_KM;
  const minSpanKm = opts.minSpanKm ?? BREAK_MIN_SPAN_KM;
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

  // Gradient field (degC/km). Cells whose 4-neighbourhood isn't fully
  // finite stay NaN — the honesty guard that keeps land-sea edges and
  // no-data boundaries from ever reading as fronts.
  const gmag = new Float32Array(w * h).fill(NaN);
  const gxArr = new Float32Array(w * h);
  const gyArr = new Float32Array(w * h);
  let oceanPx = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const c = y * w + x;
      if (!Number.isFinite(data[c])) continue;
      oceanPx++;
      const l = s[c - 1], r = s[c + 1], u = s[c - w], d = s[c + w];
      if (!Number.isFinite(l) || !Number.isFinite(r) ||
          !Number.isFinite(u) || !Number.isFinite(d)) continue;
      const gx = (r - l) / (2 * kmX);
      const gy = (d - u) / (2 * kmY);
      gxArr[c] = gx;
      gyArr[c] = gy;
      gmag[c] = Math.sqrt(gx * gx + gy * gy);
    }
  }
  if (oceanPx === 0) return null;

  // Non-maximum suppression: keep a pixel only if it is the crest of the
  // gradient ALONG the gradient direction (i.e., across the front). This
  // collapses the steep band to a ~1-px line that follows the edge.
  const crest = new Uint8Array(w * h);
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const c = y * w + x;
      const g = gmag[c];
      if (!Number.isFinite(g) || g < thLow) continue;
      // Quantize gradient direction to 0/45/90/135 degrees.
      const ang = Math.atan2(gyArr[c], gxArr[c]);
      const deg = ((ang * 180) / Math.PI + 180) % 180;
      let n1, n2;
      if (deg < 22.5 || deg >= 157.5) { n1 = c - 1; n2 = c + 1; }
      else if (deg < 67.5)            { n1 = c - w - 1; n2 = c + w + 1; }
      else if (deg < 112.5)           { n1 = c - w; n2 = c + w; }
      else                            { n1 = c - w + 1; n2 = c + w - 1; }
      const g1 = gmag[n1], g2 = gmag[n2];
      // A NaN neighbour never suppresses (treat as weaker).
      if ((!Number.isFinite(g1) || g >= g1) && (!Number.isFinite(g2) || g >= g2)) {
        crest[c] = 1;
      }
    }
  }

  // Hysteresis + span filter in one pass: flood each 8-connected crest
  // component, note whether it contains a strong (>= thHigh) seed and how
  // far apart its extremes sit. Keep components that are both seeded and
  // long enough to be a break someone can run a boat along.
  const mask = new Uint8Array(w * h);
  const seen = new Uint8Array(w * h);
  const stack = [];
  const fronts = [];
  let breakPx = 0;
  for (let start = 0; start < w * h; start++) {
    if (!crest[start] || seen[start]) continue;
    // Flood this component.
    const px = [];
    let hasSeed = false;
    let minX = w, maxX = 0, minY = h, maxY = 0;
    stack.length = 0;
    stack.push(start);
    seen[start] = 1;
    while (stack.length) {
      const c = stack.pop();
      px.push(c);
      const cx = c % w, cy = (c / w) | 0;
      if (gmag[c] >= thHigh) hasSeed = true;
      if (cx < minX) minX = cx;
      if (cx > maxX) maxX = cx;
      if (cy < minY) minY = cy;
      if (cy > maxY) maxY = cy;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (!dx && !dy) continue;
          const nx = cx + dx, ny = cy + dy;
          if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;
          const n = ny * w + nx;
          if (crest[n] && !seen[n]) { seen[n] = 1; stack.push(n); }
        }
      }
    }
    const spanKm = Math.sqrt(
      ((maxX - minX) * kmX) ** 2 + ((maxY - minY) * kmY) ** 2
    );
    if (hasSeed && spanKm >= minSpanKm) {
      for (const c of px) mask[c] = 1;
      breakPx += px.length;
      fronts.push({
        px: px.length,
        spanKm: Math.round(spanKm),
        points: mainStem(px, w, kmX, kmY),
      });
    }
  }

  return { mask, width: w, height: h, breakPx, oceanPx, fronts };
}

// ---- main-stem extraction -------------------------------------------------
//
// A traced component is a thin pixel chain, occasionally with short spurs
// at junctions. The UI wants ONE clean line per front — so we take the
// component's diameter path (the longest endpoint-to-endpoint walk):
// two BFS passes, the standard tree-diameter trick, which naturally
// ignores spurs. Then Douglas-Peucker to cut the point count before it
// becomes an SVG path.

function mainStem(pxList, w, kmX, kmY) {
  const inComp = new Map(); // cell -> index into pxList
  for (let i = 0; i < pxList.length; i++) inComp.set(pxList[i], i);

  const bfs = (startCell) => {
    const dist = new Map([[startCell, 0]]);
    const parent = new Map();
    const q = [startCell];
    let far = startCell;
    for (let qi = 0; qi < q.length; qi++) {
      const c = q[qi];
      const cx = c % w, cy = (c / w) | 0;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (!dx && !dy) continue;
          const n = (cy + dy) * w + (cx + dx);
          if (!inComp.has(n) || dist.has(n)) continue;
          dist.set(n, dist.get(c) + 1);
          parent.set(n, c);
          if (dist.get(n) > dist.get(far)) far = n;
          q.push(n);
        }
      }
    }
    return { far, parent };
  };

  const a = bfs(pxList[0]).far;      // farthest from an arbitrary start
  const { far: b, parent } = bfs(a); // farthest from THAT = diameter ends
  const chain = [];
  for (let c = b; c !== undefined; c = parent.get(c)) {
    chain.push([c % w, (c / w) | 0]);
    if (c === a) break;
  }

  return simplify(chain, 1.2, kmX, kmY);
}

// Douglas-Peucker in km-space so tolerance means the same thing on
// anisotropic grids. Tolerance in PIXELS of the finer axis.
function simplify(points, tolPx, kmX, kmY) {
  if (points.length <= 2) return points;
  const tol = tolPx * Math.min(kmX, kmY);
  const sq = (v) => v * v;
  const keep = new Uint8Array(points.length);
  keep[0] = keep[points.length - 1] = 1;
  const stack = [[0, points.length - 1]];
  while (stack.length) {
    const [i0, i1] = stack.pop();
    if (i1 - i0 < 2) continue;
    const [x0, y0] = points[i0], [x1, y1] = points[i1];
    const ax = x0 * kmX, ay = y0 * kmY, bx = x1 * kmX, by = y1 * kmY;
    const abLen2 = sq(bx - ax) + sq(by - ay) || 1e-9;
    let maxD = -1, maxI = -1;
    for (let i = i0 + 1; i < i1; i++) {
      const px = points[i][0] * kmX, py = points[i][1] * kmY;
      const t = Math.max(0, Math.min(1,
        ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / abLen2));
      const d = Math.sqrt(sq(px - (ax + t * (bx - ax))) + sq(py - (ay + t * (by - ay))));
      if (d > maxD) { maxD = d; maxI = i; }
    }
    if (maxD > tol) {
      keep[maxI] = 1;
      stack.push([i0, maxI], [maxI, i1]);
    }
  }
  const out = [];
  for (let i = 0; i < points.length; i++) if (keep[i]) out.push(points[i]);
  return out;
}
