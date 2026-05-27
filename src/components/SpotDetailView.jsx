// SpotDetailView — full-screen breakout view of a single saved spot.
//
// Built 2026-05-27 as Phase 1B (Spot Detail) per docs/spot-detail-handover.md.
// Opens when the user clicks "View detailed map" on a saved-spot pin that
// has a pre-computed bundle in public/data/spots/<id>/.
//
// Architecture: independent SVG with its own viewBox keyed to the
// bundle's bbox. Uses projectInBbox (added to mapData.js) so the wide-
// view project() stays locked to the regional BBOX. Pan + pinch-zoom
// reuse the same math as MapShell but stripped to essentials since we
// don't need the iOS Safari anti-stale dance here (the spot view doesn't
// resize mid-render).
//
// Layer z-order, bottom to top:
//   1. Bathy PNG (depth gradient grayscale)
//   2. Depth contour lines (color-graded by depth)
//   3. Coastline polygons (land mask on top of bathy)
//   4. MPA polygons (reuse styleForType)
//   5. Kelp polygons (reuse styleForStatus)
//   6. Centre pin (saved-spot style)

import { useEffect, useMemo, useRef, useState } from "react";
import { projectInBbox } from "../lib/mapData.js";
import { dataPath } from "../lib/region.js";
import { styleForType } from "./MpaLayer.jsx";
import { styleForStatus } from "./KelpLayer.jsx";
import {
  getSST,
  getChl,
  getWindSpeed,
  getWindUV,
  getVizFt,
  getSwell5dStats,
  windCompass,
  windCardinal,
} from "../lib/dataSource.js";
import { usePrefs } from "../contexts/PrefsContext.jsx";
import { track } from "../lib/analytics.js";

// Bundle fetch — single-promise cache per spot id so toggling the
// view open/closed doesn't refetch. Mirrors loadMpaBoundaries.
const bundleCache = new Map();

// Decode the bathy PNG into a Uint8ClampedArray (one channel — the
// PNG is mode='L' grayscale, but canvas always returns RGBA so we
// just read the R channel). Cached per bathy URL so we only load
// + decode once per spot.
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
        // PNG is mode LA (luminance + alpha) — the canvas decodes to
        // RGBA where R=G=B=L and A=A. For cursor depth lookup we want
        // 0 (NaN) where alpha=0, otherwise the L channel value.
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

// Decode an 8-bit bathy pixel to depth_m using the encoding contract:
//   0 = NaN/land
//   1..255 = linear over depth_range_m
function pixelToDepthM(pixel, depthRangeM) {
  if (!pixel) return null;  // 0 = land/NaN
  const [d_min, d_max] = depthRangeM || [0, 500];
  const t = (pixel - 1) / 254;
  return d_min + t * (d_max - d_min);
}

// Point-in-polygon test (ray-casting) for a single Polygon ring.
// Used to filter soundings + bathy clipPath so depth annotations
// don't bleed onto land where GMRT (depth source) and OSM (coastline
// source) disagree about where land is. OSM is the ground truth for
// what's visually "land" — anything inside an OSM coastline polygon
// is land regardless of what GMRT thinks.
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

// Test against a full Polygon (outer + holes) or MultiPolygon.
function pointInGeometry(lng, lat, geom) {
  if (!geom?.coordinates) return false;
  if (geom.type === "Polygon") {
    // First ring is outer boundary, rest are holes — inside outer
    // AND not inside any hole = inside polygon.
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
function loadBundle(spotId) {
  if (bundleCache.has(spotId)) return bundleCache.get(spotId);
  const p = (async () => {
    const base = dataPath(`/data/spots/${spotId}`);
    const manifestRes = await fetch(`${base}/bundle.json`);
    if (!manifestRes.ok) throw new Error(`bundle.json HTTP ${manifestRes.status}`);
    const manifest = await manifestRes.json();
    // Fetch the referenced geojson layers in parallel. Bathy PNG is
    // loaded by the browser via <image href> so it doesn't need an
    // explicit fetch here.
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
    return { manifest, bathyUrl: `${base}/${layers.bathy?.url}`, contours, coastline, kelp, mpa, landmarks, soundings };
  })().catch((err) => {
    bundleCache.delete(spotId);  // allow retry on transient failure
    throw err;
  });
  bundleCache.set(spotId, p);
  return p;
}

// Convert a GeoJSON ring to an SVG path-string projected into the
// bundle bbox + canvas dimensions. Mirrors MpaLayer's ringToPath but
// uses projectInBbox so the math is local to this view.
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

function geometryToPath(geom, bbox, w, h, closed = true) {
  if (!geom) return "";
  const t = geom.type;
  if (t === "Polygon") {
    return geom.coordinates.map((r) => ringToPath(r, bbox, w, h)).join(" ");
  }
  if (t === "MultiPolygon") {
    return geom.coordinates.flatMap((p) => p.map((r) => ringToPath(r, bbox, w, h))).join(" ");
  }
  if (t === "LineString") {
    return lineToPath(geom.coordinates, bbox, w, h, closed);
  }
  if (t === "MultiLineString") {
    return geom.coordinates.map((l) => lineToPath(l, bbox, w, h, closed)).join(" ");
  }
  return "";
}

function lineToPath(line, bbox, w, h, closed) {
  if (!line.length) return "";
  const [x0, y0] = projectInBbox(bbox, line[0][0], line[0][1], w, h);
  let d = `M${x0.toFixed(2)} ${y0.toFixed(2)}`;
  for (let i = 1; i < line.length; i++) {
    const [x, y] = projectInBbox(bbox, line[i][0], line[i][1], w, h);
    d += `L${x.toFixed(2)} ${y.toFixed(2)}`;
  }
  return closed ? d + "Z" : d;
}

// Contour color ramp — nautical-chart style. Real NOAA charts use
// monochrome black-blue contours with cardinal bands ("safety contour"
// at recreational dive depth, etc.). We approximate with a tight
// blue-cyan ramp where everything stays readable against the lighter
// chart-style bathy backdrop.
function contourColor(depth_m) {
  // 2026-05-27: lightened the deep-water palette. La Jolla Canyon
  // hits 300+ m just offshore, and the previous palette mapped that
  // band to #0f172a (slate-900, near-black). Result on the spot
  // detail view: a heavy 1.4 px near-black 200 m contour traced
  // right along the coast and read as a "second coastline" — user
  // QA called it out twice. Capping deep colors at slate-700/sky-800
  // keeps depth visible without ever rendering near-black.
  if (depth_m <= 5)   return "#0891b2";  // cyan-600
  if (depth_m <= 10)  return "#0e7490";  // cyan-700
  if (depth_m <= 20)  return "#155e75";  // cyan-800
  if (depth_m <= 30)  return "#164e63";  // cyan-900
  if (depth_m <= 50)  return "#0c4a6e";  // sky-900
  if (depth_m <= 100) return "#1e3a8a";  // blue-900
  if (depth_m <= 200) return "#1e40af";  // blue-800
  return "#3730a3";                       // indigo-800 (still readable, not black)
}

// Stroke weight by depth — every 10 m gets a heavier line, every
// 50 m the heaviest. Cap deep contours at 0.9 so they never compete
// with the coastline visually.
function contourStrokeWidth(depth_m) {
  if (depth_m > 100) return 0.7;             // deep — light, not heavy
  if (depth_m % 50 === 0) return 1.6;
  if (depth_m === 30)    return 1.4;  // OW dive limit — emphasised
  if (depth_m % 10 === 0) return 1.1;
  return 0.6;
}

const MAX_ZOOM = 16;

function clampVb(vb, baseW, baseH) {
  const minW = baseW / MAX_ZOOM;
  const w = Math.max(minW, Math.min(baseW, vb.w));
  const h = w * (baseH / baseW);
  const x = Math.max(0, Math.min(baseW - w, vb.x));
  const y = Math.max(0, Math.min(baseH - h, vb.y));
  return { x, y, w, h };
}

const CANVAS_W = 960;
const CANVAS_H = 960;

export default function SpotDetailView({ spot, onClose }) {
  const [bundle, setBundle] = useState(null);
  const [error, setError] = useState(null);
  const [layers, setLayers] = useState({
    // 2026-05-27: bathy default OFF. Even with OSM-burn-in mask + SVG
    // clipPath, the bathy PNG's transparency boundary doesn't align
    // exactly with the SVG-rendered tan OSM polygon — rasterized 480
    // × 480 mask against a giant CA-spanning polygon-with-holes
    // doesn't match what SVG's fill-rule="evenodd" interprets. User
    // QA: "you have two coastal views, one is correct the other is
    // an artifact". Depth reads great via contours + soundings +
    // cursor readout, so the bathy heat map is now opt-in via the
    // layer toggle.
    bathy: false,
    contours: true,
    coastline: true,
    kelp: true,
    // MPA default OFF — the rectangular state-regulatory polygons
    // (Matlahuayl SMR, SD-Scripps SMCA, etc.) have axis-aligned
    // boundaries that look like staircased coastlines at deep zoom
    // and visually compete with the actual OSM coastline. Available
    // via the layer toggle when divers want regulatory context.
    mpa: false,
    landmarks: true,
    soundings: true,
  });
  // Cursor lng/lat readout — null when the cursor is off-stage. Updated
  // on mouseMove / touch tap. Lets the user see exact coordinates over
  // any point on the spot detail map (nav-quality QA feedback).
  const [cursor, setCursor] = useState(null);
  // Screen-space cursor position (px from the stage's top-left). Used
  // by the follow-the-cursor tooltip. Kept separate from `cursor`
  // (which is in lng/lat space) because the tooltip layer is rendered
  // outside the SVG — needs screen coords for absolute positioning.
  const [cursorPx, setCursorPx] = useState(null);
  // Decoded bathy pixel grid for cursor depth lookup — populated
  // once per bundle. null until the PNG decodes.
  const [bathyPixels, setBathyPixels] = useState(null);
  const { prefs } = usePrefs();
  const { units } = prefs;

  const stageRef = useRef(null);
  const [vb, setVb] = useState({ x: 0, y: 0, w: CANVAS_W, h: CANVAS_H });
  const panStateRef = useRef(null);
  const pinchStateRef = useRef(null);

  // Load the bundle on mount. Track open/close + layer toggles for
  // analytics (allowlist updated in functions/api/analytics/event.js).
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

  // Escape to close — same affordance as MpaPopup
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Decode the bathy PNG so we can sample depth at the cursor's
  // lng/lat. Runs once per bundle. Width/height come from
  // bundle.manifest.layers.bathy so we don't have to guess.
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

  function toggleLayer(key) {
    setLayers((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      track("spot_detail_layer_toggle", { id: spot.id, layer: key, on: next[key] });
      return next;
    });
  }

  // Pan + zoom handlers — stripped-down version of MapShell's, sized
  // to the spot view's known-static canvas (CANVAS_W × CANVAS_H).
  function onWheel(e) {
    e.preventDefault();
    const r = stageRef.current?.getBoundingClientRect();
    if (!r) return;
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    zoomAt(x, y, e.deltaY < 0 ? 1 / 1.2 : 1.2);
  }
  function zoomAt(screenX, screenY, factor) {
    const r = stageRef.current?.getBoundingClientRect();
    if (!r) return;
    setVb((prev) => {
      const newW = prev.w * factor;
      const cursorVbX = prev.x + (screenX / r.width) * prev.w;
      const cursorVbY = prev.y + (screenY / r.height) * prev.h;
      const newH = newW * (CANVAS_H / CANVAS_W);
      const newX = cursorVbX - (screenX / r.width) * newW;
      const newY = cursorVbY - (screenY / r.height) * newH;
      return clampVb({ x: newX, y: newY, w: newW, h: newH }, CANVAS_W, CANVAS_H);
    });
  }
  function onMouseDown(e) {
    const r = stageRef.current.getBoundingClientRect();
    panStateRef.current = {
      startX: e.clientX - r.left,
      startY: e.clientY - r.top,
      startVb: vb,
    };
  }
  function onMouseMove(e) {
    const r = stageRef.current.getBoundingClientRect();
    const ps = panStateRef.current;
    if (ps) {
      // Active pan — don't track cursor as a readout
      const dx = (e.clientX - r.left - ps.startX) / r.width * ps.startVb.w;
      const dy = (e.clientY - r.top - ps.startY) / r.height * ps.startVb.h;
      setVb(clampVb({
        x: ps.startVb.x - dx,
        y: ps.startVb.y - dy,
        w: ps.startVb.w,
        h: ps.startVb.h,
      }, CANVAS_W, CANVAS_H));
      return;
    }
    // Idle cursor — project screen px through viewBox into bundle
    // bbox lng/lat for the readout. Cheap; runs on every mousemove
    // but the math is just a couple multiplies.
    if (!bbox) return;
    const sx = e.clientX - r.left;
    const sy = e.clientY - r.top;
    const vbX = vb.x + (sx / r.width) * vb.w;
    const vbY = vb.y + (sy / r.height) * vb.h;
    const lng = bbox.lng_min + (vbX / CANVAS_W) * (bbox.lng_max - bbox.lng_min);
    const lat = bbox.lat_max - (vbY / CANVAS_H) * (bbox.lat_max - bbox.lat_min);
    setCursor({ lng, lat });
    setCursorPx({ x: sx, y: sy });
  }
  function onMouseUp() { panStateRef.current = null; }
  function onMouseLeave() {
    panStateRef.current = null;
    setCursor(null);
    setCursorPx(null);
  }

  function onTouchStart(e) {
    const r = stageRef.current.getBoundingClientRect();
    if (e.touches.length === 1) {
      const t = e.touches[0];
      panStateRef.current = {
        startX: t.clientX - r.left,
        startY: t.clientY - r.top,
        startVb: vb,
      };
    } else if (e.touches.length === 2) {
      const a = e.touches[0], b = e.touches[1];
      pinchStateRef.current = {
        cx: (a.clientX + b.clientX) / 2 - r.left,
        cy: (a.clientY + b.clientY) / 2 - r.top,
        startDist: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
        startVb: vb,
      };
    }
  }
  function onTouchMove(e) {
    e.preventDefault();
    const r = stageRef.current.getBoundingClientRect();
    if (e.touches.length === 1 && panStateRef.current) {
      const t = e.touches[0];
      const ps = panStateRef.current;
      const dx = (t.clientX - r.left - ps.startX) / r.width * ps.startVb.w;
      const dy = (t.clientY - r.top - ps.startY) / r.height * ps.startVb.h;
      setVb(clampVb({
        x: ps.startVb.x - dx,
        y: ps.startVb.y - dy,
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
      const cursorVbX = ps.startVb.x + (ps.cx / r.width) * ps.startVb.w;
      const cursorVbY = ps.startVb.y + (ps.cy / r.height) * ps.startVb.h;
      const newX = cursorVbX - (ps.cx / r.width) * newW;
      const newY = cursorVbY - (ps.cy / r.height) * newH;
      setVb(clampVb({ x: newX, y: newY, w: newW, h: newH }, CANVAS_W, CANVAS_H));
    }
  }
  function onTouchEnd(e) {
    if (e.touches.length === 0) {
      // If this was a quick tap (no pan), set cursor to the tap point
      // so the lat/lng readout works on mobile too. We don't have a
      // hover state on touch devices, so this is the only way users
      // see coordinates.
      const ps = panStateRef.current;
      if (ps && bbox) {
        const r = stageRef.current?.getBoundingClientRect();
        if (r) {
          const ct = e.changedTouches[0];
          const sx = (ct?.clientX ?? 0) - r.left;
          const sy = (ct?.clientY ?? 0) - r.top;
          const dragDist = Math.hypot(sx - ps.startX, sy - ps.startY);
          if (dragDist < 8) {
            // Treat as a tap — drop a pin on the cursor readout
            const vbX = vb.x + (sx / r.width) * vb.w;
            const vbY = vb.y + (sy / r.height) * vb.h;
            const lng = bbox.lng_min + (vbX / CANVAS_W) * (bbox.lng_max - bbox.lng_min);
            const lat = bbox.lat_max - (vbY / CANVAS_H) * (bbox.lat_max - bbox.lat_min);
            setCursor({ lng, lat });
          }
        }
      }
      panStateRef.current = null;
      pinchStateRef.current = null;
    } else if (e.touches.length === 1 && pinchStateRef.current) {
      const r = stageRef.current.getBoundingClientRect();
      const t = e.touches[0];
      pinchStateRef.current = null;
      panStateRef.current = {
        startX: t.clientX - r.left,
        startY: t.clientY - r.top,
        startVb: vb,
      };
    }
  }

  function resetView() {
    setVb({ x: 0, y: 0, w: CANVAS_W, h: CANVAS_H });
  }

  // Pre-project the geometry paths once per (bundle, layer set) so pan/
  // zoom doesn't re-walk every ring (viewBox does the zoom for free).
  const bbox = bundle?.manifest?.bbox;
  const coastlinePaths = useMemo(() => {
    if (!bbox || !bundle?.coastline) return [];
    return (bundle.coastline.features || []).map((f, i) => ({
      key: `coast-${i}`,
      d: geometryToPath(f.geometry, bbox, CANVAS_W, CANVAS_H, true),
    }));
  }, [bundle, bbox]);
  const contourPaths = useMemo(() => {
    if (!bbox || !bundle?.contours) return [];
    return (bundle.contours.features || []).map((f, i) => ({
      key: `c-${i}-${f.properties.depth_m}`,
      depth: f.properties.depth_m,
      d: geometryToPath(f.geometry, bbox, CANVAS_W, CANVAS_H, false),
    }));
  }, [bundle, bbox]);
  const mpaPaths = useMemo(() => {
    if (!bbox || !bundle?.mpa) return [];
    return (bundle.mpa.features || []).map((f) => ({
      key: f.properties?.id || `mpa-${Math.random()}`,
      props: f.properties,
      d: geometryToPath(f.geometry, bbox, CANVAS_W, CANVAS_H, true),
      style: styleForType(f.properties?.type),
    }));
  }, [bundle, bbox]);
  const kelpPaths = useMemo(() => {
    if (!bbox || !bundle?.kelp) return [];
    return (bundle.kelp.features || []).map((f) => ({
      key: f.properties?.id || `kelp-${Math.random()}`,
      props: f.properties,
      d: geometryToPath(f.geometry, bbox, CANVAS_W, CANVAS_H, true),
      style: styleForStatus(f.properties?.status),
    }));
  }, [bundle, bbox]);
  // Landmark points in canvas coords, ready for rendering. Importance
  // drives marker + label size at zoom 1× and visibility threshold:
  // 'marquee' always shown; 'major' from zoom ≥ 1.5; 'minor' from ≥ 3.
  // Category drives icon + label styling:
  //   dive    — yellow anchor (key dive sites)
  //   coastal — small dot (beaches, points along shore)
  //   inland  — gray square (nav reference on land)
  //   marine  — italicised label only (named underwater features)
  const landmarkPts = useMemo(() => {
    if (!bbox || !bundle?.landmarks) return [];
    return (bundle.landmarks.features || []).map((f) => {
      const [lng, lat] = f.geometry.coordinates;
      const [x, y] = projectInBbox(bbox, lng, lat, CANVAS_W, CANVAS_H);
      return {
        key: f.properties.name,
        x, y, lng, lat,
        name: f.properties.name,
        importance: f.properties.importance || "minor",
        category: f.properties.category || "coastal",
      };
    });
  }, [bundle, bbox]);
  // Depth soundings sampled from the bathy grid. Each carries
  // depth_ft (rounded) and depth_m. Frontend filters two things:
  //   1. Drop soundings inside any OSM coastline polygon — GMRT
  //      and OSM disagree about where land starts and OSM is the
  //      ground truth for "visually land". This kills the user-QA'd
  //      "depths overrunning onto land" issue.
  //   2. Stride-thin by zoom so overview shows ~30 labels, zoom 4×+
  //      shows them all. NOAA-chart "more detail when zoomed" pattern.
  const soundingPts = useMemo(() => {
    if (!bbox || !bundle?.soundings) return [];
    const landGeoms = (bundle?.coastline?.features || []).map((f) => f.geometry);
    return (bundle.soundings.features || [])
      .map((f) => {
        const [lng, lat] = f.geometry.coordinates;
        // Drop the sounding if it's inside an OSM land polygon
        if (landGeoms.some((g) => pointInGeometry(lng, lat, g))) return null;
        const [x, y] = projectInBbox(bbox, lng, lat, CANVAS_W, CANVAS_H);
        return {
          key: `${lng}-${lat}`,
          x, y,
          depth_ft: f.properties.depth_ft,
        };
      })
      .filter(Boolean);
  }, [bundle, bbox]);

  // Centre pin in canvas coords
  const pinXY = useMemo(() => {
    if (!bbox) return [CANVAS_W / 2, CANVAS_H / 2];
    return projectInBbox(bbox, spot.lng, spot.lat, CANVAS_W, CANVAS_H);
  }, [bbox, spot]);

  // Current zoom level (derived) — drives readability tweaks like
  // stroke scaling on overlays at deep zoom.
  const zoomLevel = CANVAS_W / vb.w;
  const strokeScale = 1 / Math.min(zoomLevel, 4);

  // ---- Conditions readout sampled at the spot centre ----------------------
  // Lightweight read-only summary so divers see "at this point" values
  // without needing the full timeline UX inside this view.
  const conditions = useMemo(() => {
    const sstC = getSST(spot.lng, spot.lat, 1);
    const sstStr = Number.isFinite(sstC)
      ? (units === "F"
          ? `${(sstC * 9 / 5 + 32).toFixed(1)}°F`
          : `${sstC.toFixed(1)}°C`)
      : "—";
    const chlMg = getChl(spot.lng, spot.lat, 1);
    const chlStr = Number.isFinite(chlMg) ? `${chlMg.toFixed(2)} mg/m³` : "—";
    const windKt = getWindSpeed(spot.lng, spot.lat, 1);
    let windStr = "—";
    if (Number.isFinite(windKt)) {
      const { u, v } = getWindUV(spot.lng, spot.lat, 1);
      const dir = Number.isFinite(u) && Number.isFinite(v)
        ? ` ${windCardinal(windCompass(u, v))}`
        : "";
      windStr = `${windKt.toFixed(0)} kt${dir}`;
    }
    const sw = getSwell5dStats(spot.lng, spot.lat, "d0_morning");
    const swellStr = Number.isFinite(sw?.hs)
      ? `${(sw.hs * 3.28084).toFixed(1)} ft${
          Number.isFinite(sw.tp) ? ` · ${sw.tp.toFixed(0)} s` : ""}`
      : "—";
    const vizFt = getVizFt(spot.lng, spot.lat, 1);
    const vizStr = Number.isFinite(vizFt) ? `~${Math.round(vizFt)} ft` : "—";
    return { sstStr, chlStr, windStr, swellStr, vizStr };
  }, [spot, units]);

  const isFresh = bundle?.manifest?.generated_at
    ? new Date(bundle.manifest.generated_at).toISOString().slice(0, 10)
    : null;

  // Cursor depth — sampled from the decoded bathy PNG at the cursor's
  // (lng, lat). Returns { depthM, depthFt, onLand } or null when the
  // PNG isn't loaded yet / cursor is idle / outside the grid.
  function depthAt(lng, lat) {
    if (!bathyPixels || !bbox) return null;
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null;
    // Map (lng, lat) → pixel cell index, north-up.
    const fx = (lng - bbox.lng_min) / (bbox.lng_max - bbox.lng_min);
    const fy = (bbox.lat_max - lat) / (bbox.lat_max - bbox.lat_min);
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return null;
    const { gray, w, h } = bathyPixels;
    const px = Math.max(0, Math.min(w - 1, Math.floor(fx * w)));
    const py = Math.max(0, Math.min(h - 1, Math.floor(fy * h)));
    const pixel = gray[py * w + px];
    if (pixel === 0) return { onLand: true, depthM: null, depthFt: null };
    const dr = bundle?.manifest?.layers?.bathy?.depth_range_m || [0, 500];
    const dM = pixelToDepthM(pixel, dr);
    if (dM == null) return null;
    return { onLand: false, depthM: dM, depthFt: dM * 3.28084 };
  }

  const cursorDepth = cursor
    ? depthAt(cursor.lng, cursor.lat)
    : depthAt(spot.lng, spot.lat);
  const depthLabel = cursorDepth == null
    ? "—"
    : cursorDepth.onLand
      ? "land"
      : `${Math.round(cursorDepth.depthFt)} ft · ${cursorDepth.depthM.toFixed(0)} m`;

  return (
    <div className="spot-detail-overlay" role="dialog" aria-modal="true" aria-label={`${spot.name} detail map`}>
      <div className="spot-detail-header">
        <div className="spot-detail-title">
          <strong>{spot.name}</strong>
          <span className="spot-detail-sub">
            {spot.lat.toFixed(3)}°N {Math.abs(spot.lng).toFixed(3)}°W
            {bundle?.manifest?.bbox &&
              ` · ${Math.round((bundle.manifest.bbox.lat_max - bundle.manifest.bbox.lat_min) * 111)} km square`}
          </span>
        </div>
        <button
          type="button"
          className="spot-detail-close"
          onClick={onClose}
          aria-label="Close detail view"
        >×</button>
      </div>

      <div
        className="spot-detail-stage"
        ref={stageRef}
        onWheel={onWheel}
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
          <div className="spot-detail-loading">Loading bundle…</div>
        )}
        {error && (
          <div className="spot-detail-error">
            Bundle load failed: {error}
          </div>
        )}
        {bundle && (
          <svg
            className="spot-detail-svg"
            viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
            preserveAspectRatio="xMidYMid meet"
          >
            {/* Defs: clipPath that constrains the bathy image to
                OSM-water area only. Built as a single big path with
                the canvas rect (outer) PLUS each land polygon as a
                hole (CCW winding via reverse). evenodd fill-rule
                means a point inside both the canvas rect AND a land
                polygon counts as OUTSIDE the clip — so land cells
                show the cyan background, not bathy. */}
            <defs>
              <clipPath id="spot-water-clip" clipPathUnits="userSpaceOnUse">
                <path
                  fillRule="evenodd"
                  d={`M0 0 H${CANVAS_W} V${CANVAS_H} H0 Z ${coastlinePaths.map((c) => c.d).join(" ")}`}
                />
              </clipPath>
            </defs>

            {/* 1. Chart-style bathy backdrop. NOAA nautical charts use
                light-cyan water with darker tints at depth — gives
                depth contours, soundings, and aids-to-nav room to read
                as overlays. We lay down a cyan base, then render the
                bathy PNG (clipped to OSM water) at reduced opacity. */}
            <rect x="0" y="0" width={CANVAS_W} height={CANVAS_H} fill="#cfe8f1" />
            {layers.bathy && bundle.bathyUrl && (
              <image
                href={bundle.bathyUrl}
                x="0" y="0"
                width={CANVAS_W} height={CANVAS_H}
                preserveAspectRatio="none"
                opacity="0.40"
                clipPath="url(#spot-water-clip)"
                style={{ imageRendering: "auto", mixBlendMode: "multiply" }}
              />
            )}

            {/* 2. Depth contours — clipped to water area for the same
                GMRT-vs-OSM mismatch reason as bathy. */}
            {layers.contours && (
              <g
                className="spot-detail-contours"
                style={{ pointerEvents: "none" }}
                clipPath="url(#spot-water-clip)"
              >
                {contourPaths.map((c) => (
                  <path
                    key={c.key}
                    d={c.d}
                    fill="none"
                    stroke={contourColor(c.depth)}
                    strokeWidth={contourStrokeWidth(c.depth) * strokeScale}
                    strokeOpacity="0.85"
                    vectorEffect="non-scaling-stroke"
                  />
                ))}
              </g>
            )}

            {/* 3. Coastline — chart-style tan land with a thin dark
                outline (matches NOAA chart land tinting). Drawn AFTER
                bathy so it masks the depth gradient cleanly on the
                land side. */}
            {layers.coastline && (
              <g className="spot-detail-coast" style={{ pointerEvents: "none" }}>
                {coastlinePaths.map((c) => (
                  <path
                    key={c.key}
                    d={c.d}
                    fill="#e8d8b8"
                    stroke="#7c6a48"
                    strokeWidth={0.8 * strokeScale}
                    vectorEffect="non-scaling-stroke"
                  />
                ))}
              </g>
            )}

            {/* 4. MPA polygons */}
            {layers.mpa && (
              <g className="spot-detail-mpa">
                {mpaPaths.map((p) => (
                  <path
                    key={p.key}
                    d={p.d}
                    fill={p.style.fill}
                    stroke={p.style.stroke}
                    strokeWidth={1.4 * strokeScale}
                    strokeOpacity="0.9"
                    vectorEffect="non-scaling-stroke"
                  >
                    <title>{p.props?.name} — {p.props?.type}</title>
                  </path>
                ))}
              </g>
            )}

            {/* 5. Kelp polygons — chart-style with diagonal hatch
                fill so surface canopy reads as "actual kelp forest"
                vs. plain green water. Subsurface gets a dot pattern
                to distinguish from surface at a glance. */}
            {layers.kelp && (
              <>
                <defs>
                  <pattern
                    id="kelp-canopy-hatch"
                    width="6" height="6"
                    patternUnits="userSpaceOnUse"
                    patternTransform="rotate(45)"
                  >
                    <rect width="6" height="6" fill="rgba(27, 94, 32, 0.42)" />
                    <line x1="0" y1="0" x2="0" y2="6"
                          stroke="#0a3a14" strokeWidth="1.4" />
                  </pattern>
                  <pattern
                    id="kelp-subsurface-dots"
                    width="5" height="5"
                    patternUnits="userSpaceOnUse"
                  >
                    <rect width="5" height="5" fill="rgba(56, 142, 60, 0.26)" />
                    <circle cx="2.5" cy="2.5" r="0.9" fill="#2e7d32" />
                  </pattern>
                </defs>
                <g className="spot-detail-kelp">
                  {kelpPaths.map((p) => {
                    const isCanopy = (p.props?.className || "").toLowerCase() === "kelp canopy";
                    const isSubsurface = (p.props?.className || "").toLowerCase() === "kelp subsurface";
                    const fill = isCanopy
                      ? "url(#kelp-canopy-hatch)"
                      : isSubsurface
                        ? "url(#kelp-subsurface-dots)"
                        : p.style.fill;
                    const stroke = isCanopy ? "#0a3a14" : p.style.stroke;
                    return (
                      <path
                        key={p.key}
                        d={p.d}
                        fill={fill}
                        stroke={stroke}
                        strokeWidth={2.0 * strokeScale}
                        strokeOpacity="0.95"
                        vectorEffect="non-scaling-stroke"
                      >
                        <title>{p.props?.name}{p.props?.className ? ` — ${p.props.className}` : ""}</title>
                      </path>
                    );
                  })}
                </g>
              </>
            )}

            {/* 5a. Depth soundings — small black numerals at sampled
                grid points showing depth in feet, NOAA chart style.
                Thinned by zoom so the overview shows a clean spread
                (every 3rd sample) and zoom 4×+ shows all of them.
                Zoom-gated label size keeps numbers readable but not
                domineering at any zoom. */}
            {layers.soundings && (() => {
              const stride = zoomLevel >= 4 ? 1 : zoomLevel >= 2 ? 2 : 3;
              const fontPx = 9 / zoomLevel;
              return (
                <g className="spot-detail-soundings" style={{ pointerEvents: "none" }}>
                  {soundingPts.filter((_, i) => i % stride === 0).map((s) => (
                    <text
                      key={s.key}
                      x={s.x} y={s.y}
                      fill="#0b3a52"
                      fontSize={fontPx}
                      fontFamily="ui-sans-serif, system-ui, sans-serif"
                      fontWeight={500}
                      textAnchor="middle"
                      dominantBaseline="central"
                      style={{ paintOrder: "stroke" }}
                      stroke="#cfe8f1"
                      strokeWidth={fontPx * 0.18}
                    >
                      {s.depth_ft}
                    </text>
                  ))}
                </g>
              );
            })()}

            {/* 5b. Landmark markers + labels (NOAA-chart styled).
                Category drives marker + label palette; importance
                gates visibility.
                  dive    — orange anchor + dark-orange label
                  coastal — small dark dot + dark label (shore features)
                  inland  — gray square + gray label (nav reference)
                  marine  — italic blue label only (underwater feature) */}
            {layers.landmarks && landmarkPts.map((lm) => {
              if (lm.importance === "minor" && zoomLevel < 3) return null;
              if (lm.importance === "major" && zoomLevel < 1.5) return null;
              const fontSize = lm.importance === "marquee" ? 11.5 : 10;
              const weight = lm.importance === "marquee" ? 700 : 600;
              const labelDx = 8 / zoomLevel;
              const labelDy = -5 / zoomLevel;
              const isMarine = lm.category === "marine";
              const isDive = lm.category === "dive";
              const isInland = lm.category === "inland";
              const fontStyle = isMarine ? "italic" : "normal";
              const labelFill = isMarine ? "#1e3a8a"
                : isDive ? "#7c2d12"
                : isInland ? "#374151"
                : "#1f2937";
              return (
                <g key={lm.key} style={{ pointerEvents: "none" }}>
                  {/* Marker — depends on category */}
                  {isDive && (
                    // Anchor glyph for dive sites
                    <text
                      x={lm.x} y={lm.y}
                      fill="#d97706"
                      fontSize={16 / zoomLevel}
                      textAnchor="middle"
                      dominantBaseline="central"
                      stroke="#fffbeb"
                      strokeWidth={(16 / zoomLevel) * 0.18}
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
                      x={lm.x - 2.5 / zoomLevel}
                      y={lm.y - 2.5 / zoomLevel}
                      width={5 / zoomLevel}
                      height={5 / zoomLevel}
                      fill="#6b7280"
                      stroke="#fff"
                      strokeWidth={0.6 / zoomLevel}
                    />
                  )}
                  {/* Marine features get no marker — just italic label */}

                  {/* Label with halo for legibility on any backdrop */}
                  <text
                    x={lm.x + labelDx}
                    y={lm.y + labelDy}
                    fill="#ffffff"
                    stroke="#ffffff"
                    strokeWidth={2.8 / zoomLevel}
                    strokeOpacity="0.92"
                    fontSize={fontSize / zoomLevel}
                    fontWeight={weight}
                    fontStyle={fontStyle}
                    style={{ paintOrder: "stroke" }}
                  >{lm.name}</text>
                  <text
                    x={lm.x + labelDx}
                    y={lm.y + labelDy}
                    fill={labelFill}
                    fontSize={fontSize / zoomLevel}
                    fontWeight={weight}
                    fontStyle={fontStyle}
                  >{lm.name}</text>
                </g>
              );
            })}

            {/* 6. Centre pin */}
            <g className="spot-detail-pin">
              <circle
                cx={pinXY[0]} cy={pinXY[1]}
                r={11 / zoomLevel}
                fill="var(--bg-panel-solid, #fff)"
                stroke="var(--ink, #111)"
                strokeWidth={2.2 / zoomLevel}
              />
              <circle
                cx={pinXY[0]} cy={pinXY[1]}
                r={4 / zoomLevel}
                fill="var(--ink, #111)"
              />
            </g>
          </svg>
        )}
      </div>

      {/* Cursor-follow tooltip — sits absolutely positioned just to
          the right of the cursor with the lat/lng + depth at that
          point. Mirrors NOAA-chart-style coordinate readouts. */}
      {cursor && cursorPx && cursorDepth != null && (
        <div
          className="spot-detail-cursor-tip"
          style={{
            left: `${cursorPx.x + 14}px`,
            top: `${cursorPx.y + 14}px`,
          }}
        >
          <div className="sdct-coord mono">
            {cursor.lat.toFixed(4)}°N {Math.abs(cursor.lng).toFixed(4)}°W
          </div>
          <div className="sdct-depth mono">
            {cursorDepth.onLand ? "land" : `${Math.round(cursorDepth.depthFt)} ft · ${cursorDepth.depthM.toFixed(0)} m`}
          </div>
        </div>
      )}

      {/* Floating conditions panel — bottom-left */}
      <div className="spot-detail-conditions">
        {/* Cursor lng/lat readout — visible when the mouse is over
            the map. Falls back to the spot centre when idle so the
            field is always populated (nav-quality QA requirement). */}
        <div className="sdc-row sdc-coord">
          <span>{cursor ? "Cursor" : "Centre"}</span>
          <strong>
            {(cursor?.lat ?? spot.lat).toFixed(4)}°N {Math.abs(cursor?.lng ?? spot.lng).toFixed(4)}°W
          </strong>
        </div>
        {/* Depth at the cursor sampled from the bathy PNG. Falls
            back to "—" until the PNG decodes, "land" when the
            cursor is over a NaN cell (above-water). The two units
            side-by-side because divers think in feet but the data
            is metric. */}
        <div className="sdc-row sdc-coord">
          <span>Depth</span>
          <strong>{depthLabel}</strong>
        </div>
        <div className="sdc-row"><span>SST</span><strong>{conditions.sstStr}</strong></div>
        <div className="sdc-row"><span>Wind</span><strong>{conditions.windStr}</strong></div>
        <div className="sdc-row"><span>Swell</span><strong>{conditions.swellStr}</strong></div>
        <div className="sdc-row"><span>Vis (est.)</span><strong>{conditions.vizStr}</strong></div>
        <div className="sdc-row sdc-faint"><span>Chl</span><strong>{conditions.chlStr}</strong></div>
      </div>

      {/* Floating layer toggles — bottom-right */}
      <div className="spot-detail-layers">
        {[
          ["bathy",     "Bathy"],
          ["contours",  "Contours"],
          ["soundings", "Soundings"],
          ["coastline", "Coast"],
          ["mpa",       "MPA"],
          ["kelp",      "Kelp"],
          ["landmarks", "Labels"],
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
        <button
          type="button"
          className="spot-detail-layer-btn spot-detail-reset"
          onClick={resetView}
          title="Reset zoom"
        >
          Reset
        </button>
      </div>

      {/* Sources footer */}
      <div className="spot-detail-footer">
        {bundle?.manifest?.sources && (
          <span className="spot-detail-sources">
            Sources: {Object.entries(bundle.manifest.sources)
              .filter(([k]) => layers[k] !== false)
              .map(([k, v]) => `${k}: ${v.split(" ")[0]}`)
              .join(" · ")}
          </span>
        )}
        {isFresh && (
          <span className="spot-detail-fresh mono">bundle {isFresh}</span>
        )}
        <span className="spot-detail-disclaimer">
          Kelp polygons are management boundaries — not observed canopy.
        </span>
      </div>
    </div>
  );
}
