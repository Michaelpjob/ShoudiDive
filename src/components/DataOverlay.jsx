import { useEffect, useRef } from "react";
import {
  BBOX,
  unproject,
  sstColor,
  chlColor,
  sstAt as syntheticSST,
  chlAt as syntheticChl,
} from "../lib/mapData.js";
import {
  getSST,
  getChl,
  getWindSpeed,
  getLayerGrid,
} from "../lib/dataSource.js";

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

  // Two render modes:
  //   1. Real data loaded → render canvas at the source grid's NATIVE resolution
  //      (one canvas pixel per source cell). Browser scales smoothly to viewport
  //      and stays sharp at any zoom. Way faster: ~100 K source cells beats
  //      ~60 K viewport pixels with bilinear-from-source on every sample.
  //   2. Data not loaded yet → fall back to the synthetic field rendered at
  //      0.5× viewport so the prototype look isn't broken on first paint.
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");

    const grid = getLayerGrid(layer, composite);
    if (grid) {
      // Native-grid render — one canvas pixel per source cell. Fall back to
      // the synthetic field at the cell's lat/lng when source data is NaN
      // (typically MUR's land mask or VIIRS chl cells the gap-filler couldn't
      // recover). Without this, the inshore coast goes transparent and the
      // overlay feels patchy in SoCal.
      cv.width = grid.width;
      cv.height = grid.height;
      const img = ctx.createImageData(grid.width, grid.height);
      const lngSpan = BBOX.lngMax - BBOX.lngMin;
      const latSpan = BBOX.latMax - BBOX.latMin;
      for (let i = 0; i < grid.data.length; i++) {
        const v = grid.data[i];
        let rgb;
        if (Number.isFinite(v)) {
          if (layer === "sst") rgb = rgbStrToArr(sstColor(v));
          else if (layer === "chl") rgb = rgbStrToArr(chlColor(v));
          else rgb = windColorRGBArr(v);
        } else {
          // Native grid: row-major. Cell (x, y) → (lng, lat).
          const x = i % grid.width;
          const y = (i / grid.width) | 0;
          const lng = BBOX.lngMin + (x / (grid.width - 1)) * lngSpan;
          const lat = BBOX.latMax - (y / (grid.height - 1)) * latSpan;
          if (layer === "sst") rgb = rgbStrToArr(sstColor(syntheticSST(lng, lat)));
          else if (layer === "chl") rgb = rgbStrToArr(chlColor(syntheticChl(lng, lat)));
          else {
            // Wind has no synthetic; leave transparent.
            img.data[i * 4 + 3] = 0;
            continue;
          }
        }
        img.data[i * 4]     = rgb[0];
        img.data[i * 4 + 1] = rgb[1];
        img.data[i * 4 + 2] = rgb[2];
        img.data[i * 4 + 3] = 255;
      }
      ctx.putImageData(img, 0, 0);
      return;
    }

    // Fallback (data not loaded): synthetic field at 0.5× viewport.
    const scale = 0.5;
    const cw = Math.max(1, Math.floor(width * scale));
    const ch = Math.max(1, Math.floor(height * scale));
    cv.width = cw;
    cv.height = ch;
    const img = ctx.createImageData(cw, ch);
    for (let py = 0; py < ch; py++) {
      for (let px = 0; px < cw; px++) {
        const [lng, lat] = unproject(px / scale, py / scale, width, height);
        let rgb;
        if (layer === "sst") rgb = rgbStrToArr(sstColor(getSST(lng, lat, composite)));
        else if (layer === "chl") rgb = rgbStrToArr(chlColor(getChl(lng, lat, composite)));
        else rgb = windColorRGBArr(getWindSpeed(lng, lat, composite));
        const idx = (py * cw + px) * 4;
        img.data[idx]     = rgb[0];
        img.data[idx + 1] = rgb[1];
        img.data[idx + 2] = rgb[2];
        img.data[idx + 3] = 255;
      }
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
