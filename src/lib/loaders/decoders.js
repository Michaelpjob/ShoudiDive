// Shared decode + bucketing helpers extracted from dataSource.js as
// part of the 2026-05-09 Tier-1 follow-up (per-layer loader split).
//
// Why this lives in its own file:
//   * Each per-layer loader under src/lib/loaders/ needs a subset of
//     these helpers. Keeping them in dataSource.js + importing them
//     into each loader would create a circular dependency
//     (dataSource.js → loaders/sst7d.js → dataSource.js).
//   * The remaining hourly-PNG fetchers in dataSource.js (loadSwell5dHourly,
//     loadWind5dHourly) also need decodeUVPng / decodeWavePng. They
//     import from here too — same module, no cycle.
//   * `bucketKey` / `hourKey` are imported by CurrentTimeline.jsx +
//     WindDayGrid.jsx. dataSource.js re-exports them for backward
//     compatibility so the timeline components don't have to change.

import { buildLandMask, loadLandGeoJSON } from "../landMask.js";

const currentSampleMasks = new Map();

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`failed to load ${url}`));
    img.src = url;
  });
}

// Iterative dilation: each pass replaces NaN cells with the mean of
// their finite 8-neighbours. K passes propagate valid data K cells
// outward — fills small holes (cloud-shadowed satellite pixels) and
// smears valid SoCal coverage into the marine-layer gaps that wipe out
// the viz model otherwise. Mutates the grid in place.
export function fillNearestInPlace(grid, maxIters = 8) {
  const { data, width, height } = grid;
  for (let iter = 0; iter < maxIters; iter++) {
    let changed = 0;
    const snapshot = new Float32Array(data);
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const i = y * width + x;
        if (Number.isFinite(snapshot[i])) continue;
        let sum = 0, n = 0;
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            if (dx === 0 && dy === 0) continue;
            const nx = x + dx, ny = y + dy;
            if (nx < 0 || nx >= width || ny < 0 || ny >= height) continue;
            const v = snapshot[ny * width + nx];
            if (Number.isFinite(v)) { sum += v; n++; }
          }
        }
        if (n > 0) { data[i] = sum / n; changed++; }
      }
    }
    if (changed === 0) break;
  }
}

export async function decodePng(url, scale, range) {
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

export async function decodeWavePng(url, heightRange, periodRange) {
  // RGBA: R=Hs (m), G=Tp (s), B=Dp (deg), A=valid.
  const img = await loadImage(url);
  const c = document.createElement("canvas");
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  const id = ctx.getImageData(0, 0, c.width, c.height);
  const N = c.width * c.height;
  const hs = new Float32Array(N);
  const tp = new Float32Array(N);
  const dp = new Float32Array(N);
  const speed = new Float32Array(N); // alias for `data` so DataOverlay's
                                     // generic getLayerGrid path still works.
  const [hLo, hHi] = heightRange;
  const [pLo, pHi] = periodRange;
  for (let i = 0; i < N; i++) {
    const a = id.data[i * 4 + 3];
    if (a === 0) {
      hs[i] = NaN; tp[i] = NaN; dp[i] = NaN; speed[i] = NaN;
    } else {
      hs[i] = hLo + (id.data[i * 4]     / 255) * (hHi - hLo);
      tp[i] = pLo + (id.data[i * 4 + 1] / 255) * (pHi - pLo);
      dp[i] = (id.data[i * 4 + 2] / 255) * 360.0;
      speed[i] = hs[i]; // Hs in metres drives the heatmap layer.
    }
  }
  return {
    hs, tp, dp,
    width: c.width, height: c.height,
    data: speed, // .data is what bilinear() / getLayerGrid() reads
  };
}

export async function decodeUVPng(url, uvRange) {
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

export async function currentSampleMask(width, height) {
  const key = `${width}x${height}`;
  if (currentSampleMasks.has(key)) return currentSampleMasks.get(key);
  const maskPromise = loadLandGeoJSON().then((fc) => buildLandMask(fc?.features, width, height));
  currentSampleMasks.set(key, maskPromise);
  return maskPromise;
}

export function landMaskedCurrentSample(uv, mask) {
  const u = new Float32Array(uv.u);
  const v = new Float32Array(uv.v);
  if (mask) {
    for (let i = 0; i < mask.length; i++) {
      if (mask[i] === 1) {
        u[i] = NaN;
        v[i] = NaN;
      }
    }
  }
  return { u, v, width: uv.width, height: uv.height };
}

// Speed (kt) array derived from u/v components. Keeps DataOverlay rendering
// the same kind of scalar grid the legacy wind layer used.
export function computeSpeedKt({ u, v, width, height }) {
  const out = new Float32Array(width * height);
  for (let i = 0; i < out.length; i++) {
    const uu = u[i], vv = v[i];
    if (Number.isFinite(uu) && Number.isFinite(vv)) {
      out[i] = Math.sqrt(uu * uu + vv * vv) * 1.94384;  // m/s → kt
    } else {
      out[i] = NaN;
    }
  }
  return out;
}

// Bucket key helpers — used by both the manifest loaders and the lazy
// hourly fetchers. Tested at runtime by the bucket-aware timeline
// components (CurrentTimeline.jsx, WindDayGrid.jsx) which import these
// from dataSource.js (which re-exports from here).
//
// Format MUST match what fetch_swell_5day.py / fetch_wind_5day.py
// emit in summary.json: `d{N}_{bucket}` and `d{N}_h{HH}`. A drift
// here silently produces empty bucket lookups in the timeline UI.
export function bucketKey(day, bucket) {
  return `d${day}_${bucket}`;
}

export function hourKey(day, hour) {
  return `d${day}_h${String(hour).padStart(2, "0")}`;
}
