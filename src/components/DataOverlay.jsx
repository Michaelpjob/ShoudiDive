import { useEffect, useMemo, useRef } from "react";
import {
  unproject,
  sstColor,
  chlColor,
  COASTLINE,
  project,
} from "../lib/mapData.js";
import { getSST, getChl } from "../lib/dataSource.js";

export default function DataOverlay({ width, height, layer, composite, opacity, dataReady }) {
  const canvasRef = useRef(null);

  // Render to an offscreen canvas at reduced resolution; SVG <foreignObject>
  // upscales it for a smooth satellite-like field.
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const scale = 0.25;
    const cw = Math.max(1, Math.floor(width * scale));
    const ch = Math.max(1, Math.floor(height * scale));
    cv.width = cw;
    cv.height = ch;
    const ctx = cv.getContext("2d");
    const img = ctx.createImageData(cw, ch);
    for (let py = 0; py < ch; py++) {
      for (let px = 0; px < cw; px++) {
        const x = px / scale;
        const y = py / scale;
        const [lng, lat] = unproject(x, y, width, height);
        const rgb =
          layer === "sst"
            ? sstColor(getSST(lng, lat, composite))
            : chlColor(getChl(lng, lat, composite));
        const m = rgb.match(/(\d+),\s*(\d+),\s*(\d+)/);
        const r = +m[1], g = +m[2], b = +m[3];
        const idx = (py * cw + px) * 4;
        img.data[idx] = r;
        img.data[idx + 1] = g;
        img.data[idx + 2] = b;
        img.data[idx + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }, [width, height, layer, composite, dataReady]);

  const seaClipPath = useMemo(() => {
    const pts = COASTLINE.map(([lng, lat]) => project(lng, lat, width, height));
    const path = ["M " + pts[0].join(" ")];
    for (let i = 1; i < pts.length; i++) path.push("L " + pts[i].join(" "));
    path.push(`L -40 ${pts[pts.length - 1][1]}`);
    path.push(`L -40 -40`);
    path.push(`L ${pts[0][0]} -40 Z`);
    return path.join(" ");
  }, [width, height]);

  return (
    <g className="data-overlay" opacity={opacity}>
      <defs>
        <clipPath id="oceanMask">
          <path d={seaClipPath} />
        </clipPath>
      </defs>
      <foreignObject
        x="0"
        y="0"
        width={width}
        height={height}
        clipPath="url(#oceanMask)"
      >
        <canvas
          ref={canvasRef}
          style={{
            width: "100%",
            height: "100%",
            imageRendering: "auto",
            filter: "blur(1.5px)",
          }}
        />
      </foreignObject>
    </g>
  );
}
