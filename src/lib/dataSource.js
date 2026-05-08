// Data source: loads /data/manifest.json + per-window PNGs and exposes
// (lng, lat, window) → value lookups. Returns NaN where the satellite
// didn't capture data — callers must handle "no data" explicitly. We
// intentionally do NOT synthesize fake data; correctness over completeness.
//
// 2026-05-09: the per-layer load logic that used to live inline in
// loadManifest() (a 250-line if/else if chain) was carved out into
// src/lib/loaders/. loadManifest is now a registry dispatch — each
// new layer = one new file under loaders/, no more giant chain.

import { BBOX } from "./mapData.js";
import {
  LAYER_LOADERS,
  decodeUVPng,
  decodeWavePng,
  computeSpeedKt,
  bucketKey,
  hourKey,
} from "./loaders/index.js";

const state = {
  ready: false,
  manifest: null,
  layers: {}, // { sst: { '1d': { data, width, height, dates }, ... }, chl: ... }
};

const subscribers = new Set();

export function subscribe(cb) {
  subscribers.add(cb);
  return () => subscribers.delete(cb);
}

function notify() {
  for (const cb of subscribers) cb();
}

export function getDataState() {
  return state;
}

// Re-export bucketKey / hourKey for the timeline components
// (CurrentTimeline.jsx, WindDayGrid.jsx) that still import them from
// here. Keeps the public API stable across the loader-split refactor.
export { bucketKey, hourKey };

export async function loadManifest() {
  try {
    const res = await fetch("/data/manifest.json", { cache: "no-cache" });
    if (!res.ok) {
      // Expected before the pipeline has run — keep silent, fall back to mock.
      state.ready = true;
      notify();
      return;
    }
    const manifest = await res.json();
    state.manifest = manifest;
    for (const [layer, info] of Object.entries(manifest.layers || {})) {
      // The init line: for every layer EXCEPT sst, zero out any
      // existing slot map. For sst, preserve it so the sst7d/sst5d
      // loaders (which write into state.layers.sst[<slot>]) can
      // accumulate even when sst's own iteration runs first.
      state.layers[layer] = layer === "sst" ? (state.layers.sst || {}) : {};

      const loader = LAYER_LOADERS[layer];
      if (loader) {
        await loader(info, state);
      }
      // Layers absent from LAYER_LOADERS (wave, precip, kd490 today)
      // are silently skipped — they're pipeline inputs the frontend
      // doesn't render. Adding one in the future = drop a new file
      // in src/lib/loaders/ and register it in loaders/index.js.
    }
  } catch (e) {
    console.warn("dataSource: manifest load failed, using mock data", e);
  } finally {
    state.ready = true;
    notify();
  }
}

// PNG row 0 is the top of the image, which corresponds to lat_max
// (fetch.py flips the array vertically before encoding).
function bilinear(layer, lng, lat) {
  if (!layer) return NaN;
  const { data, width, height } = layer;
  const fx = ((lng - BBOX.lngMin) / (BBOX.lngMax - BBOX.lngMin)) * (width - 1);
  const fy = ((BBOX.latMax - lat) / (BBOX.latMax - BBOX.latMin)) * (height - 1);
  if (fx < 0 || fx > width - 1 || fy < 0 || fy > height - 1) return NaN;
  const x0 = Math.floor(fx);
  const x1 = Math.min(x0 + 1, width - 1);
  const y0 = Math.floor(fy);
  const y1 = Math.min(y0 + 1, height - 1);
  const tx = fx - x0;
  const ty = fy - y0;
  const v00 = data[y0 * width + x0];
  const v10 = data[y0 * width + x1];
  const v01 = data[y1 * width + x0];
  const v11 = data[y1 * width + x1];
  // NaN-safe: average whatever corners are valid.
  const vs = [v00, v10, v01, v11];
  let sum = 0,
    n = 0;
  for (const v of vs) if (Number.isFinite(v)) (sum += v), n++;
  if (n > 0) {
    if (n < 4) return sum / n;
    return (
      v00 * (1 - tx) * (1 - ty) +
      v10 * tx * (1 - ty) +
      v01 * (1 - tx) * ty +
      v11 * tx * ty
    );
  }
  // No valid corner — expand outward in concentric shells looking for the
  // nearest finite pixel. Caps at radius 6 (~30–35 km) so we don't snap
  // ridiculously far for a hover. Especially useful for the swell layer
  // where coastal cells still go NaN past the pipeline-side fill cap.
  return findNearestFinite(data, width, height, fx, fy, 6);
}

function findNearestFinite(data, width, height, fx, fy, maxRadius) {
  const cx = Math.round(fx);
  const cy = Math.round(fy);
  for (let r = 1; r <= maxRadius; r++) {
    let bestD2 = Infinity;
    let bestVal = NaN;
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue; // shell only
        const x = cx + dx;
        const y = cy + dy;
        if (x < 0 || x >= width || y < 0 || y >= height) continue;
        const v = data[y * width + x];
        if (!Number.isFinite(v)) continue;
        const d2 = dx * dx + dy * dy;
        if (d2 < bestD2) {
          bestD2 = d2;
          bestVal = v;
        }
      }
    }
    if (Number.isFinite(bestVal)) return bestVal;
  }
  return NaN;
}

const COMPOSITE_KEY = { 1: "1d", 2: "2d", 3: "3d" };
const WIND_SLOT_KEY = { 1: "now", 2: "p6h", 3: "p24h", 4: "p72h" };
const VIZ_SLOT_KEY  = { 1: "now" };

function slotKey(layer, composite) {
  // Strings pass through verbatim (used by wind5d's bucket/hour keys).
  if (typeof composite === "string") return composite;
  if (layer === "wind") return WIND_SLOT_KEY[composite] || "now";
  if (layer === "viz")  return VIZ_SLOT_KEY[composite] || "now";
  return COMPOSITE_KEY[composite] || "1d";
}

// Bucket / hour slot key conventions live in src/lib/loaders/decoders.js.
// They're re-exported at the top of this file (`export { bucketKey,
// hourKey }`) so existing call sites
// (CurrentTimeline.jsx, WindDayGrid.jsx, etc.) keep importing them
// from "./dataSource.js" without churn.

export function getSST(lng, lat, composite = 1) {
  // Returns °C, or NaN if the satellite didn't capture this cell.
  return bilinear(state.layers.sst?.[slotKey("sst", composite)], lng, lat);
}

export function getSstHistorySummary() {
  return state.layers.sst7d?.summary || null;
}

export function getSstHistoryStats(slotKeyStr) {
  const summary = getSstHistorySummary();
  if (!summary) return null;
  return summary.days?.find((d) => d.slot === slotKeyStr) || null;
}

// ---- 3-day trend (Phase A) ---------------------------------------------
//
// The 7-day history pipeline writes one PNG per day into
// state.layers.sst[d-6 .. d0]. These helpers compute trend signals
// directly off those grids — no new fetches, no pipeline changes.

// Number of days back the "trend" view compares. Picked to match the
// time-window of physical ocean processes a free diver cares about
// (a typical upwelling event spins up over 2-3 days). Configurable
// in case we want a "1-day" sub-mode later.
export const SST_TREND_DAYS = 3;

/** Per-cell ΔT (today − N days ago) as a derived grid. Cached on first
 *  call and invalidated whenever a fresh manifest lands (notify()).
 *  Cells where either day is NaN come out NaN — DataOverlay treats
 *  those as transparent, which is exactly what we want for stale tiles.
 */
let _trendGridCache = null;
subscribers.add(() => { _trendGridCache = null; });

export function getSstTrendGrid(daysBack = SST_TREND_DAYS) {
  if (_trendGridCache && _trendGridCache.daysBack === daysBack) {
    return _trendGridCache.grid;
  }
  const today = state.layers.sst?.["d0"];
  const then  = state.layers.sst?.[`d-${daysBack}`];
  if (!today || !then) return null;
  if (today.width !== then.width || today.height !== then.height) return null;
  const N = today.data.length;
  const out = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const a = today.data[i];
    const b = then.data[i];
    out[i] = (Number.isFinite(a) && Number.isFinite(b)) ? (a - b) : NaN;
  }
  const grid = { data: out, width: today.width, height: today.height };
  _trendGridCache = { daysBack, grid };
  return grid;
}

/** Per-spot trend at (lng, lat). Returns °C numbers (caller converts).
 *  `now`  = today's value
 *  `then` = N days ago value
 *  `deltaC` = now - then
 *  All three may be NaN when the satellite missed that cell.
 */
export function getSstTrend(lng, lat, daysBack = SST_TREND_DAYS) {
  const now  = bilinear(state.layers.sst?.["d0"],  lng, lat);
  const then = bilinear(state.layers.sst?.[`d-${daysBack}`], lng, lat);
  const deltaC = (Number.isFinite(now) && Number.isFinite(then)) ? now - then : NaN;
  return { now, then, deltaC };
}

/** Per-spot 7-day sparkline values at (lng, lat). Returns an array of
 *  °C samples in order [d-6, d-5, ..., d-1, d0]. NaN slots ride along
 *  for the renderer to skip / dim. */
export function getSstSparkline(lng, lat) {
  const summary = getSstHistorySummary();
  if (!summary?.days?.length) return null;
  // Use the summary's `days` array order (already chronological) so
  // we don't re-derive the slot list locally.
  return summary.days.map((d) => bilinear(state.layers.sst?.[d.slot], lng, lat));
}


// ---- 5-day forecast (Phase E) ------------------------------------------
//
// Mirror of the sst7d helpers. Each day's forecast lands in
// state.layers.sst5d.f{0..6} (slot key namespace distinct from the
// d-N history keys). Summary stats live in state.layers.sst5d.summary.

/** Live forecast summary or null if not loaded. */
export function getSstForecastSummary() {
  return state.layers.sst5d?.summary || null;
}

export function getSstForecastStats(slotKeyStr) {
  const summary = getSstForecastSummary();
  if (!summary) return null;
  return summary.days?.find((d) => d.slot === slotKeyStr) || null;
}

// getSstForecast / getSstForecastSparkline removed: they were left
// over from my Phase E loader (which wrote forecasts into a separate
// state.layers.sst5d.f{offset} namespace). The shipped loader writes
// into state.layers.sst[d.slot] alongside history, so any future
// per-spot forecast accessor should bilinear off there. No callers
// exist today — saves shipping dead code that returns NaN forever.

export function getChl(lng, lat, composite = 1) {
  // Returns mg/m³, or NaN if the satellite didn't capture this cell.
  return bilinear(state.layers.chl?.[slotKey("chl", composite)], lng, lat);
}

export function getWindSpeed(lng, lat, composite = 1) {
  // String composite = wind5d slot key (e.g. "d2_morning" or "d2_h13").
  if (typeof composite === "string") return getWind5dSpeed(lng, lat, composite);
  // Legacy integer path (4-slot now/p6h/p24h/p72h).
  return bilinear(state.layers.wind?.[slotKey("wind", composite)], lng, lat);
}

export function getVizFt(lng, lat, composite = 1) {
  // Returns predicted Secchi visibility in feet (NaN if not loaded).
  return bilinear(state.layers.viz?.[slotKey("viz", composite)], lng, lat);
}

// Returns the loaded scalar grid for a (layer, composite) — the same Float32Array
// that bilinear() reads from. Lets DataOverlay render at native grid resolution
// (one canvas pixel per source cell) and let the browser scale up smoothly.
export function getLayerGrid(layer, composite) {
  // Synthetic "sst-trend" pseudo-layer: derived from sst d0 vs d-N grids,
  // rendered with the diverging ΔT colormap. Composite carries the days-back
  // (integer); falls back to the SST_TREND_DAYS default when it's not numeric.
  if (layer === "sst-trend") {
    const days = Number.isInteger(composite) ? composite : SST_TREND_DAYS;
    return getSstTrendGrid(days);
  }
  // 5-day SST forecast layer. composite is the lead-day integer (0..6)
  // OR a string "f3"-style slot key. Reuses the SST color ramp via
  // DataOverlay's "sst" branch — so we present the forecast as a
  // separate `layer="sst5d"` value the panel can switch into without
  // re-using the sst-trend palette.
  if (layer === "sst5d") {
    const slot = typeof composite === "string"
      ? composite
      : `f${Number.isInteger(composite) ? composite : 0}`;
    const w = state.layers.sst5d?.[slot];
    if (!w) return null;
    return { data: w.data, width: w.width, height: w.height };
  }
  // Wind / Swell: a string composite is a 5-day slot key (e.g. "d2_morning").
  if (layer === "wind" && typeof composite === "string") {
    const w = wind5dEntry(composite);
    return w ? { data: w.data, width: w.width, height: w.height } : null;
  }
  if (layer === "wind5d") {
    const w = wind5dEntry(composite);
    return w ? { data: w.data, width: w.width, height: w.height } : null;
  }
  if (layer === "swell" || layer === "swell5d") {
    const w = swell5dEntry(typeof composite === "string" ? composite : "d0_midday");
    // .data is Hs in metres — DataOverlay's swell ramp expects m.
    return w ? { data: w.data, width: w.width, height: w.height } : null;
  }
  if (layer === "current" || layer === "current5d") {
    const w = current5dEntry(typeof composite === "string" ? composite : "d0_midday");
    return w ? { data: w.data, width: w.width, height: w.height } : null;
  }
  const w = state.layers[layer]?.[slotKey(layer, composite)];
  if (!w) return null;
  return { data: w.data, width: w.width, height: w.height };
}

// ---- wind5d accessors -------------------------------------------------------

function wind5dEntry(slotKeyStr) {
  const w5 = state.layers.wind5d;
  if (!w5) return null;
  // Bucket keys look like "d2_morning", hourly look like "d2_h13".
  if (/_h\d{2}$/.test(slotKeyStr)) return w5.hourly[slotKeyStr] || null;
  return w5.buckets[slotKeyStr] || null;
}

export function getWind5dSummary() {
  return state.layers.wind5d?.summary || null;
}

export function getWind5dSpeed(lng, lat, slotKeyStr) {
  return bilinear(wind5dEntry(slotKeyStr), lng, lat);
}

export function getWind5dUV(lng, lat, slotKeyStr) {
  const w = wind5dEntry(slotKeyStr);
  if (!w) return { u: NaN, v: NaN };
  const fx = ((lng - BBOX.lngMin) / (BBOX.lngMax - BBOX.lngMin)) * (w.width - 1);
  const fy = ((BBOX.latMax - lat) / (BBOX.latMax - BBOX.latMin)) * (w.height - 1);
  if (fx < 0 || fx > w.width - 1 || fy < 0 || fy > w.height - 1) {
    return { u: NaN, v: NaN };
  }
  const x0 = Math.floor(fx), x1 = Math.min(x0 + 1, w.width - 1);
  const y0 = Math.floor(fy), y1 = Math.min(y0 + 1, w.height - 1);
  const tx = fx - x0, ty = fy - y0;
  const lookup = (arr) => {
    const v00 = arr[y0 * w.width + x0];
    const v10 = arr[y0 * w.width + x1];
    const v01 = arr[y1 * w.width + x0];
    const v11 = arr[y1 * w.width + x1];
    let sum = 0, n = 0;
    if (Number.isFinite(v00)) { sum += v00; n++; }
    if (Number.isFinite(v10)) { sum += v10; n++; }
    if (Number.isFinite(v01)) { sum += v01; n++; }
    if (Number.isFinite(v11)) { sum += v11; n++; }
    if (n === 0) return NaN;
    if (n < 4) return sum / n;
    return v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty)
         + v01 * (1 - tx) * ty       + v11 * tx * ty;
  };
  return { u: lookup(w.uvU), v: lookup(w.uvV) };
}

// ---- current5d accessors ----------------------------------------------------

function current5dEntry(slotKeyStr) {
  const c5 = state.layers.current5d;
  if (!c5) return null;
  return c5.buckets[slotKeyStr] || null;
}

export function getCurrent5dSummary() {
  return state.layers.current5d?.summary || null;
}

export function getCurrentSpeed(lng, lat, slotKeyStr) {
  const entry = current5dEntry(slotKeyStr);
  if (!entry) return NaN;
  return bilinear(
    {
      data: entry.sampleData || entry.data,
      width: entry.width,
      height: entry.height,
    },
    lng,
    lat
  );
}

export function getCurrentUV(lng, lat, slotKeyStr) {
  const w = current5dEntry(slotKeyStr);
  if (!w) return { u: NaN, v: NaN };
  const fx = ((lng - BBOX.lngMin) / (BBOX.lngMax - BBOX.lngMin)) * (w.width - 1);
  const fy = ((BBOX.latMax - lat) / (BBOX.latMax - BBOX.latMin)) * (w.height - 1);
  if (fx < 0 || fx > w.width - 1 || fy < 0 || fy > w.height - 1) {
    return { u: NaN, v: NaN };
  }
  const x0 = Math.floor(fx), x1 = Math.min(x0 + 1, w.width - 1);
  const y0 = Math.floor(fy), y1 = Math.min(y0 + 1, w.height - 1);
  const tx = fx - x0, ty = fy - y0;
  const lookup = (arr) => {
    const v00 = arr[y0 * w.width + x0];
    const v10 = arr[y0 * w.width + x1];
    const v01 = arr[y1 * w.width + x0];
    const v11 = arr[y1 * w.width + x1];
    let sum = 0, n = 0;
    if (Number.isFinite(v00)) { sum += v00; n++; }
    if (Number.isFinite(v10)) { sum += v10; n++; }
    if (Number.isFinite(v01)) { sum += v01; n++; }
    if (Number.isFinite(v11)) { sum += v11; n++; }
    if (n === 0) return NaN;
    if (n < 4) return sum / n;
    return v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty)
         + v01 * (1 - tx) * ty       + v11 * tx * ty;
  };
  return { u: lookup(w.uvU), v: lookup(w.uvV) };
}

// ---- swell5d helpers -------------------------------------------------------

function swell5dEntry(slotKeyStr) {
  const s5 = state.layers.swell5d;
  if (!s5) return null;
  if (/_h\d{2}$/.test(slotKeyStr)) return s5.hourly[slotKeyStr] || null;
  return s5.buckets[slotKeyStr] || null;
}

export function getSwell5dSummary() {
  return state.layers.swell5d?.summary || null;
}

// (Hs in metres, Tp in seconds, Dp in degrees) at a lng/lat. Tries the
// 4-corner bilinear first and, if every corner is NaN (cursor is on land
// or in a cell the pipeline fill couldn't reach), expands outward in
// concentric shells looking for the nearest valid cell — and returns the
// FULL Hs/Tp/Dp triplet from that same cell so the three numbers stay
// consistent.
export function getSwell5dStats(lng, lat, slotKeyStr) {
  const w = swell5dEntry(slotKeyStr);
  if (!w) return { hs: NaN, tp: NaN, dp: NaN };
  const fx = ((lng - BBOX.lngMin) / (BBOX.lngMax - BBOX.lngMin)) * (w.width - 1);
  const fy = ((BBOX.latMax - lat) / (BBOX.latMax - BBOX.latMin)) * (w.height - 1);
  if (fx < 0 || fx > w.width - 1 || fy < 0 || fy > w.height - 1) {
    return { hs: NaN, tp: NaN, dp: NaN };
  }
  const x0 = Math.floor(fx), x1 = Math.min(x0 + 1, w.width - 1);
  const y0 = Math.floor(fy), y1 = Math.min(y0 + 1, w.height - 1);
  const tx = fx - x0, ty = fy - y0;
  const sample = (arr) => {
    const v00 = arr[y0 * w.width + x0];
    const v10 = arr[y0 * w.width + x1];
    const v01 = arr[y1 * w.width + x0];
    const v11 = arr[y1 * w.width + x1];
    let sum = 0, n = 0;
    if (Number.isFinite(v00)) { sum += v00; n++; }
    if (Number.isFinite(v10)) { sum += v10; n++; }
    if (Number.isFinite(v01)) { sum += v01; n++; }
    if (Number.isFinite(v11)) { sum += v11; n++; }
    if (n === 0) return NaN;
    if (n < 4) return sum / n;
    return v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty)
         + v01 * (1 - tx) * ty       + v11 * tx * ty;
  };
  let hs = sample(w.hs);
  let tp = sample(w.tp);
  let dp = sample(w.dp);
  if (!Number.isFinite(hs)) {
    // 4-corner search came up empty — find the closest finite Hs cell
    // and read the matching Tp/Dp from THE SAME cell so all three values
    // describe a single neighbour, not an average across mismatched ones.
    const cell = findNearestFiniteCell(w.hs, w.width, w.height, fx, fy, 6);
    if (cell) {
      const idx = cell.y * w.width + cell.x;
      hs = w.hs[idx];
      tp = w.tp[idx];
      dp = w.dp[idx];
    }
  }
  return { hs, tp, dp };
}

// Like findNearestFinite() but returns the (x, y) of the cell instead of
// just its value, so the caller can look up paired channels at the same spot.
function findNearestFiniteCell(data, width, height, fx, fy, maxRadius) {
  const cx = Math.round(fx);
  const cy = Math.round(fy);
  for (let r = 1; r <= maxRadius; r++) {
    let bestD2 = Infinity;
    let best = null;
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        const x = cx + dx;
        const y = cy + dy;
        if (x < 0 || x >= width || y < 0 || y >= height) continue;
        if (!Number.isFinite(data[y * width + x])) continue;
        const d2 = dx * dx + dy * dy;
        if (d2 < bestD2) { bestD2 = d2; best = { x, y }; }
      }
    }
    if (best) return best;
  }
  return null;
}

// bbox-aggregate stats for a single hour, computed from the loaded grid.
export function getSwell5dHourlyStats(day, hour) {
  const grid = state.layers.swell5d?.hourly?.[hourKey(day, hour)];
  if (!grid) return null;
  let sumHs = 0, sumTp = 0, sumSinDp = 0, sumCosDp = 0, n = 0;
  for (let i = 0; i < grid.hs.length; i++) {
    const h = grid.hs[i], t = grid.tp[i], d = grid.dp[i];
    if (Number.isFinite(h) && Number.isFinite(t) && Number.isFinite(d)) {
      sumHs += h; sumTp += t;
      const r = (d * Math.PI) / 180;
      sumSinDp += Math.sin(r);
      sumCosDp += Math.cos(r);
      n++;
    }
  }
  if (n === 0) return null;
  const meanHs = sumHs / n;
  const meanTp = sumTp / n;
  const dpRad = Math.atan2(sumSinDp / n, sumCosDp / n);
  const meanDp = (((dpRad * 180) / Math.PI) + 360) % 360;
  return { hs: meanHs, tp: meanTp, dp: meanDp };
}

// On-demand fetch of one day's 24 hourly wave PNGs. Idempotent + dedupes.
export async function loadSwell5dHourly(day) {
  const s5 = state.layers.swell5d;
  if (!s5) return;
  const flag = `d${day}`;
  if (s5.hourlyLoading[flag]) return s5.hourlyLoading[flag];

  const dayInfo = s5.summary?.days?.find((d) => d.day === day);
  if (!dayInfo) return;
  const tmpl = dayInfo.hourly_url_template;
  const hours = Array.from({ length: 24 }, (_, h) => h);

  s5.hourlyLoading[flag] = (async () => {
    const tasks = hours.map(async (h) => {
      const url = tmpl.replace("{HH}", String(h).padStart(2, "0"));
      try {
        const wv = await decodeWavePng(url, s5.heightRange, s5.periodRange);
        s5.hourly[hourKey(day, h)] = wv;
      } catch {
        // missing hour — silently skip
      }
    });
    await Promise.all(tasks);
    notify();
  })();
  return s5.hourlyLoading[flag];
}

export function hasSwell5dHourly(day) {
  const s5 = state.layers.swell5d;
  if (!s5) return false;
  for (let h = 0; h < 24; h++) {
    if (s5.hourly[hourKey(day, h)]) return true;
  }
  return false;
}

// On-demand fetch of a single day's 24 hourly UV PNGs. Idempotent — returns
// the in-flight promise if a previous call is still resolving.
export async function loadWind5dHourly(day) {
  const w5 = state.layers.wind5d;
  if (!w5) return;
  const flag = `d${day}`;
  if (w5.hourlyLoading[flag]) return w5.hourlyLoading[flag];

  const summary = w5.summary;
  const dayInfo = summary?.days?.find(d => d.day === day);
  if (!dayInfo) return;
  const tmpl = dayInfo.hourly_url_template; // /data/wind/hourly/d0_h{HH}_uv.png
  const hours = Array.from({ length: 24 }, (_, h) => h);

  w5.hourlyLoading[flag] = (async () => {
    const tasks = hours.map(async (h) => {
      const url = tmpl.replace("{HH}", String(h).padStart(2, "0"));
      try {
        const uv = await decodeUVPng(url, w5.uvRange);
        const speed = computeSpeedKt(uv);
        w5.hourly[hourKey(day, h)] = {
          uvU: uv.u, uvV: uv.v,
          width: uv.width, height: uv.height,
          data: speed, speedKt: speed,
        };
      } catch {
        // Hour file missing (overnight gap, NOMADS skipped, etc.) — leave
        // the slot empty; the UI handles missing entries gracefully.
      }
    });
    await Promise.all(tasks);
    notify();
  })();
  return w5.hourlyLoading[flag];
}

export function hasWind5dHourly(day) {
  const w5 = state.layers.wind5d;
  if (!w5) return false;
  for (let h = 0; h < 24; h++) {
    if (w5.hourly[hourKey(day, h)]) return true;
  }
  return false;
}

// bbox-aggregate stats for one hour, computed off the loaded UV grid.
// Returns null if the grid hasn't been fetched yet (caller falls back to
// bucket means or a placeholder). kt = scalar mean of per-pixel |v| (kt);
// dir = "from" compass bearing of the vector mean wind.
export function getWind5dHourlyStats(day, hour) {
  const grid = state.layers.wind5d?.hourly?.[hourKey(day, hour)];
  if (!grid) return null;
  const speeds = grid.data;
  const us = grid.uvU;
  const vs = grid.uvV;
  let sumKt = 0;
  let sumU = 0, sumV = 0;
  let n = 0;
  for (let i = 0; i < speeds.length; i++) {
    const s = speeds[i];
    const u = us[i];
    const v = vs[i];
    if (Number.isFinite(s) && Number.isFinite(u) && Number.isFinite(v)) {
      sumKt += s;
      sumU += u;
      sumV += v;
      n++;
    }
  }
  if (n === 0) return null;
  const meanU = sumU / n;
  const meanV = sumV / n;
  const dirDeg = ((Math.atan2(-meanU, -meanV) * 180) / Math.PI + 360) % 360;
  return { kt: sumKt / n, dir: dirDeg };
}

// Source name for the active wind slot ("HRRR" / "GFS" / "HRRR+GFS"), or null
// when the slot hasn't been loaded yet.
export function windSource(composite) {
  if (typeof composite === "string") {
    // wind5d slot — look up the bucket's `sources` array in summary.
    const summary = state.layers.wind5d?.summary;
    if (!summary) return null;
    const m = composite.match(/^d(\d+)_(?!h\d)([a-z]+)$/);
    if (m) {
      const day = +m[1];
      const bucket = m[2];
      const dayInfo = summary.days?.find((d) => d.day === day);
      const bk = dayInfo?.buckets?.find((b) => b.bucket === bucket);
      if (bk?.sources?.length) return bk.sources.map((s) => s.toUpperCase()).join("+");
    }
    // Hourly slots don't carry a per-source tag yet — fall back to a
    // confidence label from the day shell.
    const mh = composite.match(/^d(\d+)_h\d{2}$/);
    if (mh) {
      const day = +mh[1];
      const dayInfo = summary.days?.find((d) => d.day === day);
      return dayInfo?.confidence ? dayInfo.confidence.toUpperCase() : null;
    }
    return null;
  }
  return state.layers.wind?.[slotKey("wind", composite)]?.source ?? null;
}

export function currentSource(composite) {
  const summary = state.layers.current5d?.summary;
  if (!summary || typeof composite !== "string") return null;
  const m = composite.match(/^d(\d+)_(?!h\d)([a-z]+)$/);
  if (!m) return null;
  const day = +m[1];
  const bucket = m[2];
  const dayInfo = summary.days?.find((d) => d.day === day);
  const bk = dayInfo?.buckets?.find((b) => b.bucket === bucket);
  if (!bk?.source) return null;
  if (bk.source === "hfr_observed") return "HFR observed";
  if (bk.source === "hfr_persistence_tide_wind") return "HFR + tide/wind";
  if (bk.source === "inferred_tide_wind") return "tide/wind inferred";
  return bk.source;
}

// Bilinear lookup against U or V grid (NaN-safe).
function bilinearComponent(grid, lng, lat) {
  if (!grid) return NaN;
  const { uvU, uvV, width, height } = grid;
  // unused param intentionally; this just probes shape.
  return NaN;
}

export function getWindUV(lng, lat, composite = 1) {
  if (typeof composite === "string") return getWind5dUV(lng, lat, composite);
  const w = state.layers.wind?.[slotKey("wind", composite)];
  if (!w) return { u: NaN, v: NaN };
  const fx = ((lng - BBOX.lngMin) / (BBOX.lngMax - BBOX.lngMin)) * (w.width - 1);
  const fy = ((BBOX.latMax - lat) / (BBOX.latMax - BBOX.latMin)) * (w.height - 1);
  if (fx < 0 || fx > w.width - 1 || fy < 0 || fy > w.height - 1) {
    return { u: NaN, v: NaN };
  }
  const x0 = Math.floor(fx), x1 = Math.min(x0 + 1, w.width - 1);
  const y0 = Math.floor(fy), y1 = Math.min(y0 + 1, w.height - 1);
  const tx = fx - x0, ty = fy - y0;
  const lookup = (arr) => {
    const v00 = arr[y0 * w.width + x0];
    const v10 = arr[y0 * w.width + x1];
    const v01 = arr[y1 * w.width + x0];
    const v11 = arr[y1 * w.width + x1];
    let sum = 0, n = 0;
    if (Number.isFinite(v00)) { sum += v00; n++; }
    if (Number.isFinite(v10)) { sum += v10; n++; }
    if (Number.isFinite(v01)) { sum += v01; n++; }
    if (Number.isFinite(v11)) { sum += v11; n++; }
    if (n === 0) return NaN;
    if (n < 4) return sum / n;
    return v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty) + v01 * (1 - tx) * ty + v11 * tx * ty;
  };
  return { u: lookup(w.uvU), v: lookup(w.uvV) };
}

// Compass degrees ("from" direction), meteorological convention.
export function windCompass(u, v) {
  return ((Math.atan2(-u, -v) * 180) / Math.PI + 360) % 360;
}

const CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
export function windCardinal(deg) {
  return CARDINALS[Math.round(deg / 22.5) % 16];
}

// Returns the YYYY-MM-DD date list for the active window (sst/chl), or
// the wind slot's valid_at ISO string. Null when the layer/window is missing.
export function dataDates(layer, composite) {
  if (layer === "wind" && typeof composite === "string") {
    const summary = state.layers.wind5d?.summary;
    if (!summary) return null;
    const dm = composite.match(/^d(\d+)_/);
    const day = dm ? +dm[1] : 0;
    const dayInfo = summary.days?.find((d) => d.day === day);
    return dayInfo ? [`${dayInfo.date}`] : null;
  }
  if (layer === "swell" && typeof composite === "string") {
    return dataDatesForSwell(composite);
  }
  if (layer === "current" && typeof composite === "string") {
    const summary = state.layers.current5d?.summary;
    if (!summary) return null;
    const dm = composite.match(/^d(\d+)_/);
    const day = dm ? +dm[1] : 0;
    const dayInfo = summary.days?.find((d) => d.day === day);
    return dayInfo ? [dayInfo.date] : null;
  }
  const key = slotKey(layer, composite);
  const w = state.layers[layer]?.[key];
  if (!w) return null;
  if (layer === "wind") return [w.valid_at];
  return w.dates || null;
}

export function isReal(layer, composite) {
  if (layer === "wind" && typeof composite === "string") {
    return Boolean(wind5dEntry(composite));
  }
  if (layer === "swell" && typeof composite === "string") {
    return Boolean(swell5dEntry(composite));
  }
  if (layer === "current" && typeof composite === "string") {
    return Boolean(current5dEntry(composite));
  }
  return Boolean(state.layers[layer]?.[slotKey(layer, composite)]);
}

export function dataDatesForSwell(slotKeyStr) {
  const summary = state.layers.swell5d?.summary;
  if (!summary) return null;
  const dm = slotKeyStr.match(/^d(\d+)_/);
  const day = dm ? +dm[1] : 0;
  const dayInfo = summary.days?.find((d) => d.day === day);
  return dayInfo ? [dayInfo.date] : null;
}
