import { useMemo } from "react";
import { project } from "../lib/mapData.js";

// Renders map labels as constant-size HTML elements positioned over the SVG.
// Why HTML and not SVG: SVG <text> scales with viewBox, so labels balloon as
// the user zooms in. HTML positioned in screen-space stays at a fixed pixel
// size at any zoom. We also do greedy collision detection so labels don't
// stack on each other — higher-priority labels win.
//
// Each label entry:
//   {
//     key:        unique string,
//     lng, lat:   geographic coords,
//     text:       display string,
//     priority:   number (bigger = wins collisions; default 0),
//     fontSize:   px (default 11),
//     color:      CSS color (default var(--ink-2)),
//     weight:     CSS font-weight (default 400),
//     italic:     bool,
//     letterSpacing: CSS string,
//     offsetX, offsetY: extra px nudge from the geographic point,
//     anchor:    'center' | 'left' | 'right' (default 'center'),
//   }

function rectsOverlap(a, b) {
  return !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);
}

export default function MapLabels({ labels, vb, size }) {
  const positioned = useMemo(() => {
    if (!labels || !size?.w || !size?.h) return [];
    const out = [];
    for (const lab of labels) {
      const [vbX, vbY] = project(lab.lng, lab.lat, size.w, size.h);
      // Skip if outside the visible viewBox (with a small margin so a label
      // that's mostly off-screen doesn't pop in/out at the edge).
      const margin = 30;
      if (
        vbX < vb.x - margin ||
        vbX > vb.x + vb.w + margin ||
        vbY < vb.y - margin ||
        vbY > vb.y + vb.h + margin
      ) {
        continue;
      }
      const sx = ((vbX - vb.x) / vb.w) * size.w;
      const sy = ((vbY - vb.y) / vb.h) * size.h;
      const fontSize = lab.fontSize || 11;
      // Roughly estimate width so collision detection knows the label's footprint.
      const charPx = fontSize * 0.55;
      const w = lab.text.length * charPx + 6;
      const h = fontSize + 6;
      const anchor = lab.anchor || "center";
      const offX = lab.offsetX || 0;
      const offY = lab.offsetY != null ? lab.offsetY : -fontSize / 2 - 1;
      let left, top;
      if (anchor === "left") {
        left = sx + offX;
        top = sy + offY;
      } else if (anchor === "right") {
        left = sx + offX - w;
        top = sy + offY;
      } else {
        left = sx + offX - w / 2;
        top = sy + offY;
      }
      const right = left + w;
      const bottom = top + h;
      out.push({
        ...lab,
        sx,
        sy,
        left,
        top,
        right,
        bottom,
        fontSize,
        anchor,
        offX,
        offY,
      });
    }
    return out;
  }, [labels, vb, size]);

  const visible = useMemo(() => {
    const placed = [];
    const sorted = [...positioned].sort(
      (a, b) => (b.priority || 0) - (a.priority || 0)
    );
    for (const p of sorted) {
      const collides = placed.some((q) => rectsOverlap(p, q));
      if (!collides) placed.push(p);
    }
    return placed;
  }, [positioned]);

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        overflow: "hidden",
        zIndex: 12,
        // Stop the user from accidentally text-selecting these while
        // panning or pinch-zooming the map.
        userSelect: "none",
        WebkitUserSelect: "none",
        WebkitTouchCallout: "none",
      }}
    >
      {visible.map((p) => (
        <div
          key={p.key}
          style={{
            position: "absolute",
            left: p.left,
            top: p.top,
            fontSize: p.fontSize,
            color: p.color || "var(--ink-2)",
            fontWeight: p.weight || 400,
            fontStyle: p.italic ? "italic" : "normal",
            fontFamily: "Inter, sans-serif",
            whiteSpace: "nowrap",
            letterSpacing: p.letterSpacing || "0.05em",
            // Triple-stacked text-shadow as a halo against any background.
            textShadow:
              "0 0 3px var(--bg), 0 0 3px var(--bg), 0 0 3px var(--bg), 0 0 3px var(--bg)",
            lineHeight: 1,
            padding: "1px 3px",
          }}
        >
          {p.text}
        </div>
      ))}
    </div>
  );
}
