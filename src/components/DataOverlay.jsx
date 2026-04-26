import { useEffect, useRef } from "react";
import { sstColor, chlColor } from "../lib/mapData.js";
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
      else rgb = windColorRGBArr(v);
      img.data[i * 4]     = rgb[0];
      img.data[i * 4 + 1] = rgb[1];
      img.data[i * 4 + 2] = rgb[2];
      img.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
  }, [width, height, layer, composite, dataReady]);

  return (
    <g className="data-overlay" opacity={opacity}>
      <foreignObject x="0" y="0" width={width} height={height}>
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
