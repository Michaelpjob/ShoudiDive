import { useEffect, useRef } from "react";
import { getWindUV } from "../lib/dataSource.js";
import { unproject } from "../lib/mapData.js";

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

export default function WindParticles({ width, height, composite, dataReady, active }) {
  const canvasRef = useRef(null);
  const rafRef = useRef(0);
  const particlesRef = useRef([]);

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

    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const nParticles = reduce ? 0 : (width < 600 ? 1200 : 3000);
    const maxAge = 90;

    // Initialize particle pool
    const particles = [];
    for (let i = 0; i < nParticles; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        age: Math.floor(Math.random() * maxAge),
        kt: 0,
      });
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
        const { u, v } = getWindUV(lng, lat, composite);
        if (!Number.isFinite(u) || !Number.isFinite(v) || p.age >= maxAge) {
          // Respawn somewhere new.
          p.x = Math.random() * width;
          p.y = Math.random() * height;
          p.age = 0;
          continue;
        }
        const nx = p.x + u * speedScale;
        // Screen y grows downward; v is positive northward = on-screen upward.
        const ny = p.y - v * speedScale;
        if (nx < 0 || nx >= width || ny < 0 || ny >= height) {
          p.x = Math.random() * width;
          p.y = Math.random() * height;
          p.age = 0;
          continue;
        }
        const kt = Math.sqrt(u * u + v * v) * 1.94384;
        const [r, g, b] = windColor(kt);
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
  }, [width, height, composite, dataReady, active]);

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
