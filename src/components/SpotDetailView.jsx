// SpotDetailView — full-screen chart-plotter view of a single saved spot.
//
// Rebuilt 2026-05-27 from scratch after the first iteration accumulated
// layer-seam artifacts ("two coastlines"). The rebuild has ONE governing
// rendering rule:
//
//   THE LAND POLYGON IS THE ONLY COASTLINE.
//
//   Every water-domain layer (depth tint, contours) extends UNDER the
//   land polygon, and land renders opaque on top of them. The depth
//   tint is built client-side from the bathy pixels: quantized into
//   NOAA-style depth bands and flood-filled (multi-source BFS) so the
//   GMRT raster's own NaN coastline disappears entirely — any
//   GMRT-vs-OSM disagreement about where land starts is hidden under
//   the opaque land fill instead of leaking out as a phantom edge.
//
// Built for scouting fishing + diving locations and supplementing a
// GPS unit:
//   * NOAA-convention depth bands (shallow = saturated blue, deep =
//     near-white) + contour lines + sounding numerals in feet
//   * Cursor crosshair with GPS-style coordinates (degrees decimal
//     minutes, the format fish-finders and handheld GPS units use)
//     plus decimal degrees and live depth readout
//   * Click to drop marks; copy coordinates to clipboard to punch
//     into a GPS unit
//   * Observed kelp canopy (CDFW 2016 aerial survey) as a hatch
//     overlay — actual forest extent, not management rectangles
//   * Reef / canyon / bank callouts in chart-italic labels
//
// Data: pre-computed bundle at public/data/spots/<id>/ (bathy.png,
// contours.geojson, coastline.geojson, kelp.geojson, mpa.geojson,
// landmarks.geojson, soundings.geojson + bundle.json manifest).

import { useEffect, useMemo, useRef, useState } from "react";
import { projectInBbox } from "../lib/mapData.js";
import { dataPath } from "../lib/region.js";
import { styleForType } from "./MpaLayer.jsx";
import {
  getSST,
  getChl,
  getWindSpeed,
  getWindUV,
  getVizFt,
  getColumnAt,
  getColumnSpot,
  getSwell5dStats,
  windCompass,
  windCardinal,
} from "../lib/dataSource.js";
import WaterColumn from "./micro/WaterColumn.jsx";
import { usePrefs } from "../contexts/PrefsContext.jsx";
import { track } from "../lib/analytics.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CANVAS_W = 960;
const CANVAS_H = 960;
const MAX_ZOOM = 16;
// One mark at a time: each chart click REPLACES it (it doubles as
// the water-column pin). The original multi-waypoint behavior can
// return behind shift-click if collecting GPS numbers comes back.

// NOAA chart convention: shallow water carries the most saturated blue,
// deepening toward near-white. Instantly readable as "light = deep".
// Thresholds in feet because that's how CA fishers + divers talk.
const DEPTH_BANDS_FT = [
  { maxFt: 10,       rgb: [142, 198, 222], label: "10" },
  { maxFt: 20,       rgb: [168, 213, 232], label: "20" },
  { maxFt: 30,       rgb: [192, 226, 241], label: "30" },
  { maxFt: 60,       rgb: [213, 237, 247], label: "60" },
  { maxFt: 120,      rgb: [229, 245, 251], label: "120" },
  { maxFt: 300,      rgb: [240, 250, 253], label: "300" },
  { maxFt: Infinity, rgb: [248, 252, 254], label: "300+" },
];
// Background under everything = the deepest band, so any sliver the
// band image doesn't cover reads as deep open water, never as a hole.
const DEEP_BG = "rgb(248, 252, 254)";

function bandRgbForFt(ft) {
  for (const b of DEPTH_BANDS_FT) {
    if (ft <= b.maxFt) return b.rgb;
  }
  return DEPTH_BANDS_FT[DEPTH_BANDS_FT.length - 1].rgb;
}

// ---------------------------------------------------------------------------
// Bundle loading (single shared promise per spot id)
// ---------------------------------------------------------------------------

const bundleCache = new Map();
function loadBundle(spotId) {
  if (bundleCache.has(spotId)) return bundleCache.get(spotId);
  const p = (async () => {
    const base = dataPath(`/data/spots/${spotId}`);
    const manifestRes = await fetch(`${base}/bundle.json`);
    if (!manifestRes.ok) throw new Error(`bundle.json HTTP ${manifestRes.status}`);
    const manifest = await manifestRes.json();
    const layers = manifest.layers || {};
    const fetchGeojson = async (key) => {
      const url = layers[key]?.url;
      if (!url) return null;
      try {
        const r = await fetch(`${base}/${url}`);
        return r.ok ? await r.json() : null;
      } catch {
        return null;
      }
    };
    const [contours, coastline, kelp, mpa, landmarks, soundings] = await Promise.all([
      fetchGeojson("contours"),
      fetchGeojson("coastline"),
      fetchGeojson("kelp"),
      fetchGeojson("mpa"),
      fetchGeojson("landmarks"),
      fetchGeojson("soundings"),
    ]);
    return {
      manifest,
      bathyUrl: layers.bathy?.url ? `${base}/${layers.bathy.url}` : null,
      contours, coastline, kelp, mpa, landmarks, soundings,
    };
  })().catch((err) => {
    bundleCache.delete(spotId);
    throw err;
  });
  bundleCache.set(spotId, p);
  return p;
}

// ---------------------------------------------------------------------------
// Bathy pixel decode + depth-band image synthesis
// ---------------------------------------------------------------------------

// Decode bathy.png (mode LA: L = depth 1..255, A = 0 on land/NaN) into a
// single-channel array. 0 = no data. Cached per URL.
const bathyPixelCache = new Map();
function loadBathyPixels(url, width, height) {
  if (bathyPixelCache.has(url)) return bathyPixelCache.get(url);
  const p = new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        const w = width || img.naturalWidth;
        const h = height || img.naturalHeight;
        const cv = document.createElement("canvas");
        cv.width = w;
        cv.height = h;
        const ctx = cv.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, w, h);
        const data = ctx.getImageData(0, 0, w, h).data;
        const gray = new Uint8ClampedArray(w * h);
        for (let i = 0, j = 0; i < data.length; i += 4, j++) {
          gray[j] = data[i + 3] === 0 ? 0 : data[i];
        }
        resolve({ gray, w, h });
      } catch (e) {
        reject(e);
      }
    };
    img.onerror = (e) => reject(e);
    img.src = url;
  }).catch(() => null);
  bathyPixelCache.set(url, p);
  return p;
}

// pixel 1..255 → depth in meters (linear over the spot's depth range)
function pixelToDepthM(pixel, depthRangeM) {
  if (!pixel) return null;
  const [dMin, dMax] = depthRangeM || [0, 500];
  return dMin + ((pixel - 1) / 254) * (dMax - dMin);
}

// Build the depth-band tint image:
//   1. Multi-source BFS fills every NaN cell with its nearest valid
//      neighbour's value — the GMRT raster's own coastline vanishes,
//      so the band field extends seamlessly under the land polygon.
//   2. Quantize each cell's depth into DEPTH_BANDS_FT via a 256-entry
//      LUT and write an opaque RGBA canvas.
// Returns a dataURL, or null when the grid has no valid cells at all.
function buildDepthBandImage(px, depthRangeM) {
  const { gray, w, h } = px;
  const n = w * h;
  const filled = new Uint8ClampedArray(gray);
  const queue = new Int32Array(n);
  let qlen = 0;
  for (let i = 0; i < n; i++) if (filled[i] !== 0) queue[qlen++] = i;
  if (qlen === 0) return null;
  let head = 0;
  while (head < qlen) {
    const i = queue[head++];
    const v = filled[i];
    const x = i % w;
    const up = i - w;
    const dn = i + w;
    if (up >= 0 && filled[up] === 0)      { filled[up] = v;     queue[qlen++] = up; }
    if (dn < n && filled[dn] === 0)       { filled[dn] = v;     queue[qlen++] = dn; }
    if (x > 0 && filled[i - 1] === 0)     { filled[i - 1] = v;  queue[qlen++] = i - 1; }
    if (x < w - 1 && filled[i + 1] === 0) { filled[i + 1] = v;  queue[qlen++] = i + 1; }
  }

  // LUT: pixel value → band rgb
  const lut = new Uint8Array(256 * 3);
  for (let p = 1; p < 256; p++) {
    const dM = pixelToDepthM(p, depthRangeM);
    const [r, g, b] = bandRgbForFt(dM * 3.28084);
    lut[p * 3] = r; lut[p * 3 + 1] = g; lut[p * 3 + 2] = b;
  }

  const cv = document.createElement("canvas");
  cv.width = w;
  cv.height = h;
  const ctx = cv.getContext("2d");
  const img = ctx.createImageData(w, h);
  const data = img.data;
  for (let i = 0; i < n; i++) {
    const p = filled[i];
    data[i * 4]     = lut[p * 3];
    data[i * 4 + 1] = lut[p * 3 + 1];
    data[i * 4 + 2] = lut[p * 3 + 2];
    data[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return cv.toDataURL("image/png");
}

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

function ringToPath(ring, bbox, w, h) {
  if (!ring.length) return "";
  const [x0, y0] = projectInBbox(bbox, ring[0][0], ring[0][1], w, h);
  let d = `M${x0.toFixed(2)} ${y0.toFixed(2)}`;
  for (let i = 1; i < ring.length; i++) {
    const [x, y] = projectInBbox(bbox, ring[i][0], ring[i][1], w, h);
    d += `L${x.toFixed(2)} ${y.toFixed(2)}`;
  }
  return d + "Z";
}

function lineToPath(line, bbox, w, h) {
  if (!line.length) return "";
  const [x0, y0] = projectInBbox(bbox, line[0][0], line[0][1], w, h);
  let d = `M${x0.toFixed(2)} ${y0.toFixed(2)}`;
  for (let i = 1; i < line.length; i++) {
    const [x, y] = projectInBbox(bbox, line[i][0], line[i][1], w, h);
    d += `L${x.toFixed(2)} ${y.toFixed(2)}`;
  }
  return d;
}

function geometryToPath(geom, bbox, w, h) {
  if (!geom) return "";
  if (geom.type === "Polygon") {
    return geom.coordinates.map((r) => ringToPath(r, bbox, w, h)).join(" ");
  }
  if (geom.type === "MultiPolygon") {
    return geom.coordinates.flatMap((p) => p.map((r) => ringToPath(r, bbox, w, h))).join(" ");
  }
  if (geom.type === "LineString") {
    return lineToPath(geom.coordinates, bbox, w, h);
  }
  if (geom.type === "MultiLineString") {
    return geom.coordinates.map((l) => lineToPath(l, bbox, w, h)).join(" ");
  }
  return "";
}

// Ray-cast point-in-polygon. OSM land is the ground truth for "is this
// land" — used to keep soundings off the land fill.
function pointInRing(lng, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    const intersect = ((yi > lat) !== (yj > lat)) &&
      (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

function pointInGeometry(lng, lat, geom) {
  if (!geom?.coordinates) return false;
  if (geom.type === "Polygon") {
    if (!pointInRing(lng, lat, geom.coordinates[0])) return false;
    for (let i = 1; i < geom.coordinates.length; i++) {
      if (pointInRing(lng, lat, geom.coordinates[i])) return false;
    }
    return true;
  }
  if (geom.type === "MultiPolygon") {
    for (const poly of geom.coordinates) {
      if (!pointInRing(lng, lat, poly[0])) continue;
      let inHole = false;
      for (let i = 1; i < poly.length; i++) {
        if (pointInRing(lng, lat, poly[i])) { inHole = true; break; }
      }
      if (!inHole) return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// GPS coordinate formatting
// ---------------------------------------------------------------------------

// Degrees decimal-minutes — the format GPS units + fish-finders use.
// 32.8505 → 32°51.030'N
function formatDDM(value, posHemi, negHemi) {
  const hemi = value >= 0 ? posHemi : negHemi;
  const abs = Math.abs(value);
  const deg = Math.floor(abs);
  const min = (abs - deg) * 60;
  return `${deg}°${min.toFixed(3)}'${hemi}`;
}

function formatDecimal(lat, lng) {
  return `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}

// ---------------------------------------------------------------------------
// Contour styling — one blue-gray family, never near-black. Index
// contours (every 50 m) heavier; everything else whispers.
// ---------------------------------------------------------------------------

function contourStyle(depthM) {
  if (depthM % 50 === 0) return { stroke: "#33627f", width: 1.2, opacity: 0.9 };
  if (depthM % 10 === 0) return { stroke: "#4a7a99", width: 0.85, opacity: 0.8 };
  return { stroke: "#739cb8", width: 0.5, opacity: 0.7 };
}

function clampVb(vb, baseW, baseH) {
  const minW = baseW / MAX_ZOOM;
  const w = Math.max(minW, Math.min(baseW, vb.w));
  const h = w * (baseH / baseW);
  const x = Math.max(0, Math.min(baseW - w, vb.x));
  const y = Math.max(0, Math.min(baseH - h, vb.y));
  return { x, y, w, h };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function SpotDetailView({ spot, onClose }) {
  const [bundle, setBundle] = useState(null);
  const [error, setError] = useState(null);
  const [layers, setLayers] = useState({
    bands: true,      // depth tint (NOAA-style bands)
    contours: true,
    soundings: true,
    kelp: true,
    landmarks: true,  // reef callouts + place labels
    mpa: false,       // regulatory rectangles — opt-in
  });
  // Cursor in lng/lat + canvas coords (for crosshair + tooltip).
  const [cursor, setCursor] = useState(null);
  const [cursorPx, setCursorPx] = useState(null);
  // Dropped marks (click-to-mark for GPS handoff).
  const [marks, setMarks] = useState([]);
  const [copied, setCopied] = useState(false);
  const [bathyPixels, setBathyPixels] = useState(null);
  const { prefs } = usePrefs();
  const { units } = prefs;

  const stageRef = useRef(null);
  const [vb, setVb] = useState({ x: 0, y: 0, w: CANVAS_W, h: CANVAS_H });
  const vbRef = useRef(vb);
  vbRef.current = vb;
  const panStateRef = useRef(null);
  const pinchStateRef = useRef(null);

  const bbox = bundle?.manifest?.bbox;
  const bboxRef = useRef(null);
  bboxRef.current = bbox;

  // ---- Bundle load + analytics ------------------------------------------
  useEffect(() => {
    track("spot_detail_open", { id: spot.id });
    let cancelled = false;
    loadBundle(spot.id)
      .then((b) => { if (!cancelled) setBundle(b); })
      .catch((e) => { if (!cancelled) setError(e.message); });
    return () => {
      cancelled = true;
      track("spot_detail_close", { id: spot.id });
    };
  }, [spot.id]);

  // Escape closes
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Decode bathy pixels once per bundle (drives band image + cursor depth)
  useEffect(() => {
    if (!bundle?.bathyUrl) return;
    const w = bundle.manifest?.layers?.bathy?.width;
    const h = bundle.manifest?.layers?.bathy?.height;
    let cancelled = false;
    loadBathyPixels(bundle.bathyUrl, w, h).then((px) => {
      if (!cancelled) setBathyPixels(px);
    });
    return () => { cancelled = true; };
  }, [bundle]);

  // Depth-band tint image (BFS-filled + quantized) — built once per bundle.
  const bandImageUrl = useMemo(() => {
    if (!bathyPixels) return null;
    const dr = bundle?.manifest?.layers?.bathy?.depth_range_m || [0, 500];
    return buildDepthBandImage(bathyPixels, dr);
  }, [bathyPixels, bundle]);

  // Wheel zoom — attached manually with passive:false so preventDefault
  // actually stops the page scroll (React's synthetic wheel listener is
  // passive at the root).
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const onWheel = (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1 / 1.25 : 1.25;
      setVb((prev) => {
        const m = stageMetrics(prev);
        if (!m) return prev;
        // Canvas point under the pointer stays fixed through the zoom.
        const cx = prev.x + (e.clientX - m.left) / m.scale;
        const cy = prev.y + (e.clientY - m.top) / m.scale;
        const fx = (cx - prev.x) / prev.w;
        const fy = (cy - prev.y) / prev.h;
        const newW = prev.w * factor;
        const newH = newW * (CANVAS_H / CANVAS_W);
        return clampVb({
          x: cx - fx * newW,
          y: cy - fy * newH,
          w: newW, h: newH,
        }, CANVAS_W, CANVAS_H);
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  function zoomBy(factor) {
    setVb((prev) => {
      const newW = prev.w * factor;
      const newH = newW * (CANVAS_H / CANVAS_W);
      const cx = prev.x + prev.w / 2;
      const cy = prev.y + prev.h / 2;
      return clampVb({
        x: cx - newW / 2, y: cy - newH / 2, w: newW, h: newH,
      }, CANVAS_W, CANVAS_H);
    });
  }

  function resetView() {
    setVb({ x: 0, y: 0, w: CANVAS_W, h: CANVAS_H });
  }

  function toggleLayer(key) {
    setLayers((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      track("spot_detail_layer_toggle", { id: spot.id, layer: key, on: next[key] });
      return next;
    });
  }

  // The SVG renders with preserveAspectRatio="xMidYMid meet": the
  // square viewBox is uniformly scaled to FIT the stage and centred,
  // leaving letterbox gutters on the stage's long axis. Screen↔canvas
  // conversions must therefore map over the rendered CONTENT rect,
  // not the raw stage rect — the old stage-rect mapping stretched the
  // short axis, so the crosshair, dropped marks, and pan/zoom anchors
  // drifted away from the pointer the further it sat from the stage
  // centre (QA 2026-06-10: "cursor is off center vs the gps cord" —
  // hovering the spot-centre pin read coords ~300 m off).
  function stageMetrics(vbCur) {
    const r = stageRef.current?.getBoundingClientRect();
    if (!r || !r.width || !r.height) return null;
    const scale = Math.min(r.width / vbCur.w, r.height / vbCur.h);
    return {
      rect: r,
      scale, // screen px per canvas unit (uniform, both axes)
      left: r.left + (r.width - vbCur.w * scale) / 2,
      top: r.top + (r.height - vbCur.h * scale) / 2,
    };
  }

  // Screen px → { lng, lat, canvasX, canvasY }
  function screenToGeo(clientX, clientY) {
    const bb = bboxRef.current;
    if (!bb) return null;
    const v = vbRef.current;
    const m = stageMetrics(v);
    if (!m) return null;
    const cx = v.x + (clientX - m.left) / m.scale;
    const cy = v.y + (clientY - m.top) / m.scale;
    const lng = bb.lng_min + (cx / CANVAS_W) * (bb.lng_max - bb.lng_min);
    const lat = bb.lat_max - (cy / CANVAS_H) * (bb.lat_max - bb.lat_min);
    // screenX/screenY stay stage-relative — they position the HTML
    // cursor tooltip, which floats in the stage div, not the SVG.
    return { lng, lat, canvasX: cx, canvasY: cy,
             screenX: clientX - m.rect.left, screenY: clientY - m.rect.top };
  }

  // ---- Mouse: pan, hover readout, click-to-mark ---------------------------
  function onMouseDown(e) {
    const m = stageMetrics(vb);
    if (!m) return;
    panStateRef.current = {
      startX: e.clientX - m.rect.left,
      startY: e.clientY - m.rect.top,
      startVb: vb,
      // px-per-canvas-unit is constant while panning (vb.w fixed), so
      // capture it once — deltas below divide by it to track 1:1.
      scale: m.scale,
      moved: false,
    };
  }

  function onMouseMove(e) {
    const r = stageRef.current.getBoundingClientRect();
    const ps = panStateRef.current;
    if (ps) {
      const dxPx = e.clientX - r.left - ps.startX;
      const dyPx = e.clientY - r.top - ps.startY;
      if (Math.abs(dxPx) + Math.abs(dyPx) > 4) ps.moved = true;
      if (ps.moved) {
        setVb(clampVb({
          x: ps.startVb.x - dxPx / ps.scale,
          y: ps.startVb.y - dyPx / ps.scale,
          w: ps.startVb.w,
          h: ps.startVb.h,
        }, CANVAS_W, CANVAS_H));
        return;
      }
    }
    const geo = screenToGeo(e.clientX, e.clientY);
    if (!geo) return;
    setCursor({ lng: geo.lng, lat: geo.lat, canvasX: geo.canvasX, canvasY: geo.canvasY });
    setCursorPx({ x: geo.screenX, y: geo.screenY });
  }

  function onMouseUp(e) {
    const ps = panStateRef.current;
    panStateRef.current = null;
    // Click (no drag) = drop a mark for GPS handoff.
    if (ps && !ps.moved) {
      const geo = screenToGeo(e.clientX, e.clientY);
      if (geo) addMark(geo.lng, geo.lat);
    }
  }

  function onMouseLeave() {
    panStateRef.current = null;
    setCursor(null);
    setCursorPx(null);
  }

  // ---- Touch: pan, pinch, tap-to-mark -------------------------------------
  function onTouchStart(e) {
    const m = stageMetrics(vb);
    if (!m) return;
    if (e.touches.length === 1) {
      const t = e.touches[0];
      panStateRef.current = {
        startX: t.clientX - m.rect.left,
        startY: t.clientY - m.rect.top,
        startVb: vb,
        scale: m.scale,
        moved: false,
      };
    } else if (e.touches.length === 2) {
      const a = e.touches[0], b = e.touches[1];
      const midX = (a.clientX + b.clientX) / 2;
      const midY = (a.clientY + b.clientY) / 2;
      // Canvas-space pinch anchor + its fraction of the start viewBox
      // — the anchor stays put as the viewBox rescales around it.
      const anchorCx = vb.x + (midX - m.left) / m.scale;
      const anchorCy = vb.y + (midY - m.top) / m.scale;
      pinchStateRef.current = {
        anchorCx, anchorCy,
        fx: (anchorCx - vb.x) / vb.w,
        fy: (anchorCy - vb.y) / vb.h,
        startDist: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
        startVb: vb,
      };
      panStateRef.current = null;
    }
  }

  function onTouchMove(e) {
    const r = stageRef.current.getBoundingClientRect();
    if (e.touches.length === 1 && panStateRef.current) {
      const t = e.touches[0];
      const ps = panStateRef.current;
      const dxPx = t.clientX - r.left - ps.startX;
      const dyPx = t.clientY - r.top - ps.startY;
      if (Math.abs(dxPx) + Math.abs(dyPx) > 8) ps.moved = true;
      if (!ps.moved) return;
      setVb(clampVb({
        x: ps.startVb.x - dxPx / ps.scale,
        y: ps.startVb.y - dyPx / ps.scale,
        w: ps.startVb.w,
        h: ps.startVb.h,
      }, CANVAS_W, CANVAS_H));
    } else if (e.touches.length === 2 && pinchStateRef.current) {
      const a = e.touches[0], b = e.touches[1];
      const ps = pinchStateRef.current;
      const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      if (dist === 0) return;
      const factor = ps.startDist / dist;
      const newW = ps.startVb.w * factor;
      const newH = newW * (CANVAS_H / CANVAS_W);
      setVb(clampVb({
        x: ps.anchorCx - ps.fx * newW,
        y: ps.anchorCy - ps.fy * newH,
        w: newW, h: newH,
      }, CANVAS_W, CANVAS_H));
    }
  }

  function onTouchEnd(e) {
    if (e.touches.length === 0) {
      const ps = panStateRef.current;
      if (ps && !ps.moved && e.changedTouches[0]) {
        const ct = e.changedTouches[0];
        const geo = screenToGeo(ct.clientX, ct.clientY);
        if (geo) {
          setCursor({ lng: geo.lng, lat: geo.lat, canvasX: geo.canvasX, canvasY: geo.canvasY });
          addMark(geo.lng, geo.lat);
        }
      }
      panStateRef.current = null;
      pinchStateRef.current = null;
    } else if (e.touches.length === 1 && pinchStateRef.current) {
      const m = stageMetrics(vbRef.current);
      const t = e.touches[0];
      pinchStateRef.current = null;
      if (!m) { panStateRef.current = null; return; }
      panStateRef.current = {
        startX: t.clientX - m.rect.left,
        startY: t.clientY - m.rect.top,
        startVb: vbRef.current,
        scale: m.scale,
        moved: true, // post-pinch settle shouldn't drop a mark
      };
    }
  }

  // ---- Marks ----------------------------------------------------------------
  function addMark(lng, lat) {
    const d = depthAt(lng, lat);
    setMarks([{
      lng, lat,
      depthFt: d && !d.onLand ? Math.round(d.depthFt) : null,
    }]);
  }

  function copyCoords(m) {
    const txt = formatDecimal(m.lat, m.lng);
    try {
      navigator.clipboard?.writeText(txt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard unavailable — non-fatal */ }
  }

  // ---- Pre-projected geometry ----------------------------------------------
  const landPaths = useMemo(() => {
    if (!bbox || !bundle?.coastline) return [];
    return (bundle.coastline.features || []).map((f, i) => ({
      key: `land-${i}`,
      d: geometryToPath(f.geometry, bbox, CANVAS_W, CANVAS_H),
    }));
  }, [bundle, bbox]);

  const contourPaths = useMemo(() => {
    if (!bbox || !bundle?.contours) return [];
    return (bundle.contours.features || []).map((f, i) => ({
      key: `c-${i}-${f.properties.depth_m}`,
      depth: f.properties.depth_m,
      d: geometryToPath(f.geometry, bbox, CANVAS_W, CANVAS_H),
    }));
  }, [bundle, bbox]);

  const mpaPaths = useMemo(() => {
    if (!bbox || !bundle?.mpa) return [];
    return (bundle.mpa.features || []).map((f, i) => ({
      key: f.properties?.id || `mpa-${i}`,
      props: f.properties,
      d: geometryToPath(f.geometry, bbox, CANVAS_W, CANVAS_H),
      style: styleForType(f.properties?.type),
    }));
  }, [bundle, bbox]);

  const kelpPaths = useMemo(() => {
    if (!bbox || !bundle?.kelp) return [];
    return (bundle.kelp.features || []).map((f, i) => ({
      key: f.properties?.id || `kelp-${i}`,
      props: f.properties,
      surface: (f.properties?.className || "").toLowerCase() === "kelp canopy",
      d: geometryToPath(f.geometry, bbox, CANVAS_W, CANVAS_H),
    }));
  }, [bundle, bbox]);

  const landmarkPts = useMemo(() => {
    if (!bbox || !bundle?.landmarks) return [];
    const pts = (bundle.landmarks.features || []).map((f) => {
      const [lng, lat] = f.geometry.coordinates;
      const [x, y] = projectInBbox(bbox, lng, lat, CANVAS_W, CANVAS_H);
      return {
        key: f.properties.name,
        x, y,
        name: f.properties.name,
        importance: f.properties.importance || "minor",
        category: f.properties.category || "coastal",
        flip: false, // label side: false = right of marker, true = left
      };
    });
    // Collision pass: when two markers sit close together (e.g.
    // Point La Jolla + La Jolla Caves, ~300 m apart), fan the labels
    // OUTWARD by geography — the western marker's label goes left,
    // the eastern one's goes right — so the texts grow away from
    // each other instead of overprinting in the gap between markers.
    // Overview-zoom decluttering, chart-convention: within any tight
    // cluster (markers < 34 canvas px ≈ a few hundred metres apart),
    // only the most important name renders below 2× zoom; the rest
    // fade in as the user zooms and the labels physically separate
    // (font scales 1/zoom). Greedy pass in importance-rank order —
    // a point is suppressed if a kept point already sits within the
    // cluster radius. Also fan close *kept* pairs apart by geography
    // (west label extends left, east extends right).
    const rank = { marquee: 0, major: 1, minor: 2 };
    const byRank = [...pts].sort(
      (a, b) => (rank[a.importance] ?? 2) - (rank[b.importance] ?? 2)
    );
    const kept = [];
    for (const p of byRank) {
      const clash = kept.find((k) => {
        const dx = p.x - k.x, dy = p.y - k.y;
        return dx * dx + dy * dy < 34 * 34;
      });
      if (clash) {
        p.suppressLow = true; // hidden below 2× zoom
        // Pre-fan for when it appears: extend away from its neighbour
        p.flip = p.x < clash.x;
      } else {
        kept.push(p);
      }
    }
    for (let i = 0; i < kept.length; i++) {
      for (let j = 0; j < i; j++) {
        const dx = kept[i].x - kept[j].x;
        const dy = kept[i].y - kept[j].y;
        if (dx * dx + dy * dy < 60 * 60) {
          const west = kept[i].x < kept[j].x ? kept[i] : kept[j];
          const east = west === kept[i] ? kept[j] : kept[i];
          west.flip = true;
          east.flip = false;
        }
      }
    }
    return pts;
  }, [bundle, bbox]);

  // Soundings, filtered to OSM-water (PIP against land polygons).
  const soundingPts = useMemo(() => {
    if (!bbox || !bundle?.soundings) return [];
    const landGeoms = (bundle?.coastline?.features || []).map((f) => f.geometry);
    return (bundle.soundings.features || [])
      .map((f) => {
        const [lng, lat] = f.geometry.coordinates;
        if (landGeoms.some((g) => pointInGeometry(lng, lat, g))) return null;
        const [x, y] = projectInBbox(bbox, lng, lat, CANVAS_W, CANVAS_H);
        return { key: `${lng}-${lat}`, x, y, depth_ft: f.properties.depth_ft };
      })
      .filter(Boolean);
  }, [bundle, bbox]);

  const pinXY = useMemo(() => {
    if (!bbox) return [CANVAS_W / 2, CANVAS_H / 2];
    return projectInBbox(bbox, spot.lng, spot.lat, CANVAS_W, CANVAS_H);
  }, [bbox, spot]);

  const zoomLevel = CANVAS_W / vb.w;

  // ---- Depth sampling --------------------------------------------------------
  function depthAt(lng, lat) {
    if (!bathyPixels || !bboxRef.current) return null;
    const bb = bboxRef.current;
    const fx = (lng - bb.lng_min) / (bb.lng_max - bb.lng_min);
    const fy = (bb.lat_max - lat) / (bb.lat_max - bb.lat_min);
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return null;
    const { gray, w, h } = bathyPixels;
    const px = Math.max(0, Math.min(w - 1, Math.floor(fx * w)));
    const py = Math.max(0, Math.min(h - 1, Math.floor(fy * h)));
    const pixel = gray[py * w + px];
    if (pixel === 0) return { onLand: true, depthM: null, depthFt: null };
    const dr = bundle?.manifest?.layers?.bathy?.depth_range_m || [0, 500];
    const dM = pixelToDepthM(pixel, dr);
    return { onLand: false, depthM: dM, depthFt: dM * 3.28084 };
  }

  const cursorDepth = cursor ? depthAt(cursor.lng, cursor.lat) : null;
  const depthLabel = cursorDepth == null
    ? "—"
    : cursorDepth.onLand
      ? "land"
      : `${Math.round(cursorDepth.depthFt)} ft · ${cursorDepth.depthM.toFixed(0)} m`;

  // ---- Water column at the cursor (PRD water-column V2, micro form) -------
  // The regional column rasters give cliff/below vis; the bundle DEM
  // overrides the coarse 10 km bottom depth with chart-resolution
  // depth at the exact cursor point — so a shallow shelf correctly
  // clips the murk layer and a wall shows the full descent. Idle
  // cursor falls back to the spot centre, whose sidecar also carries
  // the 24 h cliff series.
  const lastMark = marks.length ? marks[marks.length - 1] : null;

  const waterColumn = (() => {
    if (!prefs.waterColumnOn) return null;
    // Point priority: PINNED mark (last chart click) > live cursor >
    // spot centre. Dropping a mark locks the readout to that point —
    // the column stops chasing the cursor until the next click
    // re-pins it (or Clear marks restores cursor-follow).
    const pin = lastMark;
    const at = pin || cursor || { lng: spot.lng, lat: spot.lat };
    const spotMode = !pin && !cursor;
    const d = depthAt(at.lng, at.lat);
    let out = null;
    if (!d?.onLand) {
      const col = getColumnAt(at.lng, at.lat);
      if (col) {
        out = col;
        if (d && Number.isFinite(d.depthFt)) {
          const bottom = Math.round(d.depthFt);
          const noCliff = col.cliff_ft != null && bottom <= col.cliff_ft;
          out = { ...col, bottom_ft: bottom, no_cliff: noCliff,
                  below_ft: noCliff ? col.surface_ft : col.below_ft };
        }
      }
    }
    // Spot-mode fallback: a saved-spot anchor can sit on a masked raster
    // pixel (Catalina = island centroid, Monterey = inner bay), which
    // would otherwise render no card at all until the first click. The
    // sidecar samples nearest-finite water for exactly this case — show
    // its profile instead.
    if (!out && spotMode) {
      const sc = getColumnSpot(spot.id);
      if (sc) out = { ...sc, bottom_ft: Math.round(sc.bottom_ft) };
    }
    if (!out) return null;
    // 24 h cliff series. The bundle spot's sidecar carries the
    // tide-phased series; the v1 swing amplitude is month-global, so
    // an arbitrary pinned point's series is the sidecar series
    // RE-CENTERED on that point's own cliff depth. Suppressed while
    // cursor-following (too twitchy mid-glide) and on no-cliff
    // shallows (a swing chart for water the cliff never enters reads
    // as noise).
    let series = null;
    if (!out.no_cliff && (pin || !cursor)) {
      const sc = getColumnSpot(spot.id);
      if (sc?.cliff_series_ft && Number.isFinite(out.cliff_ft)) {
        const offset = out.cliff_ft - (sc.cliff_ft ?? out.cliff_ft);
        series = sc.cliff_series_ft.map(
          (v) => Math.round((v + offset) * 10) / 10);
      }
    }
    const title = pin
      ? `Pinned · ${at.lat.toFixed(4)}°N ${Math.abs(at.lng).toFixed(4)}°W`
      : cursor ? "At cursor" : spot.name;
    return { col: out, title, series };
  })();

  // ---- Conditions at the spot centre ----------------------------------------
  const conditions = useMemo(() => {
    const sstC = getSST(spot.lng, spot.lat, 1);
    const sstStr = Number.isFinite(sstC)
      ? (units === "F" ? `${(sstC * 9 / 5 + 32).toFixed(1)}°F` : `${sstC.toFixed(1)}°C`)
      : "—";
    const chlMg = getChl(spot.lng, spot.lat, 1);
    const chlStr = Number.isFinite(chlMg) ? `${chlMg.toFixed(2)} mg/m³` : "—";
    const windKt = getWindSpeed(spot.lng, spot.lat, 1);
    let windStr = "—";
    if (Number.isFinite(windKt)) {
      const { u, v } = getWindUV(spot.lng, spot.lat, 1);
      const dir = Number.isFinite(u) && Number.isFinite(v)
        ? ` ${windCardinal(windCompass(u, v))}` : "";
      windStr = `${windKt.toFixed(0)} kt${dir}`;
    }
    const sw = getSwell5dStats(spot.lng, spot.lat, "d0_morning");
    const swellStr = Number.isFinite(sw?.hs)
      ? `${(sw.hs * 3.28084).toFixed(1)} ft${Number.isFinite(sw.tp) ? ` · ${sw.tp.toFixed(0)} s` : ""}`
      : "—";
    const vizFt = getVizFt(spot.lng, spot.lat, 1);
    const vizStr = Number.isFinite(vizFt) ? `~${Math.round(vizFt)} ft` : "—";
    return { sstStr, chlStr, windStr, swellStr, vizStr };
  }, [spot, units]);

  const bundleDate = bundle?.manifest?.generated_at
    ? new Date(bundle.manifest.generated_at).toISOString().slice(0, 10)
    : null;


  // ---------------------------------------------------------------------------
  return (
    <div className="spot-detail-overlay" role="dialog" aria-modal="true" aria-label={`${spot.name} detail chart`}>
      <div className="spot-detail-header">
        <div className="spot-detail-title">
          <strong>{spot.name}</strong>
          <span className="spot-detail-sub">
            {formatDDM(spot.lat, "N", "S")} {formatDDM(spot.lng, "E", "W")}
            {bbox && ` · ${Math.round((bbox.lat_max - bbox.lat_min) * 111)} km chart`}
          </span>
        </div>
        <button
          type="button"
          className="spot-detail-close"
          onClick={onClose}
          aria-label="Close detail chart"
        >×</button>
      </div>

      <div
        className="spot-detail-stage"
        ref={stageRef}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseLeave}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onTouchCancel={onTouchEnd}
      >
        {!bundle && !error && (
          <div className="spot-detail-loading">Loading chart…</div>
        )}
        {error && (
          <div className="spot-detail-error">Chart load failed: {error}</div>
        )}
        {bundle && (
          <svg
            className="spot-detail-svg"
            viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
            preserveAspectRatio="xMidYMid meet"
          >
            <defs>
              {/* Kelp canopy — diagonal hatch (surface forest) */}
              <pattern
                id="kelp-canopy-hatch"
                width="6" height="6"
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <rect width="6" height="6" fill="rgba(34, 110, 60, 0.40)" />
                <line x1="0" y1="0" x2="0" y2="6" stroke="#14532d" strokeWidth="1.3" />
              </pattern>
              {/* Kelp subsurface — dot stipple */}
              <pattern
                id="kelp-subsurface-dots"
                width="5" height="5"
                patternUnits="userSpaceOnUse"
              >
                <rect width="5" height="5" fill="rgba(74, 160, 100, 0.22)" />
                <circle cx="2.5" cy="2.5" r="0.9" fill="#15803d" />
              </pattern>
            </defs>

            {/* 1. Deep-water background — any uncovered sliver reads as
                open water, never a hole. Extends well past the canvas
                so the letterbox gutters (preserveAspectRatio="meet" on
                a non-square stage) read as open ocean too — without
                this the chart floats as a square between dark voids
                and the bbox edge looks like a hard clip (QA
                2026-06-10: "catalina is clipped on the edges"). */}
            <rect
              x={-2 * CANVAS_W} y={-2 * CANVAS_H}
              width={5 * CANVAS_W} height={5 * CANVAS_H}
              fill={DEEP_BG}
            />

            {/* 2. Depth-band tint. BFS-filled so it extends UNDER the
                land polygon — no transparency edge of its own. */}
            {layers.bands && bandImageUrl && (
              <image
                href={bandImageUrl}
                x="0" y="0"
                width={CANVAS_W} height={CANVAS_H}
                preserveAspectRatio="none"
              />
            )}

            {/* 3. Contour lines — drawn under land so any GMRT-side
                overshoot is buried beneath the opaque land fill. */}
            {layers.contours && (
              <g style={{ pointerEvents: "none" }}>
                {contourPaths.map((c) => {
                  const s = contourStyle(c.depth);
                  return (
                    <path
                      key={c.key}
                      d={c.d}
                      fill="none"
                      stroke={s.stroke}
                      strokeWidth={s.width}
                      strokeOpacity={s.opacity}
                      vectorEffect="non-scaling-stroke"
                    />
                  );
                })}
              </g>
            )}

            {/* 4. LAND — the one and only coastline. Opaque tan fill
                covers everything beneath it. */}
            <g style={{ pointerEvents: "none" }}>
              {landPaths.map((c) => (
                <path
                  key={c.key}
                  d={c.d}
                  fill="#eee3c8"
                  stroke="#8a7a56"
                  strokeWidth={1.1}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
            </g>

            {/* 5. MPA regulatory boundaries (opt-in) */}
            {layers.mpa && (
              <g>
                {mpaPaths.map((p) => (
                  <path
                    key={p.key}
                    d={p.d}
                    fill={p.style.fill}
                    stroke={p.style.stroke}
                    strokeWidth={1.2}
                    strokeOpacity="0.85"
                    strokeDasharray="6 3"
                    vectorEffect="non-scaling-stroke"
                  >
                    <title>{p.props?.name} — {p.props?.type}</title>
                  </path>
                ))}
              </g>
            )}

            {/* 6. Kelp canopy — observed forest extent, hatched. Drawn
                above land so shoreline-hugging fronds never get cut. */}
            {layers.kelp && (
              <g>
                {kelpPaths.map((p) => (
                  <path
                    key={p.key}
                    d={p.d}
                    fill={p.surface ? "url(#kelp-canopy-hatch)" : "url(#kelp-subsurface-dots)"}
                    stroke={p.surface ? "#14532d" : "#15803d"}
                    strokeWidth={p.surface ? 1.4 : 1.0}
                    strokeOpacity="0.9"
                    vectorEffect="non-scaling-stroke"
                  >
                    <title>
                      {p.props?.name}
                      {p.props?.className ? ` — ${p.props.className}` : ""}
                      {p.props?.year ? ` (${p.props.year} survey)` : ""}
                    </title>
                  </path>
                ))}
              </g>
            )}

            {/* 7. Soundings — depth numerals in feet, PIP-filtered to
                water. Thinned at low zoom, full density at 4×+. */}
            {layers.soundings && (() => {
              const stride = zoomLevel >= 4 ? 1 : zoomLevel >= 2 ? 2 : 3;
              const fontPx = 9.5 / zoomLevel;
              return (
                <g style={{ pointerEvents: "none" }}>
                  {soundingPts.filter((_, i) => i % stride === 0).map((s) => (
                    <text
                      key={s.key}
                      x={s.x} y={s.y}
                      fill="#27566f"
                      fontSize={fontPx}
                      fontFamily="ui-sans-serif, system-ui, sans-serif"
                      fontWeight={500}
                      textAnchor="middle"
                      dominantBaseline="central"
                    >
                      {s.depth_ft}
                    </text>
                  ))}
                </g>
              );
            })()}

            {/* 8. Landmarks + reef callouts. Category drives styling:
                  dive    — anchor glyph, dark-orange label
                  coastal — small dot, dark label
                  inland  — gray square, gray label (nav reference)
                  marine  — italic blue label (reefs, canyons, banks) */}
            {layers.landmarks && landmarkPts.map((lm) => {
              if (lm.importance === "minor" && zoomLevel < 2) return null;
              if (lm.suppressLow && zoomLevel < 2) return null;
              const fontSize = (lm.importance === "marquee" ? 11.5 : 10) / zoomLevel;
              const weight = lm.importance === "marquee" ? 700 : 600;
              const dx = (lm.flip ? -8 : 8) / zoomLevel;
              const dy = -5 / zoomLevel;
              const anchor = lm.flip ? "end" : "start";
              const isMarine = lm.category === "marine";
              const isDive = lm.category === "dive";
              const isInland = lm.category === "inland";
              const labelFill = isMarine ? "#1e3a8a"
                : isDive ? "#7c2d12"
                : isInland ? "#4b5563"
                : "#1f2937";
              return (
                <g key={lm.key} style={{ pointerEvents: "none" }}>
                  {isDive && (
                    <text
                      x={lm.x} y={lm.y}
                      fill="#c2620a"
                      fontSize={15 / zoomLevel}
                      textAnchor="middle"
                      dominantBaseline="central"
                      stroke="#fffbeb"
                      strokeWidth={(15 / zoomLevel) * 0.16}
                      style={{ paintOrder: "stroke" }}
                    >⚓</text>
                  )}
                  {lm.category === "coastal" && (
                    <circle
                      cx={lm.x} cy={lm.y}
                      r={2.4 / zoomLevel}
                      fill="#1f2937"
                      stroke="#fff"
                      strokeWidth={0.6 / zoomLevel}
                    />
                  )}
                  {isInland && (
                    <rect
                      x={lm.x - 2.4 / zoomLevel}
                      y={lm.y - 2.4 / zoomLevel}
                      width={4.8 / zoomLevel}
                      height={4.8 / zoomLevel}
                      fill="#6b7280"
                      stroke="#fff"
                      strokeWidth={0.6 / zoomLevel}
                    />
                  )}
                  {isMarine && /reef|rock|pinnacle|bank|hole/i.test(lm.name) && (
                    <text
                      x={lm.x} y={lm.y}
                      fill="#1e3a8a"
                      fontSize={11 / zoomLevel}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontWeight={700}
                    >+</text>
                  )}
                  <text
                    x={lm.x + dx} y={lm.y + dy}
                    textAnchor={anchor}
                    fill="#ffffff"
                    stroke="#ffffff"
                    strokeWidth={2.8 / zoomLevel}
                    strokeOpacity="0.9"
                    fontSize={fontSize}
                    fontWeight={weight}
                    fontStyle={isMarine ? "italic" : "normal"}
                    style={{ paintOrder: "stroke" }}
                  >{lm.name}</text>
                  <text
                    x={lm.x + dx} y={lm.y + dy}
                    textAnchor={anchor}
                    fill={labelFill}
                    fontSize={fontSize}
                    fontWeight={weight}
                    fontStyle={isMarine ? "italic" : "normal"}
                  >{lm.name}</text>
                </g>
              );
            })}

            {/* 9. Dropped marks — chart-plotter diamonds with index. */}
            {marks.map((m, i) => {
              const [x, y] = projectInBbox(bbox, m.lng, m.lat, CANVAS_W, CANVAS_H);
              const s = 6 / zoomLevel;
              return (
                <g key={`mark-${i}`} style={{ pointerEvents: "none" }}>
                  <path
                    d={`M${x} ${y - s} L${x + s} ${y} L${x} ${y + s} L${x - s} ${y} Z`}
                    fill="#e879f9"
                    stroke="#86198f"
                    strokeWidth={1.2 / zoomLevel}
                  />
                </g>
              );
            })}

            {/* 10. Cursor crosshair — full-span plotter-style lines. */}
            {cursor && (
              <g style={{ pointerEvents: "none" }}>
                <line
                  x1={0} x2={CANVAS_W}
                  y1={cursor.canvasY} y2={cursor.canvasY}
                  stroke="#0e7490"
                  strokeWidth={0.8}
                  strokeOpacity="0.45"
                  strokeDasharray="5 4"
                  vectorEffect="non-scaling-stroke"
                />
                <line
                  x1={cursor.canvasX} x2={cursor.canvasX}
                  y1={0} y2={CANVAS_H}
                  stroke="#0e7490"
                  strokeWidth={0.8}
                  strokeOpacity="0.45"
                  strokeDasharray="5 4"
                  vectorEffect="non-scaling-stroke"
                />
              </g>
            )}

            {/* 11. Spot centre pin */}
            <g style={{ pointerEvents: "none" }}>
              <circle
                cx={pinXY[0]} cy={pinXY[1]}
                r={10 / zoomLevel}
                fill="#ffffff"
                stroke="#111827"
                strokeWidth={2 / zoomLevel}
              />
              <circle
                cx={pinXY[0]} cy={pinXY[1]}
                r={3.6 / zoomLevel}
                fill="#111827"
              />
            </g>
          </svg>
        )}
      </div>

      {/* Cursor-follow tooltip — GPS-style DDM + decimal + depth */}
      {cursor && cursorPx && (
        <div
          className="spot-detail-cursor-tip"
          style={{ left: `${cursorPx.x + 16}px`, top: `${cursorPx.y + 16}px` }}
        >
          <div className="sdct-coord mono">
            {formatDDM(cursor.lat, "N", "S")}  {formatDDM(cursor.lng, "E", "W")}
          </div>
          <div className="sdct-coord-dec mono">
            {formatDecimal(cursor.lat, cursor.lng)}
          </div>
          <div className="sdct-depth mono">{depthLabel}</div>
        </div>
      )}

      {/* Depth-band legend — bottom centre */}
      {layers.bands && (
        <div className="spot-detail-legend" aria-label="Depth bands in feet">
          <span className="sdl-title">DEPTH&nbsp;FT</span>
          {DEPTH_BANDS_FT.map((b) => (
            <span key={b.label} className="sdl-band">
              <i style={{ background: `rgb(${b.rgb.join(",")})` }} />
              {b.label}
            </span>
          ))}
        </div>
      )}

      {/* Conditions + mark panel — bottom-left */}
      <div className="spot-detail-conditions">
        {waterColumn && (
          <div className="sdc-wc">
            <WaterColumn
              col={waterColumn.col}
              title={waterColumn.title}
              series={waterColumn.series}
              compact
            />
          </div>
        )}
        <div className="sdc-row sdc-coord">
          <span>{cursor ? "Cursor" : "Centre"}</span>
          <strong>
            {formatDDM(cursor?.lat ?? spot.lat, "N", "S")} {formatDDM(cursor?.lng ?? spot.lng, "E", "W")}
          </strong>
        </div>
        <div className="sdc-row sdc-coord">
          <span>Depth</span>
          <strong>{cursor ? depthLabel : (() => {
            const d = depthAt(spot.lng, spot.lat);
            return d && !d.onLand ? `${Math.round(d.depthFt)} ft · ${d.depthM.toFixed(0)} m` : "—";
          })()}</strong>
        </div>
        {lastMark && (
          <div className="sdc-row sdc-mark">
            <span>Mark {marks.length}</span>
            <strong className="mono">{formatDecimal(lastMark.lat, lastMark.lng)}</strong>
            <button
              type="button"
              className="sdc-copy-btn"
              onClick={() => copyCoords(lastMark)}
              title="Copy decimal coordinates for GPS entry"
            >
              {copied ? "✓" : "Copy"}
            </button>
          </div>
        )}
        <div className="sdc-row"><span>SST</span><strong>{conditions.sstStr}</strong></div>
        <div className="sdc-row"><span>Wind</span><strong>{conditions.windStr}</strong></div>
        <div className="sdc-row"><span>Swell</span><strong>{conditions.swellStr}</strong></div>
        <div className="sdc-row"><span>Vis (est.)</span><strong>{conditions.vizStr}</strong></div>
        <div className="sdc-row sdc-faint"><span>Chl</span><strong>{conditions.chlStr}</strong></div>
      </div>

      {/* Layer toggles + view controls — bottom-right */}
      <div className="spot-detail-layers">
        {[
          ["bands",     "Depth tint"],
          ["contours",  "Contours"],
          ["soundings", "Soundings"],
          ["kelp",      "Kelp"],
          ["landmarks", "Labels"],
          ["mpa",       "MPA"],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={"spot-detail-layer-btn" + (layers[key] ? " active" : "")}
            onClick={() => toggleLayer(key)}
            aria-pressed={layers[key]}
          >
            {label}
          </button>
        ))}
        <div className="spot-detail-viewctl">
          <button type="button" className="spot-detail-layer-btn" onClick={() => zoomBy(1 / 1.5)} title="Zoom in">+</button>
          <button type="button" className="spot-detail-layer-btn" onClick={() => zoomBy(1.5)} title="Zoom out">−</button>
        </div>
        <button
          type="button"
          className="spot-detail-layer-btn spot-detail-reset"
          onClick={resetView}
        >
          Reset
        </button>
        {marks.length > 0 && (
          <button
            type="button"
            className="spot-detail-layer-btn spot-detail-reset"
            onClick={() => setMarks([])}
          >
            Clear mark
          </button>
        )}
      </div>

      {/* Sources footer — bathy attribution follows what the bundle
          actually used (NCEI mosaic normally; GMRT only as fallback,
          see pipeline/build_spot_bundles.py). */}
      <div className="spot-detail-footer">
        <span className="spot-detail-sources">
          Bathy: {bundle?.manifest?.sources?.bathy?.includes("NCEI")
            ? "NOAA NCEI DEM mosaic"
            : "GMRT"} · Coast: OSM · Kelp: CDFW 2016 aerial survey · Soundings: DEM-derived
        </span>
        {bundleDate && (
          <span className="spot-detail-fresh mono">chart {bundleDate}</span>
        )}
        <span className="spot-detail-disclaimer">
          Not for navigation. Kelp canopy varies seasonally; verify on site.
        </span>
      </div>
    </div>
  );
}
