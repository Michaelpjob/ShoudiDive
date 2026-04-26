// Data source: loads /data/manifest.json + per-window PNGs and exposes
// (lng, lat, window) → value lookups. Falls back to the synthetic functions
// in mapData.js for any layer/window the manifest doesn't cover, so the UI
// keeps working before the pipeline has run.

import {
  BBOX,
  sstAt as syntheticSST,
  chlAt as syntheticChl,
} from "./mapData.js";

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
      const scale = info.scale || "linear";
      const range = info.range;
      for (const [win, w] of Object.entries(info.windows || {})) {
        const decoded = await decodePng(w.url, scale, range);
        state.layers[layer][win] = { ...decoded, dates: w.dates || [] };
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

export function getSST(lng, lat, composite = 1) {
  const key = COMPOSITE_KEY[composite] || "1d";
  const v = bilinear(state.layers.sst?.[key], lng, lat);
  if (Number.isFinite(v)) return v;
  return syntheticSST(lng, lat);
}

export function getChl(lng, lat, composite = 1) {
  const key = COMPOSITE_KEY[composite] || "1d";
  const v = bilinear(state.layers.chl?.[key], lng, lat);
  if (Number.isFinite(v)) return v;
  return syntheticChl(lng, lat);
}

// Returns the YYYY-MM-DD date list for the active window (real data only),
// or null when falling back to mock.
export function dataDates(layer, composite) {
  const key = COMPOSITE_KEY[composite] || "1d";
  return state.layers[layer]?.[key]?.dates ?? null;
}

export function isReal(layer, composite) {
  const key = COMPOSITE_KEY[composite] || "1d";
  return Boolean(state.layers[layer]?.[key]);
}
