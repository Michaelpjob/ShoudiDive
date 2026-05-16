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
