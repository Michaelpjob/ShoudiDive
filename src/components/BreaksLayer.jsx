// Temperature-break lines as real SVG paths — crisp, styled, and
// clickable. Replaces the v1/v2 canvas-pixel rendering that lived inside
// DataOverlay: vector paths get white casing for contrast on any SST
// color, hit-friendly click targets, and per-front identity for the GPS
// popup. The SST field itself is untouched — this layer only reads the
// grid DataOverlay already renders.
//
// Only draws on the observed "sst" layer (not the sst5d forecast, not
// sst-trend), and computeBreakMask still refuses fallback-source days —
// both honesty rules live in the tracer, not here.

import { useMemo } from "react";
import { getFitted, BBOX } from "../lib/mapData.js";
import { getLayerGrid, isSstSourceFallback } from "../lib/dataSource.js";
import { computeBreakMask } from "../lib/sstBreaks.js";

export default function BreaksLayer({
  width,
  height,
  active,
  layer,
  composite,
  dataReady,
  selectedIdx,
  onSelect,
}) {
  const traced = useMemo(() => {
    if (!active || layer !== "sst") return null;
    const grid = getLayerGrid(layer, composite);
    const res = computeBreakMask(grid, BBOX, {
      sourceFallback: isSstSourceFallback(),
    });
    if (!res) return null;
    return { fronts: res.fronts, gw: res.width, gh: res.height };
    // getLayerGrid reads module state that React can't see — dataReady is
    // the deliberate extra dep that re-runs this when grids finish
    // loading, same contract as DataOverlay's effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, layer, composite, dataReady]);

  const paths = useMemo(() => {
    if (!traced) return [];
    const { marginX, marginY, innerW, innerH } = getFitted(width, height);
    const px = (gx) => marginX + ((gx + 0.5) / traced.gw) * innerW;
    const py = (gy) => marginY + ((gy + 0.5) / traced.gh) * innerH;
    return traced.fronts.map((f, idx) => {
      let d = "";
      for (let i = 0; i < f.points.length; i++) {
        const [gx, gy] = f.points[i];
        d += `${i ? "L" : "M"}${px(gx).toFixed(2)} ${py(gy).toFixed(2)}`;
      }
      return { idx, d, front: f };
    });
  }, [traced, width, height]);

  if (!paths.length) return null;

  return (
    <g className="breaks-layer">
      {paths.map((p) => {
        const sel = p.idx === selectedIdx;
        return (
          <g key={p.idx}>
            {/* white casing under the core = readable on warm reds,
                cold blues, and the land basemap alike */}
            <path
              d={p.d}
              fill="none"
              stroke="#ffffff"
              strokeWidth={sel ? 5 : 3.4}
              strokeOpacity={sel ? 0.95 : 0.75}
              strokeLinecap="round"
              strokeLinejoin="round"
              pointerEvents="none"
            />
            <path
              d={p.d}
              fill="none"
              stroke={sel ? "#b91c1c" : "#0f172a"}
              strokeWidth={sel ? 2.4 : 1.7}
              strokeOpacity="0.9"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeDasharray={sel ? "none" : "7 3"}
              pointerEvents="none"
            />
            {/* fat invisible hit target — a 1.7px line is unclickable,
                especially on phones */}
            <path
              d={p.d}
              fill="none"
              stroke="transparent"
              strokeWidth="16"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ cursor: "pointer", pointerEvents: "stroke" }}
              onMouseDown={(e) => e.stopPropagation()}
              onTouchStart={(e) => e.stopPropagation()}
              onTouchEnd={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onSelect?.(p.idx, p.front, {
                  gw: traced.gw,
                  gh: traced.gh,
                });
              }}
            >
              <title>{`Temperature break · ~${p.front.spanKm} km — click for GPS coordinates`}</title>
            </path>
          </g>
        );
      })}
    </g>
  );
}
