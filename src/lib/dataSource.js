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
      if (layer === "wind5d") {
        // 5-day × 5-bucket forecast (new wind UI). Pull summary.json first
        // — keep the summary even if individual bucket PNGs fail to decode
        // so the day grid still renders with whatever data we have.
        try {
          const sres = await fetch(info.summary_url, { cache: "no-cache" });
          if (!sres.ok) throw new Error(`summary ${info.summary_url} ${sres.status}`);
          const summary = await sres.json();
          state.layers.wind5d = {
            summary,
            uvRange:    info.uv_range,
            speedRange: info.speed_range,
            buckets:    {},
            hourly:     {},
            hourlyLoading: {},
          };
          // Load every bucket UV in parallel. CRUCIAL: each task swallows
          // its own error so one bad PNG can't take down the whole forecast.
          const tasks = [];
          let failed = 0;
          let loaded = 0;
          for (const day of summary.days) {
            for (const b of day.buckets) {
              const key = bucketKey(day.day, b.bucket);
              tasks.push(
                decodeUVPng(b.uv_url, info.uv_range)
                  .then((uv) => {
                    const speed = computeSpeedKt(uv);
                    state.layers.wind5d.buckets[key] = {
                      uvU: uv.u, uvV: uv.v,
                      width: uv.width, height: uv.height,
                      data: speed, speedKt: speed,
                    };
                    loaded++;
                  })
                  .catch((e) => {
                    failed++;
                    console.warn(`wind5d bucket ${key} failed`, e);
                  })
              );
            }
          }
          await Promise.all(tasks);
          if (failed) {
            console.warn(`wind5d: ${loaded} buckets loaded, ${failed} failed`);
          }
        } catch (e) {
          console.warn("dataSource: wind5d summary load failed", e);
          // Don't null out — leave whatever summary did parse so the UI
          // can show the day labels even if the heatmap is missing.
        }
      } else if (layer === "wind") {
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
      } else if (layer === "viz") {
        const range = info.range_ft;
        for (const [slot, w] of Object.entries(info.windows || {})) {
          const decoded = await decodePng(w.url, "linear", range);
          state.layers.viz[slot] = { ...decoded, valid_at: w.valid_at };
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
const VIZ_SLOT_KEY  = { 1: "now" };

function slotKey(layer, composite) {
  // Strings pass through verbatim (used by wind5d's bucket/hour keys).
  if (typeof composite === "string") return composite;
  if (layer === "wind") return WIND_SLOT_KEY[composite] || "now";
  if (layer === "viz")  return VIZ_SLOT_KEY[composite] || "now";
  return COMPOSITE_KEY[composite] || "1d";
}

// Bucket / hour slot key conventions used across the wind5d state.
export function bucketKey(day, bucket) {
  return `d${day}_${bucket}`;
}
export function hourKey(day, hour) {
  return `d${day}_h${String(hour).padStart(2, "0")}`;
}

// Speed (kt) array derived from u/v components. Keeps DataOverlay rendering
// the same kind of scalar grid the legacy wind layer used.
function computeSpeedKt({ u, v, width, height }) {
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

export function getSST(lng, lat, composite = 1) {
  // Returns °C, or NaN if the satellite didn't capture this cell.
  return bilinear(state.layers.sst?.[slotKey("sst", composite)], lng, lat);
}

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
  // Wind: a string composite is a wind5d slot key (e.g. "d2_morning").
  if (layer === "wind" && typeof composite === "string") {
    const w = wind5dEntry(composite);
    return w ? { data: w.data, width: w.width, height: w.height } : null;
  }
  if (layer === "wind5d") {
    const w = wind5dEntry(composite);
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
  return Boolean(state.layers[layer]?.[slotKey(layer, composite)]);
}
