import { useEffect, useRef, useState } from "react";
import { sstColor, sstTrendColor, chlColor, getFitted } from "../lib/mapData.js";
import { getLayerGrid } from "../lib/dataSource.js";

// viz renders as discrete per-cell blocks (nearest-neighbour): each cell is its
// own estimate tier and must not visually bleed into its neighbours. Other
// (dense) layers render smooth. chl is neither — see SUPPORTED_GRADIENT_LAYERS.
const PIXELATED_LAYERS = new Set(["viz"]);

// chl renders as a SUPPORTED GRADIENT: a smooth field that exists ONLY where a
// real, fresh satellite observation is nearby, and fades to blank beyond that.
// Ocean-color chl is sparse + coarse (~1-2% of cells are a fresh real retrieval
// on a typical day). Full-map smoothing spreads those few points across the
// whole domain as if we had dense coverage (false confidence); pure dots read
// too stark. The middle: each real observation blooms into a soft blob out to a
// bounded reach (~1 chl correlation length); clusters merge into a continuous
// gradient, isolated obs stay small, and areas with no nearby measurement stay
// transparent. So it's a gradient over what we sampled, honest about the gaps —
// never extrapolated past a real observation. Only confidence==1 cells seed it
// (gap-fill never does). Chosen 2026-07-06 after dots + smooth-veil were both
// off. confidence + values come from loaders/scalarPng.js.
const SUPPORTED_GRADIENT_LAYERS = new Set(["chl"]);
const OBSERVED_CONF = 0.999;   // only real, fresh observations seed the gradient
const GRADIENT_REACH_CELLS = 2.5;  // bloom radius (~32-45 km — within a chl
                                   // mesoscale correlation length; THE knob for
                                   // the fill-vs-honesty balance — bigger fills
                                   // more but extrapolates further from real obs)

// Beaufort-aligned wind ramp (knots → [r,g,b]); same stops as the legend.
const WIND_RAMP = [
  { kt: 0,  c: [230, 240, 250] },
  { kt: 5,  c: [170, 210, 240] },
  { kt: 10, c: [120, 200, 160] },
  { kt: 15, c: [220, 220, 100] },
  { kt: 20, c: [240, 160, 70]  },
  { kt: 25, c: [220, 90, 60]   },
  { kt: 35, c: [140, 30, 90]   },
];

const CURRENT_RAMP = [
  { kt: 0.0, c: [232, 246, 255] },
  { kt: 0.4, c: [125, 211, 252] },
  { kt: 0.8, c: [94, 234, 212]  },
  { kt: 1.2, c: [250, 204, 21]  },
  { kt: 1.8, c: [249, 115, 22]  },
  { kt: 2.5, c: [220, 38, 38]   },
  { kt: 3.5, c: [126, 34, 206]  },
];

// Predicted-visibility ramp (Secchi feet → [r,g,b]). Stops at the lower
// edge of each category bin (Poor 0–10, Fair 10–20, Good 20–30,
// Very Good 30–50, Excellent 50+) so cells between bins interpolate smoothly.
//
// Hybrid semantics — warm "danger" floor for unsafe-to-dive viz, then a
// cool blue ramp climbing from green ("ok, go") through deepening blues
// to deep navy at Excellent. Keeps the at-a-glance traffic-light reading
// (orange = avoid, green = go) that pure mono-blue lost.
const VIZ_RAMP = [
  { ft: 0,  c: [194,  65,  12] },  // Poor       — burnt orange #C2410C
  { ft: 10, c: [34,  197,  94] },  // Fair       — green        #22C55E
  { ft: 20, c: [6,   182, 212] },  // Good       — cyan         #06B6D4
  { ft: 30, c: [3,   105, 161] },  // Very Good  — blue         #0369A1
  { ft: 50, c: [31,   77, 117] },  // Excellent  — deep navy    #1F4D75
];

// Significant wave height ramp (Hs in METRES → rgb). Gradient reads
// "glassy → small → fun → solid → big → don't" — increasing Hs as
// increasing physical risk. Matches the design doc band table.
const SWELL_RAMP_M = [
  { hs: 0.0, c: [236, 254, 255] }, // 0 ft — glassy           #ecfeff
  { hs: 0.3, c: [103, 232, 249] }, // 1 ft — calm              #67e8f9
  { hs: 1.0, c: [132, 204, 22]  }, // 3 ft — workable          #84cc16
  { hs: 1.5, c: [234, 179, 8]   }, // 5 ft — sketchy nearshore #eab308
  { hs: 2.5, c: [249, 115, 22]  }, // 8 ft — big offshore      #f97316
  { hs: 3.7, c: [220, 38, 38]   }, // 12 ft — XL               #dc2626
  { hs: 6.0, c: [127, 29, 29]   }, // 20 ft+ — storm seas      #7f1d1d
];
function windColorRGBArr(kt) {
  if (!Number.isFinite(kt)) return [220, 220, 220];
  for (let i = 0; i < WIND_RAMP.length - 1; i++) {
    const a = WIND_RAMP[i], b = WIND_RAMP[i + 1];
    if (kt >= a.kt && kt <= b.kt) {
      const k = (kt - a.kt) / (b.kt - a.kt);
      return [
        Math.round(a.c[0] + (b.c[0] - a.c[0]) * k),
        Math.round(a.c[1] + (b.c[1] - a.c[1]) * k),
        Math.round(a.c[2] + (b.c[2] - a.c[2]) * k),
      ];
    }
  }
  return WIND_RAMP[WIND_RAMP.length - 1].c;
}

function currentColorRGBArr(kt) {
  if (!Number.isFinite(kt)) return [220, 220, 220];
  if (kt <= CURRENT_RAMP[0].kt) return CURRENT_RAMP[0].c;
  for (let i = 0; i < CURRENT_RAMP.length - 1; i++) {
    const a = CURRENT_RAMP[i], b = CURRENT_RAMP[i + 1];
    if (kt >= a.kt && kt <= b.kt) {
      const k = (kt - a.kt) / (b.kt - a.kt);
      return [
        Math.round(a.c[0] + (b.c[0] - a.c[0]) * k),
        Math.round(a.c[1] + (b.c[1] - a.c[1]) * k),
        Math.round(a.c[2] + (b.c[2] - a.c[2]) * k),
      ];
    }
  }
  return CURRENT_RAMP[CURRENT_RAMP.length - 1].c;
}

function vizColorRGBArr(ft) {
  if (!Number.isFinite(ft)) return [220, 220, 220];
  if (ft <= VIZ_RAMP[0].ft) return VIZ_RAMP[0].c;
  for (let i = 0; i < VIZ_RAMP.length - 1; i++) {
    const a = VIZ_RAMP[i], b = VIZ_RAMP[i + 1];
    if (ft >= a.ft && ft <= b.ft) {
      const k = (ft - a.ft) / (b.ft - a.ft);
      return [
        Math.round(a.c[0] + (b.c[0] - a.c[0]) * k),
        Math.round(a.c[1] + (b.c[1] - a.c[1]) * k),
        Math.round(a.c[2] + (b.c[2] - a.c[2]) * k),
      ];
    }
  }
  return VIZ_RAMP[VIZ_RAMP.length - 1].c;
}

// Hs in metres → rgb. Glassy → storm-seas gradient.
function swellColorRGBArr(hsM) {
  if (!Number.isFinite(hsM)) return [220, 220, 220];
  if (hsM <= SWELL_RAMP_M[0].hs) return SWELL_RAMP_M[0].c;
  for (let i = 0; i < SWELL_RAMP_M.length - 1; i++) {
    const a = SWELL_RAMP_M[i], b = SWELL_RAMP_M[i + 1];
    if (hsM >= a.hs && hsM <= b.hs) {
      const k = (hsM - a.hs) / (b.hs - a.hs);
      return [
        Math.round(a.c[0] + (b.c[0] - a.c[0]) * k),
        Math.round(a.c[1] + (b.c[1] - a.c[1]) * k),
        Math.round(a.c[2] + (b.c[2] - a.c[2]) * k),
      ];
    }
  }
  return SWELL_RAMP_M[SWELL_RAMP_M.length - 1].c;
}

function rgbStrToArr(rgb) {
  const m = rgb.match(/(\d+),\s*(\d+),\s*(\d+)/);
  return m ? [+m[1], +m[2], +m[3]] : [128, 128, 128];
}

export default function DataOverlay({ width, height, layer, composite, opacity, dataReady }) {
  // Why an offscreen canvas piped to an SVG <image> instead of a
  // <foreignObject><canvas/></foreignObject>:
  //
  // iOS Safari has a long-standing bug where foreignObject contents
  // are not transformed by the parent SVG's viewBox during pan/pinch.
  // The map base + pin labels zoom correctly because they're real
  // SVG geometry; the canvas inside foreignObject stayed pinned to
  // screen-pixel coordinates, which is exactly the "overlay doesn't
  // scale with the map" symptom users see on phones.
  //
  // SVG <image> honors viewBox transforms on every browser. We render
  // the grid into an offscreen canvas, encode it to a PNG data URL,
  // and feed that to <image> with preserveAspectRatio="none" so the
  // 1-cell-per-pixel raster stretches exactly to the fitted rectangle.
  const canvasRef = useRef(null);
  if (!canvasRef.current && typeof document !== "undefined") {
    canvasRef.current = document.createElement("canvas");
  }
  const [imgHref, setImgHref] = useState(null);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");

    const grid = getLayerGrid(layer, composite);
    if (!grid) {
      // No real data loaded for this (layer, window) yet — drop the
      // overlay image so the basemap + no-data hatch are all that show.
      setImgHref(null);
      return;
    }

    // Supported-gradient layers (chl): build a smooth field that exists ONLY
    // near a real, fresh observation. Each observation "stamps" a distance-
    // weighted bloom onto the canvas out to GRADIENT_REACH_CELLS; overlapping
    // blooms average (inverse-distance weighted) into a continuous gradient,
    // and alpha fades to 0 at the reach edge. Cells with no observation within
    // reach stay transparent — the gradient never extends past what we
    // measured. Only confidence==1 cells seed it (gap-fill never does).
    if (SUPPORTED_GRADIENT_LAYERS.has(layer)) {
      const conf = grid.confidence;
      const W = grid.width, H = grid.height;
      const R = GRADIENT_REACH_CELLS, Ri = Math.ceil(R);
      const vSum = new Float32Array(W * H);   // Σ w·value
      const wSum = new Float32Array(W * H);   // Σ w
      const nearest = new Float32Array(W * H).fill(Infinity);
      if (conf) {
        for (let i = 0; i < grid.data.length; i++) {
          if (!(conf[i] >= OBSERVED_CONF) || !Number.isFinite(grid.data[i])) continue;
          const ox = i % W, oy = (i / W) | 0, v = grid.data[i];
          for (let dy = -Ri; dy <= Ri; dy++) {
            const y = oy + dy; if (y < 0 || y >= H) continue;
            for (let dx = -Ri; dx <= Ri; dx++) {
              const x = ox + dx; if (x < 0 || x >= W) continue;
              const d = Math.hypot(dx, dy); if (d > R) continue;
              const w = 1 / (d * d + 0.35);   // inverse-distance weight
              const j = y * W + x;
              vSum[j] += w * v; wSum[j] += w;
              if (d < nearest[j]) nearest[j] = d;
            }
          }
        }
      }
      cv.width = W; cv.height = H;
      const gimg = ctx.createImageData(W, H);
      for (let j = 0; j < W * H; j++) {
        if (wSum[j] <= 0) { gimg.data[j * 4 + 3] = 0; continue; }  // no obs in reach
        const rgb = rgbStrToArr(chlColor(vSum[j] / wSum[j]));
        gimg.data[j * 4] = rgb[0];
        gimg.data[j * 4 + 1] = rgb[1];
        gimg.data[j * 4 + 2] = rgb[2];
        // Alpha fades from full at an observation to 0 at the reach edge, so
        // isolated obs are soft blobs and the field dissolves honestly at gaps.
        gimg.data[j * 4 + 3] = Math.round(255 * Math.max(0, 1 - nearest[j] / R));
      }
      ctx.putImageData(gimg, 0, 0);
      try { setImgHref(cv.toDataURL("image/png")); } catch { setImgHref(null); }
      return;
    }

    cv.width = grid.width;
    cv.height = grid.height;
    // Observed-only: a cell either has a real value (paint it, fully opaque)
    // or it's blank (NaN → transparent). The loaders already blanked
    // neighbour-derived cells (chl gap-fill sources, viz estimate tiers) and
    // dropped the fillNearest smear, so there is nothing to fade here — a
    // blank cell is honest "no observation", never backfilled.
    // Per-cell confidence (0..1), when the loader attached one (chl). Encodes
    // trust as opacity: fresh verified obs paint solid, gap-filled/aging cells
    // paint faded. Absent → every finite cell is fully opaque (legacy layers).
    const conf = grid.confidence;
    const img = ctx.createImageData(grid.width, grid.height);
    for (let i = 0; i < grid.data.length; i++) {
      const v = grid.data[i];
      if (!Number.isFinite(v)) {
        // No data at this cell — leave transparent.
        img.data[i * 4 + 3] = 0;
        continue;
      }
      let rgb;
      if (layer === "sst") rgb = rgbStrToArr(sstColor(v));
      else if (layer === "sst5d") rgb = rgbStrToArr(sstColor(v));
      else if (layer === "sst-trend") rgb = rgbStrToArr(sstTrendColor(v));
      else if (layer === "chl") rgb = rgbStrToArr(chlColor(v));
      else if (layer === "wind") rgb = windColorRGBArr(v);
      else if (layer === "current") rgb = currentColorRGBArr(v);
      else if (layer === "swell") rgb = swellColorRGBArr(v);  // Hs in m
      else rgb = vizColorRGBArr(v);  // viz layer (predicted Secchi feet)
      img.data[i * 4]     = rgb[0];
      img.data[i * 4 + 1] = rgb[1];
      img.data[i * 4 + 2] = rgb[2];
      img.data[i * 4 + 3] = conf ? Math.round(255 * conf[i]) : 255;
    }

    // Coastal halo elimination. NaN cells leave alpha=0 but their RGB
    // stays at (0,0,0) from createImageData's default fill. When the
    // browser bilinearly scales this small grid (140×110) up to the
    // viewport (~1000×800), it blends adjacent water-color pixels with
    // those black-RGB cells at every coastline, producing a half-strength
    // brown halo against the dark `--land` basemap. The user saw this
    // as a wide dark glow around the entire Baja peninsula on 2026-05-19.
    //
    // Fix: at each NaN cell, copy RGB from the nearest finite neighbour
    // (alpha stays 0). The browser still alpha-blends to land at the
    // coast, but the colour going INTO the blend is now the local water
    // colour instead of black — clean soft fade, no dark halo.
    //
    // Three 4-connected propagation passes cover any cell within 3 of a
    // finite neighbour. Cells deeper inland stay at RGB=(0,0,0) but
    // they're invisible anyway — LandBasemap paints opaquely over them.
    const W = grid.width, H = grid.height;
    const a = img.data;
    const filled = new Uint8Array(W * H);
    for (let i = 0; i < W * H; i++) filled[i] = a[i * 4 + 3] > 0 ? 1 : 0;
    for (let pass = 0; pass < 3; pass++) {
      let progress = false;
      for (let y = 0; y < H; y++) {
        for (let x = 0; x < W; x++) {
          const c = y * W + x;
          if (filled[c]) continue;
          const nbr = (x > 0 && filled[c - 1])      ? c - 1 :
                      (x < W - 1 && filled[c + 1])  ? c + 1 :
                      (y > 0 && filled[c - W])      ? c - W :
                      (y < H - 1 && filled[c + W])  ? c + W : -1;
          if (nbr < 0) continue;
          const i = c * 4, j = nbr * 4;
          a[i] = a[j]; a[i + 1] = a[j + 1]; a[i + 2] = a[j + 2];
          // alpha stays 0 — we're borrowing colour only.
          filled[c] = 1;
          progress = true;
        }
      }
      if (!progress) break;
    }

    ctx.putImageData(img, 0, 0);

    // toDataURL is synchronous; encoding ~200x200 native cells is
    // sub-50ms on phones, well within the existing per-layer-change
    // budget that already runs the for-loop above.
    try {
      setImgHref(cv.toDataURL("image/png"));
    } catch {
      // Tainted-canvas guard (shouldn't trigger here — we never draw
      // cross-origin imagery — but harden anyway).
      setImgHref(null);
    }
  }, [layer, composite, dataReady]);

  // The overlay's pixel grid is a linear lng/lat raster across the bbox, so
  // it has to live inside the same fitted rectangle that project() uses.
  // Otherwise the canvas stretches one way while the coastline geometry
  // stays correctly proportioned and they visibly drift apart.
  const { marginX, marginY, innerW, innerH } = getFitted(width, height);

  if (!imgHref) return null;

  return (
    <g className="data-overlay" opacity={opacity}>
      <image
        x={marginX}
        y={marginY}
        width={innerW}
        height={innerH}
        href={imgHref}
        preserveAspectRatio="none"
        // viz renders as discrete cells (each cell is its own estimate tier);
        // other layers keep smooth interpolation.
        style={{ imageRendering: PIXELATED_LAYERS.has(layer) ? "pixelated" : "auto" }}
      />
    </g>
  );
}
