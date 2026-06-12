import { BBOX } from "./mapData.js";
import { dataPath } from "./region.js";

let landPromise = null;

export function loadLandGeoJSON() {
  if (landPromise) return landPromise;
  landPromise = fetch(dataPath("/data/land.geojson"))
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return landPromise;
}

export function buildLandMask(features, width, height) {
  if (!features || width <= 0 || height <= 0 || typeof document === "undefined") {
    return null;
  }

  const lngSpan = BBOX.lngMax - BBOX.lngMin;
  const latSpan = BBOX.latMax - BBOX.latMin;
  const toX = (lng) => ((lng - BBOX.lngMin) / lngSpan) * width;
  const toY = (lat) => ((BBOX.latMax - lat) / latSpan) * height;

  const c = document.createElement("canvas");
  c.width = width;
  c.height = height;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.fillStyle = "white";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "black";

  for (const f of features) {
    const geom = f.geometry;
    if (!geom) continue;
    const polys =
      geom.type === "Polygon" ? [geom.coordinates] :
      geom.type === "MultiPolygon" ? geom.coordinates : null;
    if (!polys) continue;
    for (const poly of polys) {
      ctx.beginPath();
      for (let r = 0; r < poly.length; r++) {
        const ring = poly[r];
        if (!ring.length) continue;
        ctx.moveTo(toX(ring[0][0]), toY(ring[0][1]));
        for (let i = 1; i < ring.length; i++) {
          ctx.lineTo(toX(ring[i][0]), toY(ring[i][1]));
        }
        ctx.closePath();
      }
      ctx.fill("evenodd");
    }
  }

  const id = ctx.getImageData(0, 0, width, height).data;
  const mask = new Uint8Array(width * height);
  for (let i = 0; i < mask.length; i++) {
    mask[i] = id[i * 4] < 128 ? 1 : 0;
  }
  return mask;
}


// ---- Hover-time "is this point on land?" ----------------------------------
//
// Used by the Tooltip to suppress data readings when the cursor is on
// a coastline polygon. Without this guard, bilinear() in dataSource.js
// expands outward up to 6 cells looking for the nearest finite pixel
// when all 4 corners are NaN (the deep-land case) — so hovering the
// peninsula renders e.g. "0.75 mg/m³ Clear" pulled from a nearby ocean
// cell. The number is technically real but reads as wrong to the user
// hovering land. (User report 2026-05-24, Baja peninsula at La Paz.)
//
// Resolution 560x440 ≈ 1.5 km/cell across the active region's bbox,
// fine enough that "on land" closely matches the visible coastline.
const HOVER_MASK_W = 560;
const HOVER_MASK_H = 440;
let _hoverMaskCache = null;
let _hoverMaskPromise = null;

export function ensureHoverLandMaskLoaded() {
  if (_hoverMaskPromise) return _hoverMaskPromise;
  _hoverMaskPromise = loadLandGeoJSON().then((fc) => {
    if (!fc?.features) return null;
    _hoverMaskCache = buildLandMask(fc.features, HOVER_MASK_W, HOVER_MASK_H);
    return _hoverMaskCache;
  });
  return _hoverMaskPromise;
}

// Kick off the load eagerly at module import so the first hover after
// page load already has the mask cached. Fire-and-forget; isLandAtSync()
// silently returns false until the promise resolves.
ensureHoverLandMaskLoaded();

export function isLandAtSync(lng, lat) {
  if (!_hoverMaskCache) return false;
  const lngSpan = BBOX.lngMax - BBOX.lngMin;
  const latSpan = BBOX.latMax - BBOX.latMin;
  const fx = (lng - BBOX.lngMin) / lngSpan;
  const fy = (BBOX.latMax - lat) / latSpan;
  if (fx < 0 || fx >= 1 || fy < 0 || fy >= 1) return false;
  const x = Math.floor(fx * HOVER_MASK_W);
  const y = Math.floor(fy * HOVER_MASK_H);
  return _hoverMaskCache[y * HOVER_MASK_W + x] === 1;
}
