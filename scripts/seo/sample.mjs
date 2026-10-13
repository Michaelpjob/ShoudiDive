// Build-time point sampling of the published data rasters.
//
// Mirrors the browser path exactly so a number on a static spot page is the
// number the map shows for the same spot:
//   decode math        src/lib/loaders/decoders.js (decodePng/Wave/UV)
//   observed-only gate src/lib/loaders/scalarPng.js (chl) + viz.js (quality)
//   sampling           src/lib/dataSource.js bilinear()/getSwell5dStats()
// If you change the decode or the honesty gates there, change them here too;
// tests/seoPages.test.js pins the shared constants.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { decodePngFile } from "./png.mjs";

// scalarPng.js: chl cells older than 3 days or owned by a gap-fill source are
// blanked. viz.js: quality code 0 or >= 3 (estimate cells) are blanked.
export const CHL_FRESH_DAYS = 3;
export const GAP_FILL_SOURCE_CODES = new Set([4, 5, 6]);

export function makeRegionReader(dataDir, region) {
  const prefix = region === "ca" ? "" : `${region}/`;
  const resolve = (url) => join(dataDir, prefix, url.replace(/^\/data\//, ""));
  const readJson = (rel) => {
    const p = join(dataDir, prefix, rel);
    return existsSync(p) ? JSON.parse(readFileSync(p, "utf8")) : null;
  };
  const cache = new Map();
  const png = (url) => {
    if (!url) return null;
    if (cache.has(url)) return cache.get(url);
    const p = resolve(url);
    const img = existsSync(p) ? decodePngFile(p) : null;
    cache.set(url, img);
    return img;
  };
  return { resolve, readJson, png };
}

function scalarGrid(img, scale, [lo, hi]) {
  const out = new Float32Array(img.width * img.height);
  const llo = Math.log10(lo), lhi = Math.log10(hi);
  for (let i = 0; i < out.length; i++) {
    const px = img.data[i * 4];
    if (px === 0) out[i] = NaN;
    else if (scale === "log10") out[i] = Math.pow(10, llo + ((px - 1) / 254) * (lhi - llo));
    else out[i] = lo + ((px - 1) / 254) * (hi - lo);
  }
  return { data: out, width: img.width, height: img.height };
}

function rawCodes(img) {
  const codes = new Uint8Array(img.width * img.height);
  for (let i = 0; i < codes.length; i++) codes[i] = img.data[i * 4];
  return codes;
}

function uvGrid(img, [lo, hi]) {
  const n = img.width * img.height;
  const u = new Float32Array(n), v = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    if (img.data[i * 4 + 3] === 0) { u[i] = NaN; v[i] = NaN; continue; }
    u[i] = lo + (img.data[i * 4] / 255) * (hi - lo);
    v[i] = lo + (img.data[i * 4 + 1] / 255) * (hi - lo);
  }
  return { u, v, width: img.width, height: img.height };
}

function waveGrid(img, [hLo, hHi], [pLo, pHi]) {
  const n = img.width * img.height;
  const hs = new Float32Array(n), tp = new Float32Array(n), dp = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    if (img.data[i * 4 + 3] === 0) { hs[i] = tp[i] = dp[i] = NaN; continue; }
    hs[i] = hLo + (img.data[i * 4] / 255) * (hHi - hLo);
    tp[i] = pLo + (img.data[i * 4 + 1] / 255) * (pHi - pLo);
    dp[i] = (img.data[i * 4 + 2] / 255) * 360;
  }
  return { hs, tp, dp, width: img.width, height: img.height };
}

function frac(bbox, width, height, lng, lat) {
  const [lngMin, latMin, lngMax, latMax] = bbox;
  const fx = ((lng - lngMin) / (lngMax - lngMin)) * (width - 1);
  const fy = ((latMax - lat) / (latMax - latMin)) * (height - 1);
  if (fx < 0 || fx > width - 1 || fy < 0 || fy > height - 1) return null;
  return { fx, fy };
}

function bilinearArr(arr, width, f) {
  const x0 = Math.floor(f.fx), y0 = Math.floor(f.fy);
  const x1 = Math.min(x0 + 1, width - 1);
  const height = arr.length / width;
  const y1 = Math.min(y0 + 1, height - 1);
  const tx = f.fx - x0, ty = f.fy - y0;
  const v00 = arr[y0 * width + x0], v10 = arr[y0 * width + x1];
  const v01 = arr[y1 * width + x0], v11 = arr[y1 * width + x1];
  let sum = 0, n = 0;
  for (const v of [v00, v10, v01, v11]) if (Number.isFinite(v)) { sum += v; n++; }
  if (n === 0) return NaN;
  if (n < 4) return sum / n;
  return v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty) + v01 * (1 - tx) * ty + v11 * tx * ty;
}

function nearestCell(arr, width, height, f, maxRadius) {
  const cx = Math.round(f.fx), cy = Math.round(f.fy);
  for (let r = 1; r <= maxRadius; r++) {
    let best = null, bestD2 = Infinity;
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        const x = cx + dx, y = cy + dy;
        if (x < 0 || x >= width || y < 0 || y >= height) continue;
        if (!Number.isFinite(arr[y * width + x])) continue;
        const d2 = dx * dx + dy * dy;
        if (d2 < bestD2) { bestD2 = d2; best = y * width + x; }
      }
    }
    if (best != null) return best;
  }
  return null;
}

// dataSource.js bilinear(): 4-corner NaN-safe, then optional nearest-finite
// shell search. maxReach=0 for observed-only layers (chl, viz).
function sampleScalar(grid, bbox, lng, lat, maxReach = 6) {
  if (!grid) return NaN;
  const f = frac(bbox, grid.width, grid.height, lng, lat);
  if (!f) return NaN;
  const v = bilinearArr(grid.data, grid.width, f);
  if (Number.isFinite(v) || maxReach <= 0) return v;
  const idx = nearestCell(grid.data, grid.width, grid.height, f, maxReach);
  return idx == null ? NaN : grid.data[idx];
}

function sampleUV(grid, bbox, lng, lat) {
  if (!grid) return { u: NaN, v: NaN };
  const f = frac(bbox, grid.width, grid.height, lng, lat);
  if (!f) return { u: NaN, v: NaN };
  return { u: bilinearArr(grid.u, grid.width, f), v: bilinearArr(grid.v, grid.width, f) };
}

// dataSource.js getSwell5dStats(): bilinear, else the nearest finite Hs cell
// within 6 with Tp/Dp read from that SAME cell.
function sampleWave(grid, bbox, lng, lat) {
  const none = { hs: NaN, tp: NaN, dp: NaN };
  if (!grid) return none;
  const f = frac(bbox, grid.width, grid.height, lng, lat);
  if (!f) return none;
  let hs = bilinearArr(grid.hs, grid.width, f);
  let tp = bilinearArr(grid.tp, grid.width, f);
  let dp = bilinearArr(grid.dp, grid.width, f);
  if (!Number.isFinite(hs)) {
    const idx = nearestCell(grid.hs, grid.width, grid.height, f, 6);
    if (idx != null) { hs = grid.hs[idx]; tp = grid.tp[idx]; dp = grid.dp[idx]; }
  }
  return { hs, tp, dp };
}

const MS_TO_KT = 1.94384;

// Loads one region's published data and returns point samplers over it.
export function loadRegion(dataDir, region) {
  const r = makeRegionReader(dataDir, region);
  const manifest = r.readJson("manifest.json");
  if (!manifest) return null;
  const bbox = manifest.bbox;
  const L = manifest.layers || {};

  const out = { region, manifest, bbox, generatedAt: manifest.generated_at };

  // Sea surface temperature: latest single-day window + the 7-day history
  // (for a 3-day change, same as getSstTrend()).
  if (L.sst?.windows?.["1d"]) {
    const w = L.sst.windows["1d"];
    const img = r.png(w.url);
    const grid = img && scalarGrid(img, L.sst.scale || "linear", L.sst.range);
    out.sst = {
      source: L.sst.source || null,
      date: (w.dates || []).slice(-1)[0] || null,
      at: (lng, lat) => sampleScalar(grid, bbox, lng, lat),
    };
    const hist = r.readJson("sst/summary.json");
    if (hist?.days?.length) {
      const grids = {};
      for (const d of hist.days) {
        const himg = r.png(d.url);
        if (himg) grids[d.slot] = scalarGrid(himg, hist.scale || "linear", hist.range);
      }
      out.sst.trend = (lng, lat, daysBack = 3) => {
        const now = sampleScalar(grids.d0, bbox, lng, lat);
        const then = sampleScalar(grids[`d-${daysBack}`], bbox, lng, lat);
        return Number.isFinite(now) && Number.isFinite(then) ? now - then : NaN;
      };
    }
  }

  // Chlorophyll, observed-only: blank gap-fill sources and stale cells.
  if (L.chl?.windows?.["1d"]) {
    const w = L.chl.windows["1d"];
    const img = r.png(w.url);
    if (img) {
      const grid = scalarGrid(img, L.chl.scale || "log10", L.chl.range);
      const src = w.source_url && r.png(w.source_url);
      const age = w.age_days_url && r.png(w.age_days_url);
      const srcCodes = src && src.width === img.width ? rawCodes(src) : null;
      const ageCodes = age && age.width === img.width ? rawCodes(age) : null;
      for (let i = 0; i < grid.data.length; i++) {
        if (!Number.isFinite(grid.data[i])) continue;
        if (srcCodes && GAP_FILL_SOURCE_CODES.has(srcCodes[i])) { grid.data[i] = NaN; continue; }
        if (ageCodes && (ageCodes[i] === 0 || ageCodes[i] - 1 > CHL_FRESH_DAYS)) grid.data[i] = NaN;
      }
      out.chl = { at: (lng, lat) => sampleScalar(grid, bbox, lng, lat, 0) };
    }
  }

  // Modeled visibility (Secchi-style, ft). Observed-only gate from viz.js.
  if (L.viz?.windows?.now) {
    const w = L.viz.windows.now;
    const img = r.png(w.url);
    if (img) {
      const grid = scalarGrid(img, "linear", L.viz.range_ft);
      const q = w.quality_url && r.png(w.quality_url);
      if (q && q.width === img.width && q.height === img.height) {
        const codes = rawCodes(q);
        for (let i = 0; i < codes.length; i++) if (codes[i] === 0 || codes[i] >= 3) grid.data[i] = NaN;
      }
      out.viz = { validAt: w.valid_at || null, at: (lng, lat) => sampleScalar(grid, bbox, lng, lat, 0) };
    }
  }

  // Swell, wind, and current forecasts by day and time-of-day bucket.
  const swell = L.swell5d && r.readJson("swell/summary.json");
  if (swell?.days) {
    out.swell = {
      tz: swell.tz, cycle: swell.gfswave_cycle || null,
      days: swell.days.map((d) => ({
        day: d.day, date: d.date, weekday: d.weekday,
        buckets: Object.fromEntries((d.buckets || []).map((b) => {
          const img = r.png(b.wave_url);
          const grid = img && waveGrid(img, L.swell5d.height_range_m, L.swell5d.period_range_s);
          return [b.bucket, (lng, lat) => sampleWave(grid, bbox, lng, lat)];
        })),
      })),
    };
  }
  const wind = L.wind5d && r.readJson("wind/summary.json");
  if (wind?.days) {
    out.wind = {
      tz: wind.tz, source: L.wind?.source || null,
      days: wind.days.map((d) => ({
        day: d.day, date: d.date, weekday: d.weekday,
        buckets: Object.fromEntries((d.buckets || []).map((b) => {
          const img = r.png(b.uv_url);
          const grid = img && uvGrid(img, L.wind5d.uv_range);
          return [b.bucket, (lng, lat) => {
            const { u, v } = sampleUV(grid, bbox, lng, lat);
            if (!Number.isFinite(u) || !Number.isFinite(v)) return null;
            // windCompass(): direction the wind blows FROM.
            const from = (Math.atan2(-u, -v) * 180 / Math.PI + 360) % 360;
            return { kt: Math.hypot(u, v) * MS_TO_KT, fromDeg: from };
          }];
        })),
      })),
    };
  }

  out.tides = r.readJson("tides.json");
  out.vizSkill = r.readJson("viz_skill.json");
  return out;
}

const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
export function compass(deg) {
  if (!Number.isFinite(deg)) return "";
  return COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
}
