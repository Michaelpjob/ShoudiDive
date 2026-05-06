import { useEffect, useRef, useState } from "react";
import { getCurrentUV, getWindUV } from "../lib/dataSource.js";
import { project, unproject, BBOX } from "../lib/mapData.js";


// ---- Coastline land mask ---------------------------------------------------
//
// `getWindUV` does bilinear interpolation over the ~0.16° wind grid,
// which means a cell over land that touches ANY ocean neighbour gets a
// smeared finite UV from the ocean side instead of NaN. So checking
// `Number.isFinite(u, v)` alone doesn't catch land — particles flow
// happily across the entire bbox, including straight over Big Sur and
// the Central Valley.
//
// Fix: pre-render the same coastline polygons LandBasemap uses into an
// offscreen canvas (in foreignObject coordinates), then per-frame
// per-particle do an O(1) pixel lookup. Builds once per (width, height)
// change. Memory: width*height bytes (~270 KB at 393×690 phone size,
// negligible).
//
// `loadLand` is a module-level memoised fetch so subscribing here costs
// no extra network round-trip — Basemap.jsx already pulled it.
let _landPromise = null;
function loadLandGeoJSON() {
  if (_landPromise) return _landPromise;
  _landPromise = fetch("/data/land.geojson")
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return _landPromise;
}


function buildLandMask(features, width, height) {
  // Returns a Uint8Array of length (width * height) where 1 = land,
  // 0 = ocean. The foreignObject's coordinate system maps directly to
  // the fitted bbox area (since App.jsx sizes the foreignObject to
  // f.innerW × f.innerH and translates by f.marginX, f.marginY). So
  // here we use full-stage projection of lng/lat to figure out where
  // the corner of the bbox sits, then translate paths into the local
  // foreignObject space.
  if (!features || width <= 0 || height <= 0) {
    return null;
  }

  // The foreignObject (and therefore THIS canvas) is already the
  // fitted bbox area — `project(lng=lngMin, lat=latMax, width, height)`
  // would give us the position of the NW corner inside the FULL stage.
  // But our canvas IS the fitted area, so within it the NW corner is
  // (0,0) and the SE corner is (width, height). Project lng→x linearly
  // across width, lat→y linearly across height.
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

  // Fill every land polygon. Same data as LandBasemap.
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
      // evenodd so polygon holes (e.g. enclosed lakes) read as ocean.
      ctx.fill("evenodd");
    }
  }

  // Read back. Black pixels (R=0) = land; white (R=255) = ocean.
  const id = ctx.getImageData(0, 0, width, height).data;
  const mask = new Uint8Array(width * height);
  for (let i = 0; i < mask.length; i++) {
    mask[i] = id[i * 4] < 128 ? 1 : 0;
  }
  return mask;
}

// Beaufort-aligned wind ramp (knots → rgb) — matches the legend.
const WIND_STOPS = [
  { t: 0, kt: 0,  c: [230, 240, 250] },
  { t: 5,  kt: 5,  c: [170, 210, 240] },
  { t: 10, kt: 10, c: [120, 200, 160] },
  { t: 15, kt: 15, c: [220, 220, 100] },
  { t: 20, kt: 20, c: [240, 160, 70]  },
  { t: 25, kt: 25, c: [220, 90, 60]   },
  { t: 35, kt: 35, c: [140, 30, 90]   },
];

const CURRENT_STOPS = [
  { kt: 0.0, c: [232, 246, 255] },
  { kt: 0.4, c: [125, 211, 252] },
  { kt: 0.8, c: [94, 234, 212]  },
  { kt: 1.2, c: [250, 204, 21]  },
  { kt: 1.8, c: [249, 115, 22]  },
  { kt: 2.5, c: [220, 38, 38]   },
  { kt: 3.5, c: [126, 34, 206]  },
];

function windColor(kt) {
  for (let i = 0; i < WIND_STOPS.length - 1; i++) {
    const a = WIND_STOPS[i], b = WIND_STOPS[i + 1];
    if (kt >= a.kt && kt <= b.kt) {
      const k = (kt - a.kt) / (b.kt - a.kt);
      return [
        Math.round(a.c[0] + (b.c[0] - a.c[0]) * k),
        Math.round(a.c[1] + (b.c[1] - a.c[1]) * k),
        Math.round(a.c[2] + (b.c[2] - a.c[2]) * k),
      ];
    }
  }
  return WIND_STOPS[WIND_STOPS.length - 1].c;
}

function currentColor(kt) {
  if (!Number.isFinite(kt)) return [220, 220, 220];
  for (let i = 0; i < CURRENT_STOPS.length - 1; i++) {
    const a = CURRENT_STOPS[i], b = CURRENT_STOPS[i + 1];
    if (kt >= a.kt && kt <= b.kt) {
      const k = (kt - a.kt) / (b.kt - a.kt);
      return [
        Math.round(a.c[0] + (b.c[0] - a.c[0]) * k),
        Math.round(a.c[1] + (b.c[1] - a.c[1]) * k),
        Math.round(a.c[2] + (b.c[2] - a.c[2]) * k),
      ];
    }
  }
  return CURRENT_STOPS[CURRENT_STOPS.length - 1].c;
}

export default function WindParticles({ width, height, composite, dataReady, active, vectorLayer = "wind" }) {
  const canvasRef = useRef(null);
  const rafRef = useRef(0);
  const particlesRef = useRef([]);
  const [landFeatures, setLandFeatures] = useState(null);

  // Load the coastline once. Memoised at module level — Basemap.jsx
  // also subscribes, but the promise is shared.
  useEffect(() => {
    let cancelled = false;
    loadLandGeoJSON().then((fc) => {
      if (cancelled || !fc) return;
      setLandFeatures(fc.features);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!active) {
      const c = canvasRef.current;
      if (c) c.getContext("2d").clearRect(0, 0, c.width, c.height);
      cancelAnimationFrame(rafRef.current);
      return;
    }
    const c = canvasRef.current;
    if (!c || width <= 0 || height <= 0) return;
    c.width = width;
    c.height = height;
    const ctx = c.getContext("2d");

    // Land mask in canvas-local pixel space. landMask[y*width + x] = 1
    // if that pixel sits on land, 0 if ocean. Built once when the
    // coastline geojson + dimensions are both ready; null until then,
    // which means the no-land-skip path runs (acceptable for the
    // first 100ms before geojson lands).
    const landMask = buildLandMask(landFeatures, width, height);

    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const nParticles = reduce ? 0 : (width < 600 ? 1200 : 3000);
    const maxAge = 90;

    // Helper: is the canvas pixel (x, y) on land? Returns false when
    // the mask isn't built yet (first ~100 ms before geojson loads),
    // which means particles temporarily flow over land — acceptable
    // since the brief flicker fades within ~50 frames as the mask
    // kicks in and respawn-out-of-land catches up.
    const isLand = (x, y) => {
      if (!landMask) return false;
      const ix = x | 0;
      const iy = y | 0;
      if (ix < 0 || ix >= width || iy < 0 || iy >= height) return false;
      return landMask[iy * width + ix] === 1;
    };

    // Initialize particle pool. Skip-land at spawn so we never start
    // a streamline halfway through Big Sur.
    const spawn = (p) => {
      let tries = 0;
      do {
        p.x = Math.random() * width;
        p.y = Math.random() * height;
        tries++;
      } while (isLand(p.x, p.y) && tries < 8);
      p.age = 0;
    };

    const particles = [];
    for (let i = 0; i < nParticles; i++) {
      const p = { x: 0, y: 0, age: 0, kt: 0 };
      spawn(p);
      p.age = Math.floor(Math.random() * maxAge);
      particles.push(p);
    }
    particlesRef.current = particles;

    function step() {
      // Trail fade — translucent black wash to fade prior frames.
      ctx.globalCompositeOperation = "destination-in";
      ctx.fillStyle = "rgba(0,0,0,0.94)";
      ctx.fillRect(0, 0, width, height);
      ctx.globalCompositeOperation = "source-over";

      ctx.lineCap = "round";
      ctx.lineWidth = 1.1;

      // Step each particle
      const speedScale = 0.6; // px per m/s per frame; keeps motion readable
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        const [lng, lat] = unproject(p.x, p.y, width, height);
        const { u, v } =
          vectorLayer === "current"
            ? getCurrentUV(lng, lat, composite)
            : getWindUV(lng, lat, composite);
        // Respawn if: out of life, no UV (open ocean grid edge), or
        // the particle wandered onto land. The land check defeats the
        // bilinear-smear that otherwise pulls finite UV from ocean
        // neighbours and lets streamlines flow across coast lines.
        if (
          !Number.isFinite(u) || !Number.isFinite(v) ||
          p.age >= maxAge ||
          isLand(p.x, p.y)
        ) {
          spawn(p);
          continue;
        }
        const nx = p.x + u * speedScale;
        // Screen y grows downward; v is positive northward = on-screen upward.
        const ny = p.y - v * speedScale;
        if (nx < 0 || nx >= width || ny < 0 || ny >= height || isLand(nx, ny)) {
          spawn(p);
          continue;
        }
        const kt = Math.sqrt(u * u + v * v) * 1.94384;
        const [r, g, b] = vectorLayer === "current" ? currentColor(kt) : windColor(kt);
        ctx.strokeStyle = `rgba(${r},${g},${b},0.85)`;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(nx, ny);
        ctx.stroke();
        p.x = nx;
        p.y = ny;
        p.age++;
      }
      rafRef.current = requestAnimationFrame(step);
    }
    rafRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafRef.current);
  }, [width, height, composite, dataReady, active, landFeatures, vectorLayer]);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
        display: active ? "block" : "none",
      }}
    />
  );
}
