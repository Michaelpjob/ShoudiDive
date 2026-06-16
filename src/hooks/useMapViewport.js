// Map viewport state — the SVG viewBox, the container size, the pan
// gesture in-progress flag, and the geometry helpers (clamp + zoom-at).
//
// Extracted from DesktopView in App.jsx (2026-05-23) as part of the
// Stage 3 refactor. Pure mechanical extraction — the resize observer
// + viewport reset effect + clampVb math are byte-for-byte the same
// as the inline versions, just relocated.
//
// What stays in DesktopView:
//   * The gesture event HANDLERS (onMouseDown/Move/Up, onWheel,
//     onTouchStart/Move/End). They consume hook.setVb + hook.clampVb
//     + hook.zoomAt + hook.panStateRef but are themselves tied to JSX
//     event wiring and to DesktopView's own hover/active-spot state,
//     so they're not part of the hook's API surface.
//   * The hover-clear-on-layer-switch effect (it's a hover state
//     concern, not a viewport one).
//
// API:
//   const v = useMapViewport();
//   v.stageRef            — ref to attach to the SVG element
//   v.size                — { w, h } in CSS pixels, updated via ResizeObserver
//   v.vb / v.setVb        — the live viewBox { x, y, w, h } in CSS-pixel coordinates
//   v.isPanning / v.setIsPanning
//   v.panStateRef         — gesture in-progress data (closures want a ref, not state)
//   v.clampVb(next)       — clamp a viewBox to valid pan/zoom range
//   v.zoomAt(x, y, factor) — zoom around a screen-pixel anchor point
//   v.renderVb            — anti-stale viewBox for the actual SVG render path
//   v.zoomLevel           — derived ratio for layer-LOD decisions
//   v.MAX_ZOOM            — exposed constant (test fixtures rely on this)
//
// renderVb explanation: when the window resizes mid-render iOS Safari
// can paint one frame with the OLD viewBox stretched across the NEW
// aspect ratio. The renderVb useMemo silently falls back to the
// "fit-the-stage" viewBox until the state reset above catches up.
// Without this, you'd see a visibly stretched coastline for ~1 frame
// after every orientation flip.

import { useEffect, useMemo, useRef, useState } from "react";

// 2026-05-26: bumped 8 → 16 as Phase 2 (PR-K2-1) of the kelp roadmap.
// At 8× the visible bbox is ~1/8 of the full extent — fine when the
// canonical map content was a 1 km MUR raster (which blurs past 8×
// anyway), but SVG-vector layers (MPA, kelp, bathy, coastline,
// place labels, saved-spot pins) are resolution-independent and
// stay crisp at any zoom. 16× lets divers read individual kelp beds
// and reef names in busy areas like the SF/Monterey kelp forest
// cluster and the Channel Islands. Raster layers still soften past
// 8× — that's a known trade-off the kelp handover called out ("kelp
// stays crisp while SST/chl blur") and the eventual fix is Phase 4
// raster tile-pyramid work, not held against this bump.
// See docs/kelp-roadmap.md § "Phase 2 — Vector fidelity unlock".
const MAX_ZOOM = 16;

export function useMapViewport() {
  const stageRef = useRef(null);
  const [size, setSize] = useState({ w: 1200, h: 700 });

  // Pan/zoom state — viewBox in original svg coords. Initial = full extent.
  const [vb, setVb] = useState({ x: 0, y: 0, w: 1, h: 1 });
  const [isPanning, setIsPanning] = useState(false);
  const panStateRef = useRef(null);

  // Measure the stage on mount + ResizeObserver + window resize +
  // orientation change + iOS visualViewport changes. rAF-debounced
  // so a long resize gesture doesn't trigger one state-update per
  // pixel.
  useEffect(() => {
    let raf = 0;
    function measure() {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        if (!stageRef.current) return;
        const r = stageRef.current.getBoundingClientRect();
        setSize((prev) =>
          Math.abs(prev.w - r.width) < 0.5 && Math.abs(prev.h - r.height) < 0.5
            ? prev
            : { w: r.width, h: r.height }
        );
      });
    }
    measure();
    const ro =
      typeof ResizeObserver !== "undefined" && stageRef.current
        ? new ResizeObserver(measure)
        : null;
    if (ro && stageRef.current) ro.observe(stageRef.current);
    window.addEventListener("resize", measure);
    window.addEventListener("orientationchange", measure);
    window.visualViewport?.addEventListener("resize", measure);
    return () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("orientationchange", measure);
      window.visualViewport?.removeEventListener("resize", measure);
    };
  }, []);

  // Reset / clamp the viewBox whenever the stage size changes.
  useEffect(() => {
    setVb((prev) => {
      // First-time init or after a resize that breaks proportions: reset to fit.
      if (prev.w <= 1 || Math.abs(prev.w / prev.h - size.w / size.h) > 0.001) {
        return { x: 0, y: 0, w: size.w, h: size.h };
      }
      return prev;
    });
  }, [size.w, size.h]);

  function clampVb(next) {
    const w = Math.max(size.w / MAX_ZOOM, Math.min(size.w, next.w));
    const h = w * (size.h / size.w);
    const x = Math.max(0, Math.min(size.w - w, next.x));
    const y = Math.max(0, Math.min(size.h - h, next.y));
    return { x, y, w, h };
  }

  function zoomAt(screenX, screenY, factor) {
    const r = stageRef.current?.getBoundingClientRect();
    if (!r) return;
    const newW = vb.w * factor;
    const cursorVbX = vb.x + (screenX / r.width) * vb.w;
    const cursorVbY = vb.y + (screenY / r.height) * vb.h;
    const newH = newW * (size.h / size.w);
    const newX = cursorVbX - (screenX / r.width) * newW;
    const newY = cursorVbY - (screenY / r.height) * newH;
    setVb(clampVb({ x: newX, y: newY, w: newW, h: newH }));
  }

  // Anti-stale render-time viewBox. When the window resizes mid-render
  // iOS Safari can paint one frame with the OLD viewBox stretched
  // across the NEW aspect ratio. The state reset above catches up on
  // the next commit; this memo silently swaps in the fit-the-stage
  // viewBox for that interim frame.
  const renderVb = useMemo(() => {
    if (!(size.w > 0) || !(size.h > 0)) return vb;
    const ratio = size.w / size.h;
    if (vb.w <= 1 || vb.h <= 1 || Math.abs(vb.w / vb.h - ratio) > 0.001) {
      return { x: 0, y: 0, w: size.w, h: size.h };
    }
    return vb;
  }, [vb, size.w, size.h]);

  // Current zoom factor: ratio of full-extent width to visible viewBox width.
  const zoomLevel = size.w > 0 && renderVb.w > 0 ? size.w / renderVb.w : 1;

  return {
    stageRef,
    size,
    vb, setVb,
    isPanning, setIsPanning,
    panStateRef,
    clampVb,
    zoomAt,
    renderVb,
    zoomLevel,
    MAX_ZOOM,
  };
}
