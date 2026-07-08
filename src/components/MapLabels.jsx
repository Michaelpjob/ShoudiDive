import { useEffect, useMemo, useState } from "react";
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

// Fixed chrome floating over the map occludes labels rendered under
// it — "Pt. Conception" read as "Pt. Conce" behind the zoom column on
// phones. Measure those rects (stage-relative) so the placement pass
// can flip a label to the other side of its pin, and the collision
// pass can drop any label that still lands underneath. Re-measured on
// size change; the zoom column only moves when the viewport does.
function useChromeObstacles(size) {
  const [obstacles, setObstacles] = useState([]);
  useEffect(() => {
    const stage = document.querySelector(".map-stage");
    if (!stage) { setObstacles([]); return; }
    const s = stage.getBoundingClientRect();
    const out = [];
    for (const sel of [".zoom-ctl"]) {
      const el = document.querySelector(sel);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      out.push({
        left: r.left - s.left,
        top: r.top - s.top,
        right: r.right - s.left,
        bottom: r.bottom - s.top,
      });
    }
    setObstacles(out);
  }, [size]);
  return obstacles;
}

export default function MapLabels({ labels, vb, size }) {
  const obstacles = useChromeObstacles(size);
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
      // Roughly estimate width so collision detection + edge-flip
      // know the label's footprint. 0.62× fontSize is a generous-side
      // estimate for uppercase + letter-spacing + the 3px CSS
      // padding-x; under-counting (was 0.55) caused SAN DIEGO + LA
      // JOLLA to still clip by a few px on a portrait phone after
      // the edge-flip auto-correction.
      const charPx = fontSize * 0.62;
      const w = lab.text.length * charPx + 12;
      const h = fontSize + 6;

      // Auto-flip the anchor near screen edges so a label that would
      // otherwise clip flips to the other side of its pin. Fixes the
      // "60 Mile Co", "Cor", etc. labels getting truncated against the
      // right edge of the map on a portrait phone — saved spots
      // hardcode anchor:'left' but for spots near the east extent
      // (Cortes, Tanner, 60 Mile, Coronados) the text runs off-screen.
      let anchor = lab.anchor || "center";
      let offX = lab.offsetX || 0;
      const offY = lab.offsetY != null ? lab.offsetY : -fontSize / 2 - 1;
      const edgePad = 4;
      if (anchor === "left" && sx + offX + w > size.w - edgePad) {
        anchor = "right";
        offX = -offX;
      } else if (anchor === "right" && sx + offX - w < edgePad) {
        anchor = "left";
        offX = -offX;
      } else if (anchor === "center") {
        // Center labels: nudge horizontally if they'd clip either edge.
        if (sx - w / 2 < edgePad) {
          anchor = "left";
          offX = edgePad - sx;
        } else if (sx + w / 2 > size.w - edgePad) {
          anchor = "right";
          offX = (size.w - edgePad) - sx;
        }
      }

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
      let right = left + w;
      let bottom = top + h;

      // Chrome-occlusion flip: a left-anchored label running under the
      // zoom column (right side of the map) flips to the other side of
      // its pin, same as the screen-edge flip above. Labels that still
      // collide after this get dropped by the obstacle-seeded collision
      // pass below — half-hidden text helps nobody.
      if (anchor === "left" && obstacles.some((o) => rectsOverlap({ left, top, right, bottom }, o))) {
        anchor = "right";
        offX = -(lab.offsetX || 0);
        left = sx + offX - w;
        right = left + w;
        top = sy + offY;
        bottom = top + h;
      }
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
  }, [labels, vb, size, obstacles]);

  const visible = useMemo(() => {
    // Seed with the chrome rects: a label that would render under the
    // zoom column (even after the anchor flip) drops like any other
    // collision loser.
    const placed = [...obstacles];
    const kept = [];
    const sorted = [...positioned].sort(
      (a, b) => (b.priority || 0) - (a.priority || 0)
    );
    for (const p of sorted) {
      const collides = placed.some((q) => rectsOverlap(p, q));
      if (!collides) {
        placed.push(p);
        kept.push(p);
      }
    }
    return kept;
  }, [positioned, obstacles]);

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
