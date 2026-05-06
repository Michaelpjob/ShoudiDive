import { useEffect, useRef, useState } from "react";
import { getCurrentUV, getWindUV } from "../lib/dataSource.js";
import { unproject } from "../lib/mapData.js";
import { buildLandMask, loadLandGeoJSON } from "../lib/landMask.js";


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
    const pixelWidth = Math.max(1, Math.round(width));
    const pixelHeight = Math.max(1, Math.round(height));
    c.width = pixelWidth;
    c.height = pixelHeight;
    const ctx = c.getContext("2d");

    if (!landFeatures) {
      ctx.clearRect(0, 0, pixelWidth, pixelHeight);
      return;
    }

    const landMask = buildLandMask(landFeatures, pixelWidth, pixelHeight);
    if (!landMask) {
      ctx.clearRect(0, 0, pixelWidth, pixelHeight);
      return;
    }

    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const nParticles = reduce ? 0 : (pixelWidth < 600 ? 1200 : 3000);
    const maxAge = 90;

    const isLand = (x, y) => {
      const ix = x | 0;
      const iy = y | 0;
      if (ix < 0 || ix >= pixelWidth || iy < 0 || iy >= pixelHeight) return false;
      return landMask[iy * pixelWidth + ix] === 1;
    };

    // Initialize particle pool. Skip-land at spawn so we never start
    // a streamline halfway through Big Sur.
    const spawn = (p) => {
      let tries = 0;
      do {
        p.x = Math.random() * pixelWidth;
        p.y = Math.random() * pixelHeight;
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
      ctx.fillRect(0, 0, pixelWidth, pixelHeight);
      ctx.globalCompositeOperation = "source-over";

      ctx.lineCap = "round";
      ctx.lineWidth = 1.1;

      // Step each particle
      const speedScale = 0.6; // px per m/s per frame; keeps motion readable
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        const [lng, lat] = unproject(p.x, p.y, pixelWidth, pixelHeight);
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
        if (nx < 0 || nx >= pixelWidth || ny < 0 || ny >= pixelHeight || isLand(nx, ny)) {
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
