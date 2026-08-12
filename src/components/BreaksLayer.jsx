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
import { computeBreakMask, BREAK_STRONG_C_PER_KM } from "../lib/sstBreaks.js";

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
      // Catmull-Rom spline through the stem points, emitted as cubic
      // Béziers. The stem is a Douglas-Peucker skeleton — straight L
      // segments between its vertices render as angular pixel-walks
      // ("clunky", user 2026-08-12); the spline turns the same skeleton
      // into the flowing contour a front actually is. Endpoints are
      // interpolated exactly, so the GPS popup's start/end coordinates
      // still sit on the drawn line.
      const pts = f.points.map(([gx, gy]) => [px(gx), py(gy)]);
      let d = `M${pts[0][0].toFixed(2)} ${pts[0][1].toFixed(2)}`;
      for (let i = 0; i < pts.length - 1; i++) {
        const p0 = pts[Math.max(0, i - 1)];
        const p1 = pts[i];
        const p2 = pts[i + 1];
        const p3 = pts[Math.min(pts.length - 1, i + 2)];
        const c1x = p1[0] + (p2[0] - p0[0]) / 6;
        const c1y = p1[1] + (p2[1] - p0[1]) / 6;
        const c2x = p2[0] - (p3[0] - p1[0]) / 6;
        const c2y = p2[1] - (p3[1] - p1[1]) / 6;
        d += `C${c1x.toFixed(2)} ${c1y.toFixed(2)} ${c2x.toFixed(2)} ${c2y.toFixed(2)} ${p2[0].toFixed(2)} ${p2[1].toFixed(2)}`;
      }
      return { idx, d, front: f };
    });
  }, [traced, width, height]);

  if (!paths.length) return null;

  return (
    <g className="breaks-layer">
      {paths.map((p) => {
        const sel = p.idx === selectedIdx;
        // Hard breaks (peak >= the knife-edge bar) draw solid and bold;
        // softer MUR-smeared edges draw thin + dashed. Keeps the majors
        // unmissable without flooding the map at the lower seed.
        const strong = p.front.maxGradient >= BREAK_STRONG_C_PER_KM;
        return (
          <g key={p.idx}>
            {/* white casing under the core = readable on warm reds,
                cold blues, and the land basemap alike. Kept slim — the
                heavy casing is what made v3 read as outlined worms. */}
            <path
              d={p.d}
              fill="none"
              stroke="#ffffff"
              strokeWidth={sel ? 4 : strong ? 2.8 : 2.2}
              strokeOpacity={sel ? 0.9 : strong ? 0.65 : 0.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              pointerEvents="none"
            />
            <path
              d={p.d}
              fill="none"
              stroke={sel ? "#b91c1c" : "#1e293b"}
              strokeWidth={sel ? 2.2 : strong ? 1.6 : 1.2}
              strokeOpacity={sel ? 0.9 : strong ? 0.85 : 0.65}
              strokeLinecap="round"
              strokeLinejoin="round"
              // Soft fronts: round-cap bead dots — quieter than dashes,
              // still clearly "a traced line, lighter than the solid ones".
              strokeDasharray={sel || strong ? "none" : "0.1 5"}
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
