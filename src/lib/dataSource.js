// Data source: loads /data/manifest.json + per-window PNGs and exposes
// (lng, lat, window) → value lookups. Returns NaN where the satellite
// didn't capture data — callers must handle "no data" explicitly. We
// intentionally do NOT synthesize fake data; correctness over completeness.

import { BBOX } from "./mapData.js";

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

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`failed to load ${url}`));
    img.src = url;
  });
}

async function decodePng(url, scale, range) {
  const img = await loadImage(url);
  const c = document.createElement("canvas");
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  const id = ctx.getImageData(0, 0, c.width, c.height);
  const out = new Float32Array(c.width * c.height);
  const [lo, hi] = range;
  if (scale === "log10") {
    const llo = Math.log10(lo);
    const lhi = Math.log10(hi);
    for (let i = 0; i < out.length; i++) {
      const px = id.data[i * 4];
      out[i] = px === 0 ? NaN : Math.pow(10, llo + ((px - 1) / 254) * (lhi - llo));
    }
  } else {
    for (let i = 0; i < out.length; i++) {
      const px = id.data[i * 4];
      out[i] = px === 0 ? NaN : lo + ((px - 1) / 254) * (hi - lo);
    }
  }
  return { data: out, width: c.width, height: c.height };
}

async function decodeUVPng(url, uvRange) {
  const img = await loadImage(url);
  const c = document.createElement("canvas");
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  const id = ctx.getImageData(0, 0, c.width, c.height);
  const u = new Float32Array(c.width * c.height);
  const v = new Float32Array(c.width * c.height);
  const [lo, hi] = uvRange;
  const span = hi - lo;
  for (let i = 0; i < u.length; i++) {
    const a = id.data[i * 4 + 3];
    if (a === 0) {
      u[i] = NaN;
      v[i] = NaN;
    } else {
      u[i] = lo + (id.data[i * 4] / 255) * span;
      v[i] = lo + (id.data[i * 4 + 1] / 255) * span;
    }
  }
  return { u, v, width: c.width, height: c.height };
}

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
      state.layers[layer] = {};
      if (layer === "wind") {
        const speedRange = info.speed_range;
        const uvRange = info.uv_range;
        for (const [slot, w] of Object.entries(info.windows || {})) {
          const speed = await decodePng(w.speed_url, "linear", speedRange);
          const uv = await decodeUVPng(w.uv_url, uvRange);
          state.layers.wind[slot] = {
            ...speed,           // .data, .width, .height for speed
            uvU: uv.u,
            uvV: uv.v,
            valid_at: w.valid_at,
            fcst_hour: w.fcst_hour,
            source: w.source || null,  // "HRRR" / "GFS" — for the legend
          };
        }
      } else {
        const scale = info.scale || "linear";
        const range = info.range;
        for (const [win, w] of Object.entries(info.windows || {})) {
          const decoded = await decodePng(w.url, scale, range);
          state.layers[layer][win] = { ...decoded, dates: w.dates || [] };
        }
      }
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
  if (n === 0) return NaN;
  if (n < 4) return sum / n;
  return (
    v00 * (1 - tx) * (1 - ty) +
    v10 * tx * (1 - ty) +
    v01 * (1 - tx) * ty +
    v11 * tx * ty
  );
}

const COMPOSITE_KEY = { 1: "1d", 2: "2d", 3: "3d" };
const WIND_SLOT_KEY = { 1: "now", 2: "p6h", 3: "p24h", 4: "p72h" };

function slotKey(layer, composite) {
  if (layer === "wind") return WIND_SLOT_KEY[composite] || "now";
  return COMPOSITE_KEY[composite] || "1d";
}

export function getSST(lng, lat, composite = 1) {
  // Returns °C, or NaN if the satellite didn't capture this cell.
  return bilinear(state.layers.sst?.[slotKey("sst", composite)], lng, lat);
}

export function getChl(lng, lat, composite = 1) {
  // Returns mg/m³, or NaN if the satellite didn't capture this cell.
  return bilinear(state.layers.chl?.[slotKey("chl", composite)], lng, lat);
}

export function getWindSpeed(lng, lat, composite = 1) {
  // Returns knots (NaN if no data).
  return bilinear(state.layers.wind?.[slotKey("wind", composite)], lng, lat);
}

// Returns the loaded scalar grid for a (layer, composite) — the same Float32Array
// that bilinear() reads from. Lets DataOverlay render at native grid resolution
// (one canvas pixel per source cell) and let the browser scale up smoothly.
export function getLayerGrid(layer, composite) {
  const w = state.layers[layer]?.[slotKey(layer, composite)];
  if (!w) return null;
  return { data: w.data, width: w.width, height: w.height };
}

// Source name for the active wind slot ("HRRR" / "GFS"), or null when not loaded.
export function windSource(composite) {
  return state.layers.wind?.[slotKey("wind", composite)]?.source ?? null;
}

// Bilinear lookup against U or V grid (NaN-safe).
function bilinearComponent(grid, lng, lat) {
  if (!grid) return NaN;
  const { uvU, uvV, width, height } = grid;
  // unused param intentionally; this just probes shape.
  return NaN;
}

export function getWindUV(lng, lat, composite = 1) {
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
  const key = slotKey(layer, composite);
  const w = state.layers[layer]?.[key];
  if (!w) return null;
  if (layer === "wind") return [w.valid_at];
  return w.dates || null;
}

export function isReal(layer, composite) {
  return Boolean(state.layers[layer]?.[slotKey(layer, composite)]);
}
