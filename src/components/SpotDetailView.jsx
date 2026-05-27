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
  if (depth_m <= 5)   return "#0891b2";  // cyan-600
  if (depth_m <= 10)  return "#0e7490";  // cyan-700
  if (depth_m <= 20)  return "#155e75";  // cyan-800
  if (depth_m <= 30)  return "#164e63";  // cyan-900
  if (depth_m <= 50)  return "#1e3a8a";  // blue-900
  if (depth_m <= 100) return "#1e293b";
  return "#0f172a";
}

// Stroke weight by depth — every 10 m gets a heavier line, every
// 50 m the heaviest. Recreational dive depth (≤30 m) and OW limit
// (40 m) are visually obvious.
function contourStrokeWidth(depth_m) {
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
    bathy: true,
    contours: true,
    coastline: true,
    kelp: true,
    mpa: true,
    landmarks: true,
    soundings: true,
  });
  // Cursor lng/lat readout — null when the cursor is off-stage. Updated
  // on mouseMove / touch tap. Lets the user see exact coordinates over
  // any point on the spot detail map (nav-quality QA feedback).
  const [cursor, setCursor] = useState(null);
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
  }
  function onMouseUp() { panStateRef.current = null; }
  function onMouseLeave() {
    panStateRef.current = null;
    setCursor(null);
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
      };
    });
  }, [bundle, bbox]);
  // Depth soundings sampled from the bathy grid. Each carries
  // depth_ft (rounded) and depth_m. Frontend filters by zoom so the
  // overview shows ~30 well-spaced labels and zoom 4× shows all of
  // them (matches a NOAA chart's "more detail when you zoom in"
  // hierarchy). The stride pattern visits every Nth point on each
  // zoom band, which decorrelates which points are shown.
  const soundingPts = useMemo(() => {
    if (!bbox || !bundle?.soundings) return [];
    return (bundle.soundings.features || []).map((f) => {
      const [lng, lat] = f.geometry.coordinates;
      const [x, y] = projectInBbox(bbox, lng, lat, CANVAS_W, CANVAS_H);
      return {
        key: `${lng}-${lat}`,
        x, y,
        depth_ft: f.properties.depth_ft,
      };
    });
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
            {/* 1. Chart-style bathy backdrop. NOAA nautical charts use
                light-cyan water with darker tints at depth — gives
                depth contours, soundings, and aids-to-nav room to read
                as overlays. We lay down a cyan base, then render the
                bathy PNG at reduced opacity for depth gradient context
                (not as the primary depth signal — that's the contours
                + soundings). */}
            <rect x="0" y="0" width={CANVAS_W} height={CANVAS_H} fill="#cfe8f1" />
            {layers.bathy && bundle.bathyUrl && (
              <image
                href={bundle.bathyUrl}
                x="0" y="0"
                width={CANVAS_W} height={CANVAS_H}
                preserveAspectRatio="none"
                opacity="0.40"
                style={{ imageRendering: "auto", mixBlendMode: "multiply" }}
              />
            )}

            {/* 2. Depth contours */}
            {layers.contours && (
              <g className="spot-detail-contours" style={{ pointerEvents: "none" }}>
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

            {/* 5. Kelp polygons */}
            {layers.kelp && (
              <g className="spot-detail-kelp">
                {kelpPaths.map((p) => (
                  <path
                    key={p.key}
                    d={p.d}
                    fill={p.style.fill}
                    stroke={p.style.stroke}
                    strokeWidth={1.4 * strokeScale}
                    strokeOpacity="0.9"
                    vectorEffect="non-scaling-stroke"
                  >
                    <title>{p.props?.name}{p.props?.status ? ` — ${p.props.status}` : ""}</title>
                  </path>
                ))}
              </g>
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

            {/* 5b. Landmark markers + labels (above polygons,
                under the centre pin). Importance gates visibility:
                marquee always shown; major from zoom ≥ 1.5; minor
                from zoom ≥ 3. Marquee landmarks get chart-style
                anchor icons; minors get a simple magenta dot. */}
            {layers.landmarks && landmarkPts.map((lm) => {
              if (lm.importance === "minor" && zoomLevel < 3) return null;
              if (lm.importance === "major" && zoomLevel < 1.5) return null;
              const fontSize = lm.importance === "marquee" ? 11.5 : 10;
              const weight = lm.importance === "marquee" ? 700 : 600;
              const labelDx = 8 / zoomLevel;
              const labelDy = -5 / zoomLevel;
              return (
                <g key={lm.key} style={{ pointerEvents: "none" }}>
                  {lm.importance === "marquee" ? (
                    // Anchor glyph at harbors (NOAA chart symbol).
                    // Single-character render — scaled by zoom.
                    <text
                      x={lm.x} y={lm.y}
                      fill="#9333ea"
                      fontSize={18 / zoomLevel}
                      textAnchor="middle"
                      dominantBaseline="central"
                      stroke="#fff"
                      strokeWidth={(18 / zoomLevel) * 0.12}
                      style={{ paintOrder: "stroke" }}
                    >⚓</text>
                  ) : (
                    <circle
                      cx={lm.x} cy={lm.y}
                      r={3.4 / zoomLevel}
                      fill="#c026d3"
                      stroke="#fff"
                      strokeWidth={0.8 / zoomLevel}
                    />
                  )}
                  {/* Label with halo for legibility on any backdrop */}
                  <text
                    x={lm.x + labelDx}
                    y={lm.y + labelDy}
                    fill="#1e1b4b"
                    stroke="#fff"
                    strokeWidth={2.6 / zoomLevel}
                    strokeOpacity="0.92"
                    fontSize={fontSize / zoomLevel}
                    fontWeight={weight}
                    style={{ paintOrder: "stroke" }}
                  >{lm.name}</text>
                  <text
                    x={lm.x + labelDx}
                    y={lm.y + labelDy}
                    fill="#4a044e"
                    fontSize={fontSize / zoomLevel}
                    fontWeight={weight}
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
