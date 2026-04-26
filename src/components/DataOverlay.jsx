import { useEffect, useRef } from "react";
import { sstColor, chlColor, getFitted } from "../lib/mapData.js";
import { getLayerGrid } from "../lib/dataSource.js";

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

// Predicted-visibility ramp (Secchi feet → [r,g,b]). Stops sit at the lower
// edge of each category band so cells between bands interpolate smoothly:
// Poor (0–10) → Fair (10–20) → Good (20–30) → Very Good (30–50) → Excellent (50+).
// Very Good moved from emerald to cyan so the gradient reads as a single
// "muddy → clean blue water" arc instead of doubling back through green.
const VIZ_RAMP = [
  { ft: 0,  c: [194, 65, 12]  },   // Poor — burnt orange  #c2410c
  { ft: 10, c: [234, 179, 8]  },   // Fair — yellow         #eab308
  { ft: 20, c: [132, 204, 22] },   // Good — lime           #84cc16
  { ft: 30, c: [6, 182, 212]  },   // Very Good — cyan      #06b6d4
  { ft: 50, c: [3, 105, 161]  },   // Excellent — deep blue #0369a1
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
  const canvasRef = useRef(null);

  // Render the canvas at the source grid's NATIVE resolution — one canvas
  // pixel per source cell. Cells where the satellite didn't capture data
  // (NaN) stay transparent; the no-data hatch below the overlay shows
  // through, so the user sees clearly where coverage is missing rather
  // than fake-smooth synthetic colors.
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");

    const grid = getLayerGrid(layer, composite);
    if (!grid) {
      // No real data loaded for this (layer, window) yet: clear the canvas.
      // The basemap + no-data hatch will be all that's visible.
      cv.width = 1;
      cv.height = 1;
      ctx.clearRect(0, 0, 1, 1);
      return;
    }

    cv.width = grid.width;
    cv.height = grid.height;
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
      else if (layer === "chl") rgb = rgbStrToArr(chlColor(v));
      else if (layer === "wind") rgb = windColorRGBArr(v);
      else if (layer === "swell") rgb = swellColorRGBArr(v);  // Hs in m
      else rgb = vizColorRGBArr(v);  // viz layer (predicted Secchi feet)
      img.data[i * 4]     = rgb[0];
      img.data[i * 4 + 1] = rgb[1];
      img.data[i * 4 + 2] = rgb[2];
      img.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
  }, [width, height, layer, composite, dataReady]);

  // The overlay's pixel grid is a linear lng/lat raster across the bbox, so
  // it has to live inside the same fitted rectangle that project() uses.
  // Otherwise the canvas stretches one way while the coastline geometry
  // stays correctly proportioned and they visibly drift apart.
  const { marginX, marginY, innerW, innerH } = getFitted(width, height);

  return (
    <g className="data-overlay" opacity={opacity}>
      <foreignObject x={marginX} y={marginY} width={innerW} height={innerH}>
        <canvas
          ref={canvasRef}
          style={{
            width: "100%",
            height: "100%",
            imageRendering: "auto",
          }}
        />
      </foreignObject>
    </g>
  );
}
