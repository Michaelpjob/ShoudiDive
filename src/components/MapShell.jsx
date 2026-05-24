// MapShell — owns the map stage SVG + DataOverlay + all layer overlays,
// renders the desktop panels (saved-spots, controls, legend, hover
// tooltip), and routes to MobileSheet when on mobile.
//
// Was called `DesktopView` and lived inline at the bottom of
// App.jsx (1400+ lines). Renamed + relocated 2026-05-23 as Stage 4
// of the refactor — the old name was a lie (it renders BOTH desktop
// AND mobile UIs). Pure mechanical extraction: every effect, helper,
// and JSX element is byte-equivalent to what App.jsx was rendering
// before; only the file it lives in changed.
//
// Future Stage 4b: split this further into DesktopLayout +
// MobileLayout sub-components so the desktop panel branch and the
// mobile sheet branch stop sharing one ~1400-line body. The hooks
// it uses (useMapViewport, usePopupState) are already extracted so
// that next split is mostly a JSX-relocation job.

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { SeaBasemap, LandBasemap, OceanMaskDefs, PLACE_LABELS } from "./Basemap.jsx";
import DataOverlay from "./DataOverlay.jsx";
import WindParticles from "./WindParticles.jsx";
import MpaLayer from "./MpaLayer.jsx";
import BathyLayer, {
  visibleBathyFeatures,
  bathyLabels,
} from "./BathyLayer.jsx";
import MapLabels from "./MapLabels.jsx";
import MobileSheet from "./MobileSheet.jsx";
import BathyPopup from "./BathyPopup.jsx";
import MpaPopup from "./MpaPopup.jsx";
import CoronadosBanner from "./CoronadosBanner.jsx";
import Tooltip from "./Tooltip.jsx";
import {
  WindCurrentSelectionCard,
  selToSlotKey,
} from "./WindDayGrid.jsx";
import WindTimeline from "./WindTimeline.jsx";
import SwellTimeline, { SwellCurrentCard } from "./SwellTimeline.jsx";
import CurrentTimeline, {
  CurrentCurrentCard,
  currentSelToSlotKey,
} from "./CurrentTimeline.jsx";
import SstTimeline, {
  SstCurrentCard,
  SstModeToggle,
  sstSelToSlotKey,
} from "./SstTimeline.jsx";
import { SstTrendChip, SstSparkline } from "./SstTrendBits.jsx";
import { MoonWidget } from "./MoonIcon.jsx";

import { useMapViewport } from "../hooks/useMapViewport.js";
import { usePopupState } from "../hooks/usePopupState.js";
import { usePrefs } from "../contexts/PrefsContext.jsx";
import {
  project,
  unproject,
  sstColor,
  chlColor,
  getFitted,
  SAVED_SPOTS,
  BBOX,
} from "../lib/mapData.js";
import {
  getSST,
  getChl,
  getWindSpeed,
  getWindUV,
  getCurrentSpeed,
  getCurrentUV,
  getVizFt,
  windCompass,
  windCardinal,
  windSource,
  currentSource,
  dataDates,
  isReal,
  getWind5dSummary,
  getCurrent5dSummary,
  getSwell5dSummary,
  getSwell5dStats,
} from "../lib/dataSource.js";
import { activeRegion } from "../lib/region.js";
import {
  isMapGestureChildTarget,
  shouldPinMapTap,
} from "../lib/mapInteractionGuards.js";
import { track } from "../lib/analytics.js";

// Reactive viewport hook for the bottom-sheet mobile UI.
//
// Treat touch/coarse-pointer devices as mobile at any width, which catches
// large phones and tablets in landscape. For fine-pointer desktop browsers,
// only the truly narrow layout switches to mobile; a windowed laptop browser
// keeps the desktop panels and the original map framing.
const MOBILE_QUERY = "(max-width: 760px), (hover: none) and (pointer: coarse)";
function subscribeMatchMedia(cb) {
  const mql = window.matchMedia(MOBILE_QUERY);
  mql.addEventListener("change", cb);
  return () => mql.removeEventListener("change", cb);
}
function getMobileSnapshot() {
  return window.matchMedia(MOBILE_QUERY).matches;
}
function useIsMobile() {
  return useSyncExternalStore(subscribeMatchMedia, getMobileSnapshot, () => false);
}


function Chevron({ open }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      style={{
        transform: open ? "rotate(0deg)" : "rotate(-90deg)",
        transition: "transform 0.15s",
        color: "var(--ink-3)",
      }}
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

// Time filter is layer-aware: SST/wind/swell use timeline scrubbers, chl
// keeps rolling satellite composites, and viz (predicted) is a single slot.
const TIME_OPTIONS = {
  sst:   { label: "Composite",      helper: "rolling window",      buttons: ["1 Day", "2 Day", "3 Day"],         tags: ["freshest", "balanced", "best cover"] },
  chl:   { label: "Composite",      helper: "rolling window",      buttons: ["1 Day", "2 Day", "3 Day"],         tags: ["freshest", "balanced", "best cover"] },
  wind:  { label: "Forecast Step",  helper: "HRRR + GFS",          buttons: ["Now",   "+6h",   "+24h", "+72h"],  tags: ["analysis", "afternoon", "tomorrow", "3-day"] },
  swell: { label: "Swell forecast", helper: "WaveWatch III · 5d",  buttons: ["Now"],                              tags: ["timeline below"] },
  current: { label: "Surface current", helper: "HFR + tide/wind", buttons: ["Now"], tags: ["timeline below"] },
  viz:   { label: "Visibility forecast", helper: "model output · feet", buttons: ["Now"],                          tags: ["best estimate"] },
};
// Compact value readout for the legend metadata strip when the user is
// hovering over the map. Returns null if the cursor doesn't have data

function hoverReadout(layer, hover, activeComposite, units) {
  if (!hover) return null;
  const { lng, lat } = hover;
  if (layer === "sst") {
    const v = getSST(lng, lat, activeComposite);
    if (!Number.isFinite(v)) return null;
    return units === "F"
      ? `${(v * 9 / 5 + 32).toFixed(1)}°F at cursor`
      : `${v.toFixed(1)}°C at cursor`;
  }
  if (layer === "chl") {
    const v = getChl(lng, lat, activeComposite);
    if (!Number.isFinite(v)) return null;
    return `${v.toFixed(2)} mg/m³ at cursor`;
  }
  if (layer === "wind") {
    const kt = getWindSpeed(lng, lat, activeComposite);
    if (!Number.isFinite(kt)) return null;
    const { u, v } = getWindUV(lng, lat, activeComposite);
    const dirStr =
      Number.isFinite(u) && Number.isFinite(v)
        ? ` ${windCardinal(windCompass(u, v))}`
        : "";
    return `${kt.toFixed(1)} kt${dirStr} at cursor`;
  }
  if (layer === "current") {
    const kt = getCurrentSpeed(lng, lat, activeComposite);
    if (!Number.isFinite(kt)) return null;
    const { u, v } = getCurrentUV(lng, lat, activeComposite);
    const dirStr =
      Number.isFinite(u) && Number.isFinite(v)
        ? ` to ${windCardinal((Math.atan2(u, v) * 180 / Math.PI + 360) % 360)}`
        : "";
    return `${kt.toFixed(1)} kt${dirStr} at cursor`;
  }
  if (layer === "swell") {
    const w = getSwell5dStats(lng, lat, activeComposite);
    if (!Number.isFinite(w.hs)) return null;
    const ft = w.hs * 3.28084;
    const tp = Number.isFinite(w.tp) ? ` · ${w.tp.toFixed(0)} s` : "";
    const dp = Number.isFinite(w.dp) ? ` · ${windCardinal(w.dp)}` : "";
    return `${ft.toFixed(1)} ft${tp}${dp}`;
  }
  if (layer === "viz") {
    const ft = getVizFt(lng, lat, activeComposite);
    if (!Number.isFinite(ft)) return null;
    const cat =
      ft < 10 ? "Poor"
      : ft < 20 ? "Fair"
      : ft < 30 ? "Good"
      : ft < 50 ? "Very Good"
      : "Excellent";
    return `~${Math.round(ft)} ft · ${cat}`;
  }
  return null;
}

function formatWindow(dates, fallback, layer) {
  if (!dates || dates.length === 0) return fallback;
  if (layer === "wind") {
    // dates is a single ISO timestamp like "2026-04-26T06:00:00Z".
    const d = new Date(dates[0]);
    return d.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
      timeZoneName: "short",
    });
  }
  const parse = (iso) => {
    const d = new Date(iso + "T00:00:00Z");
    return {
      month: d.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" }),
      day: d.getUTCDate(),
      year: d.getUTCFullYear(),
    };
  };
  const a = parse(dates[0]);
  const b = parse(dates[dates.length - 1]);
  if (dates.length === 1) return `${a.month} ${a.day}, ${a.year}`;
  if (a.month === b.month && a.year === b.year) return `${a.month} ${a.day}–${b.day}, ${a.year}`;
  return `${a.month} ${a.day} – ${b.month} ${b.day}, ${b.year}`;
}

export default function MapShell({ layer, setLayer, composite, setComposite, sstMode, setSstMode, sstActiveSel, setSstActiveSel, activeSstMode, sstTimelineSummary, hasSstTimeline, windSel, setWindSel, swellSel, setSwellSel, currentSel, setCurrentSel, dataState, viewingDate }) {
  // Prefs (opacity / units / mpaOn / bathyOn) read directly from
  // PrefsContext — extracted in Stage 5c (2026-05-23) so they no
  // longer have to be drilled through App → MapShell as props.
  const { prefs, setPref } = usePrefs();
  const { opacity, units, mpaOn, bathyOn } = prefs;
  const setMpaOn = (v) => setPref("mpaOn", v);
  const setBathyOn = (v) => setPref("bathyOn", v);
  // Timeline layers use a slot-key string derived from their selection
  // state; helpers fall back to a valid slot if the requested one has no
  // data. Chl/viz keep the legacy integer composite.
  //
  // Stage 5b (2026-05-23): activeSstMode + sstActiveSel + sstTimelineSummary
  // + hasSstTimeline now come pre-resolved from useTimelineSelections so
  // we don't recompute them here (and MobileSheet doesn't recompute them
  // either, with subtly different inline logic).
  const activeComposite =
    layer === "sst"   ? (sstTimelineSummary ? sstSelToSlotKey(sstActiveSel, sstTimelineSummary) : composite)
    : layer === "wind"  ? selToSlotKey(windSel,  getWind5dSummary())
    : layer === "swell" ? selToSlotKey(swellSel, getSwell5dSummary())
    : layer === "current" ? currentSelToSlotKey(currentSel, getCurrent5dSummary())
    : composite;
  const isMobile = useIsMobile();

  // Viewport state extracted into useMapViewport (2026-05-23, Stage 3
  // of the refactor). The hook owns the SVG ref + container size +
  // viewBox state + pan-in-progress flag + clampVb/zoomAt geometry
  // helpers + the anti-stale renderVb computation. The gesture event
  // handlers below stay in DesktopView because they're tied to JSX
  // event wiring + DesktopView's hover state.
  const {
    stageRef,
    size,
    vb, setVb,
    isPanning, setIsPanning,
    panStateRef,
    clampVb,
    zoomAt,
    renderVb,
    zoomLevel,
  } = useMapViewport();
  const [hover, setHover] = useState(null);
  const [activeSpot, setActiveSpotRaw] = useState("lajolla");
  // Wrap setActiveSpot so every saved-spot click — desktop list or
  // mobile sheet — fires one analytics event. Answers "which spots
  // do users actually look at, and is it the same on mobile vs
  // desktop?".
  const setActiveSpot = (next) => {
    if (next !== activeSpot) {
      track("spot_click", { from: activeSpot, to: next, layer });
    }
    setActiveSpotRaw(next);
  };
  const [infoOpen, setInfoOpen] = useState(true);
  const [controlsOpen, setControlsOpen] = useState(true);
  const [spotsOpen, setSpotsOpen] = useState(true);
  const [legendOpen, setLegendOpen] = useState(true);
  // MPA/bathy popup state extracted into usePopupState (2026-05-23,
  // Stage 3 of the refactor). The hook owns the selected* state +
  // the toggle-off effects + the bathy lazy-load. See
  // src/hooks/usePopupState.js.
  const {
    selectedMpa, setSelectedMpa,
    selectedBathy, setSelectedBathy,
    bathyFeatures,
  } = usePopupState({ mpaOn, bathyOn });

  // updateMpaOn / updateBathyOn stay here — they wrap mpaOn/bathyOn
  // setters (which are App-level state, not hook-managed) AND
  // synchronously clear the selected popup. The hook's toggle-off
  // effect is the safety-net catch-all for any other path that
  // disables the layer.
  const updateMpaOn = (next) => {
    const value = typeof next === "function" ? next(mpaOn) : next;
    if (!value) setSelectedMpa(null);
    setMpaOn(value);
  };
  const updateBathyOn = (next) => {
    const value = typeof next === "function" ? next(bathyOn) : next;
    if (!value) setSelectedBathy(null);
    setBathyOn(value);
  };

  // Stale hover state from the previous layer carries an incompatible val
  // shape (number for sst/chl, {u,v,kt} object for wind). Drop it on switch.
  useEffect(() => {
    setHover(null);
  }, [layer]);

  function onWheel(e) {
    // No preventDefault — body has overflow:hidden so there's nothing to
    // scroll anyway, and calling it inside React's passive wheel listener
    // just produced an iOS Safari warning without doing useful work.
    const r = stageRef.current.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    zoomAt(x, y, e.deltaY < 0 ? 1 / 1.2 : 1.2);
  }

  function onMouseDown(e) {
    // Same bail as the touch handlers — desktop click-drag inside a
    // child that owns its own gesture shouldn't ALSO start a map pan.
    // (Less critical than touch since mouse drag-on-slider already
    // works, but it eliminates the "pan starts then aborts" flicker
    // when the user clicks the timeline track on a touchpad laptop.)
    if (isMapGestureChildTarget(e.target)) {
      return;
    }
    const r = stageRef.current.getBoundingClientRect();
    panStateRef.current = {
      startScreenX: e.clientX - r.left,
      startScreenY: e.clientY - r.top,
      startVb: vb,
      moved: false,
    };
    setIsPanning(true);
  }

  function onMouseUp() {
    panStateRef.current = null;
    setIsPanning(false);
  }

  function onMove(e) {
    if (isMapGestureChildTarget(e.target)) {
      setHover(null);
      return;
    }
    const r = stageRef.current.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;

    if (panStateRef.current) {
      const ps = panStateRef.current;
      const dxScreen = x - ps.startScreenX;
      const dyScreen = y - ps.startScreenY;
      if (Math.abs(dxScreen) + Math.abs(dyScreen) > 3) ps.moved = true;
      const dxVb = (dxScreen / r.width) * ps.startVb.w;
      const dyVb = (dyScreen / r.height) * ps.startVb.h;
      setVb(clampVb({
        x: ps.startVb.x - dxVb,
        y: ps.startVb.y - dyVb,
        w: ps.startVb.w,
        h: ps.startVb.h,
      }));
      setHover(null);
      return;
    }

    // Hover lookup — convert screen px to vbox coord, then to lng/lat. We
    // intentionally do NOT cache the value here: if the layer changes while
    // hover is still populated, the cached val shape would mismatch the
    // active layer's tooltip code. Tooltip recomputes from lng/lat instead.
    if (isMobile) return;

    const vbX = vb.x + (x / r.width) * vb.w;
    const vbY = vb.y + (y / r.height) * vb.h;
    const [lng, lat] = unproject(vbX, vbY, size.w, size.h);
    setHover({ x, y, lng, lat });
  }
  function onLeave() {
    setHover(null);
    panStateRef.current = null;
    setIsPanning(false);
  }

  function resetView() {
    setVb({ x: 0, y: 0, w: size.w, h: size.h });
  }

  // ---- Touch handlers: 1-finger pan, 2-finger pinch zoom, tap-to-pin ----
  // touchStateRef drives pan/pinch geometry. touchTapRef is a separate
  // tracker for tap-to-pin: phones don't fire mousemove, so without an
  // explicit tap → setHover, mobile users have no way to read the value
  // at a location on the map. A tap is "1 finger, <12 px movement, <350 ms"
  // — anything else is a pan or a pinch.
  const touchStateRef = useRef(null);
  const touchTapRef = useRef(null);

  // Bail out of the map's pan/zoom touch handlers when the finger is
  // on a child that owns its own gesture (the wind/swell timeline,
  // the mobile-shell, panels). Without this, iOS fires BOTH Pointer
  // events (which the timeline uses for scrubbing) AND Touch events
  // (which the map listens to) for the same drag — so dragging the
  // slider also pans the map underneath. The closest-match selector
  // covers every direct-gesture child in one place.
  function isOnGestureChild(e) {
    return isMapGestureChildTarget(e.target);
  }

  function onTouchStart(e) {
    if (isOnGestureChild(e)) return;
    const r = stageRef.current.getBoundingClientRect();
    if (e.touches.length === 1) {
      const t = e.touches[0];
      const startX = t.clientX - r.left;
      const startY = t.clientY - r.top;
      touchStateRef.current = {
        kind: "pan",
        startScreenX: startX,
        startScreenY: startY,
        startVb: vb,
      };
      touchTapRef.current = {
        startX,
        startY,
        startTime: Date.now(),
        moved: false,
      };
      // Don't clear hover on touchstart — keep the prior pin visible until
      // the gesture resolves into a pan (we'll clear on first move) or a
      // tap (we'll re-pin to the new location).
    } else if (e.touches.length === 2) {
      const a = e.touches[0];
      const b = e.touches[1];
      const cx = (a.clientX + b.clientX) / 2 - r.left;
      const cy = (a.clientY + b.clientY) / 2 - r.top;
      touchStateRef.current = {
        kind: "pinch",
        cx,
        cy,
        startDist: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
        startVb: vb,
      };
      touchTapRef.current = null; // 2-finger gesture → never a tap
      setHover(null);
    }
  }

  function onTouchMove(e) {
    // Same gesture-child bail as onTouchStart so a continuing drag
    // that crosses out of the timeline can't suddenly start panning.
    if (isOnGestureChild(e)) return;
    const ts = touchStateRef.current;
    if (!ts) return;
    // touch-action: none on .map-stage already prevents the default page
    // pan/zoom on touch devices, so calling preventDefault() here only
    // tripped iOS Safari's passive-listener warning. Not needed.
    const r = stageRef.current.getBoundingClientRect();
    if (ts.kind === "pan" && e.touches.length === 1) {
      const t = e.touches[0];
      const x = t.clientX - r.left;
      const y = t.clientY - r.top;
      // Promote tap → pan once the finger has wandered. 12 px is enough
      // slack to forgive jittery thumbs but tight enough to feel decisive.
      if (touchTapRef.current) {
        const dx = x - touchTapRef.current.startX;
        const dy = y - touchTapRef.current.startY;
        if (Math.hypot(dx, dy) > 12) {
          touchTapRef.current.moved = true;
          setHover(null);
        }
      }
      const dxScreen = x - ts.startScreenX;
      const dyScreen = y - ts.startScreenY;
      const dxVb = (dxScreen / r.width) * ts.startVb.w;
      const dyVb = (dyScreen / r.height) * ts.startVb.h;
      setVb(clampVb({
        x: ts.startVb.x - dxVb,
        y: ts.startVb.y - dyVb,
        w: ts.startVb.w,
        h: ts.startVb.h,
      }));
    } else if (ts.kind === "pinch" && e.touches.length === 2) {
      const a = e.touches[0];
      const b = e.touches[1];
      const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      if (dist === 0) return;
      const factor = ts.startDist / dist; // pinch-out (dist↑) → vbW↓ → zoom in
      const newW = ts.startVb.w * factor;
      const newH = newW * (size.h / size.w);
      const cursorVbX = ts.startVb.x + (ts.cx / r.width) * ts.startVb.w;
      const cursorVbY = ts.startVb.y + (ts.cy / r.height) * ts.startVb.h;
      const newX = cursorVbX - (ts.cx / r.width) * newW;
      const newY = cursorVbY - (ts.cy / r.height) * newH;
      setVb(clampVb({ x: newX, y: newY, w: newW, h: newH }));
    }
  }

  // Vertical "guard band" at the top of the map where tap-to-pin is
  // suppressed on wind/swell layers. The slider sits at top: 12 with
  // ~56px height (mobile) plus a generous buffer for the moon widget
  // beneath and any near-misses — extending to ~140px feels generous
  // without eating into the actual chart area where users tap.
  // User report 2026-05-04: "the taps around that wind icon wind
  // slider, or swell slider, shouldn't go through". Most useful taps
  // are toward the center of the map, not in the upper strip.
  function onTouchEnd(e) {
    if (e.touches.length === 0) {
      // All fingers up. If the gesture was actually a tap, drop a pin so
      // the value is readable on phones (which have no hover state).
      const tap = touchTapRef.current;
      if (shouldPinMapTap({ tap, layer, hasSstHistory: hasSstTimeline, target: e.target })) {
        const r = stageRef.current.getBoundingClientRect();
        const vbX = vb.x + (tap.startX / r.width) * vb.w;
        const vbY = vb.y + (tap.startY / r.height) * vb.h;
        const [lng, lat] = unproject(vbX, vbY, size.w, size.h);
        setHover({ x: tap.startX, y: tap.startY, lng, lat, pinned: true });
      }
      touchStateRef.current = null;
      touchTapRef.current = null;
    } else if (e.touches.length === 1 && touchStateRef.current?.kind === "pinch") {
      // Released one finger out of a pinch → restart as a pan from the remaining touch.
      const r = stageRef.current.getBoundingClientRect();
      const t = e.touches[0];
      touchStateRef.current = {
        kind: "pan",
        startScreenX: t.clientX - r.left,
        startScreenY: t.clientY - r.top,
        startVb: vb,
      };
      touchTapRef.current = null; // already a multi-touch, never a tap
    }
  }

  const fallbackText =
    layer === "sst"
      ? "latest SST"
      : layer === "wind"
      ? "now"
      : layer === "current"
      ? "surface current"
      : layer === "viz"
      ? "now"
      : composite === 1
      ? "Apr 24, 2026"
      : composite === 2
      ? "Apr 23–24, 2026"
      : "Apr 22–24, 2026";
  const compositeText = formatWindow(dataDates(layer, activeComposite), fallbackText, layer);
  const layerIsReal = isReal(layer, activeComposite);
  const timeOpts = TIME_OPTIONS[layer];

  // Render with a size-matched viewBox immediately after orientation changes.
  // renderVb + zoomLevel are computed inside useMapViewport (the hook
  // returns them from its destructuring above). The anti-stale logic
  // for the iOS Safari mid-resize stretched-coastline case lives in
  // the hook now — see src/hooks/useMapViewport.js.

  // Assemble all labels for the screen-space overlay (constant size + collision).
  const allLabels = useMemo(() => {
    const out = PLACE_LABELS.map((l) => ({ ...l }));
    // Saved spots — always shown, slightly higher priority than place labels.
    for (const s of SAVED_SPOTS) {
      out.push({
        key: "spot-" + s.id,
        lng: s.lng,
        lat: s.lat,
        text: s.name,
        fontSize: 10.5,
        weight: 500,
        color: "var(--ink)",
        priority: s.id === activeSpot ? 80 : 50,
        anchor: "left",
        offsetX: 9,
        offsetY: -3,
      });
    }
    // Bathy labels when the layer is on.
    if (bathyOn && bathyFeatures) {
      const visible = visibleBathyFeatures(bathyFeatures, zoomLevel);
      out.push(...bathyLabels(visible));
    }
    return out;
  }, [activeSpot, bathyOn, bathyFeatures, zoomLevel]);

  return (
    <>
    <div
      className="map-stage"
      ref={stageRef}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      onMouseDown={onMouseDown}
      onMouseUp={onMouseUp}
      onWheel={onWheel}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
      onTouchCancel={onTouchEnd}
      style={{
        cursor: isPanning ? "grabbing" : "grab",
        touchAction: "none",
        // Pinch-zoom + panning kept dragging text selection across the map
        // labels, leaving them highlighted blue. Lock selection on the whole
        // stage; tooltips/panels above this stay selectable normally.
        userSelect: "none",
        WebkitUserSelect: "none",
        WebkitTouchCallout: "none",
      }}
    >
      <svg
        className="map-svg"
        viewBox={`${renderVb.x} ${renderVb.y} ${renderVb.w} ${renderVb.h}`}
        preserveAspectRatio="none"
      >
        <defs>
          {/* Diagonal hatch shown wherever the satellite didn't capture
              data for the active layer. Sits under the data overlay; the
              overlay's transparent (NaN) cells let it show through. */}
          <pattern
            id="noDataHatch"
            width="9"
            height="9"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <line
              x1="0"
              y1="0"
              x2="0"
              y2="9"
              stroke="var(--ink-3)"
              strokeWidth="2.5"
              strokeOpacity="0.32"
            />
          </pattern>
          <OceanMaskDefs width={size.w} height={size.h} />
        </defs>

        {/* Sea + graticule under everything (full-bleed: open ocean fills
            the pillarbox/letterbox margins as well, which reads correctly). */}
        <SeaBasemap width={size.w} height={size.h} />

        {(() => {
          // Letterbox/pillarbox the geographic content so x and y always
          // represent the same on-the-ground distance regardless of the
          // window aspect ratio. The data overlay, no-data hatch, and wind
          // particles all live in this fitted rect; coastlines + spot pins
          // come from project() which already uses it.
          const f = getFitted(size.w, size.h);
          return (
            <g clipPath="url(#ocean-clip)" mask="url(#ocean-mask)">
              {/* No-data hatch — only inside the bbox area; outside is just sea.
                  Suppressed for non-CA regions today because PNW + tropical
                  have legitimately-sparse coverage (HFRNet has no Caribbean,
                  rivers/tides are CA-station-only, etc.) and the hatch
                  dominated the visible map. CA still gets it as a coverage
                  indicator until the beta regions reach feature parity. */}
              {activeRegion() === "ca" && (
                <rect
                  x={f.marginX}
                  y={f.marginY}
                  width={f.innerW}
                  height={f.innerH}
                  fill="url(#noDataHatch)"
                  pointerEvents="none"
                />
              )}

              {/* Data overlay (positions itself inside the fitted box). */}
              <DataOverlay
                width={size.w}
                height={size.h}
                layer={layer}
                composite={activeComposite}
                opacity={opacity}
                dataReady={dataState?.ready}
              />

              {/* Wind particles used to live here as a foreignObject child
                  of the SVG. Pulled out — see the WindParticlesHost block
                  after </svg> below. iOS Safari doesn't apply the SVG
                  viewBox transform to foreignObject contents, which made
                  the streamlines stay locked to screen pixels while the
                  rest of the map zoomed. */}
            </g>
          );
        })()}

        {/* Real coastline + islands on top of data — naturally masks the overlay */}
        <LandBasemap width={size.w} height={size.h} />

        <MpaLayer
          width={size.w}
          height={size.h}
          active={mpaOn}
          onSelect={(mpa) => {
            track("popup_open", { kind: "mpa", type: mpa?.type || "unknown" });
            setSelectedMpa(mpa);
          }}
        />

        <BathyLayer
          width={size.w}
          height={size.h}
          active={bathyOn}
          zoomLevel={zoomLevel}
          onSelect={(feat) => {
            track("popup_open", { kind: "bathy", class: feat?.class || "unknown" });
            setSelectedBathy(feat);
          }}
        />

        <g className="spot-pins">
          {SAVED_SPOTS.map((s) => {
            const [x, y] = project(s.lng, s.lat, size.w, size.h);
            const isActive = s.id === activeSpot;
            // SVG <circle> radius scales with viewBox, so a 7-unit pin
            // becomes ~56 px wide at 8× zoom. Divide by zoomLevel so the
            // pin stays the same on-screen size regardless of zoom.
            // (vector-effect: non-scaling-stroke handles the outline width.)
            const r = (isActive ? 11 : 7) / zoomLevel;
            const inner = 4 / zoomLevel;
            return (
              <g
                key={s.id}
                style={{ cursor: "pointer" }}
                onClick={() => setActiveSpot(s.id)}
              >
                <circle
                  cx={x}
                  cy={y}
                  r={r}
                  fill="var(--bg-panel-solid)"
                  stroke="var(--ink)"
                  strokeWidth={isActive ? 2.2 : 1.4}
                />
                {isActive && <circle cx={x} cy={y} r={inner} fill="var(--ink)" />}
              </g>
            );
          })}
        </g>
      </svg>

      {/* Wind particles — moved out of the SVG (used to be a
          foreignObject inside .map-svg, but iOS Safari doesn't honor the
          parent SVG's viewBox transform on foreignObject contents).
          Now an HTML canvas at fixed pixel size = full-extent fitted
          box, with a CSS transform that mirrors the current viewBox so
          the streamlines pan/zoom with the basemap. The land-mask
          respawn check inside WindParticles (already there) keeps
          streamlines from visibly crossing land now that the SVG land
          basemap no longer naturally occludes them. */}
      {(layer === "wind" || layer === "current") && (() => {
        const f = getFitted(size.w, size.h);
        const cssLeft = ((f.marginX - renderVb.x) / renderVb.w) * size.w;
        const cssTop  = ((f.marginY - renderVb.y) / renderVb.h) * size.h;
        const scaleX = size.w / renderVb.w;
        const scaleY = size.h / renderVb.h;
        return (
          <div
            className="wind-particles-host"
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              width: f.innerW,
              height: f.innerH,
              transform: `translate(${cssLeft}px, ${cssTop}px) scale(${scaleX}, ${scaleY})`,
              transformOrigin: "0 0",
              pointerEvents: "none",
              // Avoid blurry rasterized scaling on retina; let the
              // browser composite at the new scale natively.
              willChange: "transform",
            }}
          >
            <WindParticles
              width={f.innerW}
              height={f.innerH}
              composite={activeComposite}
              dataReady={dataState?.ready}
              vectorLayer={layer === "current" ? "current" : "wind"}
              active
            />
          </div>
        );
      })()}

      <MapLabels labels={allLabels} vb={renderVb} size={size} />

      {/* Moon-phase legend — anchored top-right of the map. On wind/swell
          layers it tracks the time slider (the parent passes the active
          slot's anchor as viewingDate); other layers show "now".
          On mobile, wind/swell also have a top-center timeline; the
          .below-timeline class shifts the moon down to clear it. */}
      <MoonWidget
        date={viewingDate}
        className={((layer === "sst" && hasSstTimeline) || layer === "wind" || layer === "swell" || layer === "current") ? "below-timeline" : ""}
      />

      {/* Timeline scrubbers. Wind/swell/current are forecasts; SST can show
          observed history or the beta forecast. The map heatmap updates on
          every drag tick. */}
      {layer === "sst" && hasSstTimeline && (
        <SstTimeline sel={sstActiveSel} setSel={setSstActiveSel} units={units} mode={activeSstMode} />
      )}
      {layer === "wind" && (
        <WindTimeline sel={windSel} setSel={setWindSel} />
      )}
      {layer === "swell" && (
        <SwellTimeline sel={swellSel} setSel={setSwellSel} />
      )}
      {layer === "current" && (
        <CurrentTimeline sel={currentSel} setSel={setCurrentSel} />
      )}

      {!isMobile && hover && (
        <Tooltip
          x={hover.x}
          y={hover.y}
          layer={layer}
          composite={activeComposite}
          lng={hover.lng}
          lat={hover.lat}
          units={units}
        />
      )}

      <div className={"panel controls-tl" + (controlsOpen ? "" : " collapsed")}>
        <div
          className="panel-header"
          style={{ cursor: "pointer", userSelect: "none" }}
          onClick={() => setControlsOpen((v) => !v)}
        >
          <span className="panel-title">Layer</span>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <button
              type="button"
              className={"mpa-pill" + (mpaOn ? " active" : "")}
              onClick={(e) => { e.stopPropagation(); updateMpaOn(!mpaOn); }}
              title={mpaOn ? "MPAs visible · click to hide" : "MPAs hidden · click to show"}
              aria-pressed={mpaOn}
            >
              MPAs
            </button>
            <button
              type="button"
              className={"mpa-pill" + (bathyOn ? " active" : "")}
              onClick={(e) => { e.stopPropagation(); updateBathyOn(!bathyOn); }}
              title={bathyOn ? "Bottom detail visible · click to hide" : "Bottom detail hidden · click to show"}
              aria-pressed={bathyOn}
            >
              Bottom
            </button>
            <Chevron open={controlsOpen} />
          </span>
        </div>
        {controlsOpen && <div className="panel-body">
          <div className="layer-toggle layer-toggle-6" role="tablist">
            <button
              className={layer === "sst" ? "active" : ""}
              onClick={() => setLayer("sst")}
              title="Sea-surface temperature from MUR satellite"
            >
              <span className="lt-label">Temp</span>
              <span className="lt-sub">°{units}</span>
            </button>
            <button
              className={layer === "chl" ? "active" : ""}
              onClick={() => setLayer("chl")}
              title="Chlorophyll-a concentration from VIIRS — visibility proxy"
            >
              <span className="lt-label">Chl</span>
              <span className="lt-sub">mg/m³</span>
            </button>
            <button
              className={layer === "wind" ? "active" : ""}
              onClick={() => setLayer("wind")}
              title="10 m wind from HRRR + GFS"
            >
              <span className="lt-label">Wind</span>
              <span className="lt-sub">kt</span>
            </button>
            <button
              className={layer === "swell" ? "active" : ""}
              onClick={() => setLayer("swell")}
              title="Significant wave height + period + direction from NOAA WaveWatch III"
            >
              <span className="lt-label">Swell</span>
              <span className="lt-sub">ft Hs</span>
            </button>
            <button
              className={layer === "current" ? "active" : ""}
              onClick={() => setLayer("current")}
              title="Surface current speed and direction from HFR observations plus tide/wind inference (BETA — model blends sparse HF-radar coverage with bulk wind/tide inference; verify against local knowledge)."
            >
              <span className="lt-label">Current</span>
              <span className="lt-sub">kt</span>
              <span className="lt-beta">Beta</span>
            </button>
            <button
              className={layer === "viz" ? "active" : ""}
              onClick={() => setLayer("viz")}
              title="Predicted dive visibility (BETA — model unvalidated for NorCal; use as advisory only). Output is feet, not a direct measurement."
            >
              <span className="lt-label">Vis</span>
              <span className="lt-sub">ft</span>
              <span className="lt-beta">Beta</span>
            </button>
          </div>
          {layer === "sst" && hasSstTimeline ? (
            <div className="composite wind-grid-host">
              <div className="composite-label">
                <span>{activeSstMode === "forecast" ? "Sea temp forecast" : "Sea temp trend"}</span>
                <span className="hint">drag the timeline below</span>
              </div>
              <SstModeToggle
                mode={activeSstMode}
                setMode={setSstMode}
                hasHistory={Boolean(sstHistorySummary)}
                hasForecast={Boolean(sstForecastSummary)}
              />
              <SstCurrentCard sel={sstActiveSel} units={units} mode={activeSstMode} />
              <div className="composite-window">
                <span>{activeSstMode === "forecast" ? "Forecast" : "Day"}</span>
                <span className="mono">{compositeText}</span>
              </div>
            </div>
          ) : layer === "wind" ? (
            <div className="composite wind-grid-host">
              <div className="composite-label">
                <span>7-day forecast</span>
                <span className="hint">drag the timeline below</span>
              </div>
              <WindCurrentSelectionCard
                sel={windSel}
                setSel={setWindSel}
              />
            </div>
          ) : layer === "swell" ? (
            <div className="composite wind-grid-host">
              <div className="composite-label">
                <span>5-day swell</span>
                <span className="hint">drag the timeline below</span>
              </div>
              <SwellCurrentCard sel={swellSel} />
            </div>
          ) : layer === "current" ? (
            <div className="composite wind-grid-host">
              <div className="composite-label">
                <span>Surface current</span>
                <span className="hint">drag the timeline below</span>
              </div>
              <CurrentCurrentCard sel={currentSel} />
            </div>
          ) : (
            <div className="composite">
              <div className="composite-label">
                <span>{timeOpts.label}</span>
                <span className="hint">{timeOpts.helper}</span>
              </div>
              <div
                className="composite-buttons"
                style={{ gridTemplateColumns: `repeat(${timeOpts.buttons.length}, 1fr)` }}
              >
                {timeOpts.buttons.map((label, i) => {
                  const d = i + 1;
                  return (
                    <button
                      key={d}
                      className={composite === d ? "active" : ""}
                      onClick={() => setComposite(d)}
                    >
                      <span className="cb-num">{label}</span>
                      <span className="cb-tag">{timeOpts.tags[i]}</span>
                    </button>
                  );
                })}
              </div>
              <div className="composite-window">
                <span>Window</span>
                <span className="mono">{compositeText}</span>
              </div>
            </div>
          )}
        </div>}
      </div>

      <div className={"panel info-tr" + (infoOpen ? "" : " collapsed")}>
        <div
          className="panel-header"
          style={{ cursor: "pointer", userSelect: "none" }}
          onClick={() => setInfoOpen((v) => !v)}
        >
          <span className="panel-title">How to read this</span>
          <Chevron open={infoOpen} />
        </div>
        {infoOpen && (<>
          <div className="panel-body" style={{ overflowY: "auto" }}>
            {layer === "sst" ? (
              <div className="info-section">
                <h4 className="info-h">Sea Surface Temperature</h4>
                {(() => {
                  const r = activeRegion();
                  const sstCopy = {
                    ca: {
                      blue:   "cold — typical Central Coast (54–57°F) and upwelling near Pt. Conception.",
                      cyan:   "the transition zone — comfortable for divers in spring suits.",
                      yellow: "warm SoCal summer water (66–70°F). Trunks weather.",
                      red:    "anomaly — possible marine heatwave. Watch for kelp stress and harmful algal blooms.",
                    },
                    pnw: {
                      blue:   "cold — typical Salish Sea / Olympic Coast (45–52°F). Drysuit conditions.",
                      cyan:   "warmer outer-coast summer water (54–58°F). Thick wetsuit OK on calmer days.",
                      yellow: "rare warm anomaly (60–65°F). Watch for stratification + bloom triggers.",
                      red:    "extreme anomaly — marine heatwave territory. Kelp stress, harmful algal blooms.",
                    },
                    tropical: {
                      blue:   "rare cold pocket (~72–76°F), upwelling near the Keys or Yucatan shelf.",
                      cyan:   "typical winter Caribbean / Bahamas (76–80°F). Light wetsuit on long dives.",
                      yellow: "typical summer Caribbean (82–86°F). Rash guard + boardies.",
                      red:    "extreme warm anomaly — coral bleaching threshold (>86°F sustained).",
                    },
                    baja: {
                      blue:   "cold California-Current upwelling (~57–63°F). Cedros / Bahía Tortugas / Ensenada in winter — drysuit or 7mm.",
                      cyan:   "mid-Baja transition water (~65–72°F). Magdalena Bay year-round; northern Sea of Cortez winter.",
                      yellow: "warm Sea of Cortez summer (~75–82°F). Cabo corridor, La Paz, Loreto — 3mm or trunks.",
                      red:    "hot northern Cortez (>85°F). Bahía de los Ángeles / San Felipe peak summer — Cabo Pulmo coral stress watch.",
                    },
                  };
                  const c = sstCopy[r] || sstCopy.ca;
                  return (
                    <>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(40,130,210)" }}></span>
                        <strong>Blue</strong> means {c.blue}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(120,220,220)" }}></span>
                        <strong>Cyan</strong> is {c.cyan}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(240,220,110)" }}></span>
                        <strong>Yellow</strong> is {c.yellow}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(170,20,35)" }}></span>
                        <strong>Red</strong> means {c.red}
                      </p>
                    </>
                  );
                })()}
              </div>
            ) : layer === "chl" ? (
              <div className="info-section">
                <h4 className="info-h">Water Clarity (Chlorophyll-a)</h4>
                {(() => {
                  const r = activeRegion();
                  const copy = {
                    navy:  "gin-clear, low-productivity water. Best visibility for divers and spearos.",
                    blue:  "typical clear nearshore viz.",
                    green: "upwelling — fish food, but viz drops.",
                    brown: "peak upwelling or mild bloom. Avoid if water smells off.",
                  };
                  if (r === "baja") Object.assign(copy, {
                    navy:  "Cabo Pulmo / Espíritu Santo / Cerralvo summer clarity — 80–100 ft+ on calm days.",
                    blue:  "typical south-Baja Pacific + open Cortez water.",
                    green: "Pacific Baja upwelling (Vizcaíno tongue, Cedros) — colder, fishier, viz drops.",
                    brown: "north-Cortez summer bloom or post-rain river plume (Sonora/Sinaloa mainland). Cortez green-tide season is Jul–Sep.",
                  });
                  return (
                    <>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(31,58,85)" }}></span>
                        <strong>Deep navy</strong> = {copy.navy}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(91,141,181)" }}></span>
                        <strong>Mid blue</strong> = {copy.blue}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(127,160,90)" }}></span>
                        <strong>Olive green</strong> = {copy.green}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(122,90,60)" }}></span>
                        <strong>Warm brown</strong> = {copy.brown}
                      </p>
                    </>
                  );
                })()}
              </div>
            ) : layer === "wind" ? (
              <div className="info-section">
                <h4 className="info-h">Wind Speed (10 m)</h4>
                {(() => {
                  const r = activeRegion();
                  const copy = {
                    light:   "Glassy. Paddleable, divable.",
                    green:   "Light breeze. Surface texture, still mellow.",
                    yellow:  "Moderate chop. Boat-handling matters.",
                    orange:  "Small craft advisory territory.",
                    magenta: "Gale. Stay home.",
                  };
                  if (r === "baja") Object.assign(copy, {
                    light:   "Glassy. Cortez at dawn, Pacific Baja calm morning. Pangas + spearos out.",
                    green:   "Light breeze. Typical Cortez afternoon (sea breeze on, before the gulf builds).",
                    yellow:  "El Norte ramping in the upper gulf, Pacific Baja whitecaps. Watch panga safety.",
                    orange:  "Sustained Norte / cold-front passage. Cortez crossings get sporty.",
                    magenta: "Chubasco / Norte storm winds. Stay home — Cortez gets steep + dangerous fast.",
                  });
                  return (
                    <>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(170,210,240)" }}></span>
                        <strong>Light blue</strong> = 5 kt or less. {copy.light}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(120,200,160)" }}></span>
                        <strong>Green</strong> = ~10 kt. {copy.green}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(220,220,100)" }}></span>
                        <strong>Yellow</strong> = ~15 kt. {copy.yellow}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(240,160,70)" }}></span>
                        <strong>Orange</strong> = ~20 kt. {copy.orange}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(140,30,90)" }}></span>
                        <strong>Magenta</strong> = 35 kt+. {copy.magenta}
                      </p>
                      <p className="info-p">
                        Particles trace direction (the line is "from where wind is coming"). Hover
                        for the exact knots and compass bearing.
                      </p>
                    </>
                  );
                })()}
              </div>
            ) : layer === "current" ? (
              <div className="info-section">
                <h4 className="info-h">Surface Current</h4>
                {(() => {
                  const r = activeRegion();
                  const intro = r === "baja"
                    ? "Color shows estimated surface-current speed in knots. Cortez gets dominated by tidal pumping — the Midriff Islands sill funnels 3–5 kt flows between Tiburón and Ángel de la Guarda; south Cortez points (Cerralvo, Cabo Pulmo) run gentler."
                    : "Color shows estimated surface-current speed in knots. Particle trails show where the water is setting, which matters for drift, anchoring, and how stable a kelp-bed dive window will feel.";
                  const teal = r === "baja"
                    ? "noticeable set. Watch the panga drift on Cortez crossings and at point breaks."
                    : "noticeable set. Watch the boat, floatline, and exit.";
                  const yellow = r === "baja"
                    ? "Cortez tidal pump. El Bajo / Marisla / Midriff channels run yellow at peak ebb/flood."
                    : "strong enough to matter for most freedivers.";
                  const red = r === "baja"
                    ? "Salsipuedes / Canal de Ballenas tidal-race territory. Local-guide-only."
                    : "high-risk surface set. Treat as a no-go unless you have strong local confirmation.";
                  return (
                    <>
                      <p className="info-p">
                        <strong>Beta estimate.</strong> Use this as planning context only, and
                        verify with local observations, boat drift, and in-water feel before
                        committing to a dive.
                      </p>
                      <p className="info-p">{intro}</p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(125,211,252)" }}></span>
                        <strong>Blue</strong> is weak current, generally easier diving.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(94,234,212)" }}></span>
                        <strong>Teal</strong> is {teal}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(250,204,21)" }}></span>
                        <strong>Yellow</strong> is {yellow}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(220,38,38)" }}></span>
                        <strong>Red/purple</strong> is {red}
                      </p>
                      <p className="info-p" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                        This is a surface-current product. Reef-depth current can differ near points,
                        kelp, shelves, and island structure.
                      </p>
                    </>
                  );
                })()}
              </div>
            ) : layer === "swell" ? (
              <div className="info-section">
                <h4 className="info-h">Swell · Hs / Tp / Dp</h4>
                {(() => {
                  const r = activeRegion();
                  const stormTxt = r === "baja"
                    ? "Cabo Falso storm-swell / hurricane outflow."
                    : "Mavericks/Cortes territory.";
                  return (
                    <>
                      <p className="info-p">
                        Three numbers per cell — significant wave height (<strong>Hs</strong>, the
                        headline ft), peak period (<strong>Tp</strong>, seconds), and primary
                        direction (<strong>Dp</strong>, "from" compass). Color shows Hs.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(236,254,255)" }}></span>
                        <strong>Glassy</strong> — 0–1 ft. Flat. Perfect for novices and freedivers.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(103,232,249)" }}></span>
                        <strong>Calm</strong> — 1–3 ft. Easy nearshore conditions.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(132,204,22)" }}></span>
                        <strong>Workable</strong> — 3–5 ft. Manageable surge, fun-size surf.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(234,179,8)" }}></span>
                        <strong>Sketchy</strong> — 5–8 ft. OK offshore; rough nearshore.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(249,115,22)" }}></span>
                        <strong>Big</strong> — 8–12 ft. Stay deep; advanced surf only.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(220,38,38)" }}></span>
                        <strong>XL</strong> — 12–20 ft. Don't dive. Gnarly surf.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(127,29,29)" }}></span>
                        <strong>Storm seas</strong> — 20+ ft. {stormTxt}
                      </p>
                      <p className="info-p" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                        Period flips the feel: a 4 ft / <strong>16 s</strong> day is a clean
                        long-period groundswell; same 4 ft / <strong>8 s</strong> is choppy
                        windswell. Tooltip + the timeline badge expose Tp and Dp directly.
                        {r === "baja" && " Pacific Baja gets NW groundswell year-round; Cortez sees almost no real swell — most height shown there is local wind chop."}
                      </p>
                    </>
                  );
                })()}
              </div>
            ) : (
              <div className="info-section">
                <h4 className="info-h">Predicted Visibility · model output</h4>
                {(() => {
                  const r = activeRegion();
                  const goodCopy = r === "baja"
                    ? "Cyan; typical open Cortez or Pacific Baja nearshore."
                    : "Cyan; typical CA kelp diving.";
                  const exCopy = r === "baja"
                    ? "Deep navy; Cabo Pulmo / Espíritu Santo / Cerralvo / Carmen on a calm low-chl day. 90–115 ft is reachable in summer."
                    : "Deep navy; once-a-year clarity.";
                  return (
                    <>
                      <p className="info-p">
                        <strong>This is a prediction, not a measurement.</strong> A zone-aware
                        model blends satellite, weather, and ocean inputs to estimate the
                        Secchi-equivalent visibility you'd expect in feet.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(194,65,12)" }}></span>
                        <strong>Poor</strong> — 0–10 ft. Burnt orange; silty / blown out.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(34,197,94)" }}></span>
                        <strong>Fair</strong> — 10–20 ft. Green; diveable but washed out.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(6,182,212)" }}></span>
                        <strong>Good</strong> — 20–30 ft. {goodCopy}
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(3,105,161)" }}></span>
                        <strong>Very Good</strong> — 30–50 ft. Blue; clean blue water.
                      </p>
                      <p className="info-p">
                        <span className="swatch" style={{ background: "rgb(31,77,117)" }}></span>
                        <strong>Excellent</strong> — 50 ft+. {exCopy}
                      </p>
                    </>
                  );
                })()}
              </div>
            )}
            <div className="info-section">
              <h4 className="info-h">{
                layer === "sst"    ? (activeSstMode === "forecast" ? "Beta forecast" : "Historical trend")
                : layer === "wind" ? "Forecast slots"
                : layer === "current" ? "Consistency and reversals"
                : layer === "viz"  ? "How the model works"
                : layer === "swell"? "Period vs height"
                : "Why composite windows?"
              }</h4>
              <p className="info-p">
                {layer === "sst"
                  ? activeSstMode === "forecast"
                    ? "This is a beta temperature forecast. It starts from the freshest observed SST field and carries recent warming or cooling forward with fast decay, so it is useful for trend direction rather than exact dive-day temperature."
                    : "The timeline shows the most recent daily satellite analyses so you can see whether a zone is warming, cooling, or holding steady before a dive window."
                  : layer === "wind"
                  ? "HRRR is NOAA's hourly 3-km weather model for the first 48 h, then GFS (25 km) extends out to 7 days. Drag the timeline to scrub through any hour — every cell on the heatmap reflects the wind speed at that exact hour. Days 5–6 are tagged 'low' confidence (trend, not gospel)."
                  : layer === "current"
                  ? "The current layer carries speed, set direction, consistency, and reversal risk by dive-window bucket. HFR observations anchor the near-term surface field where available; later windows decay into an explicitly labeled tide/wind inference."
                  : layer === "viz"
                  ? "A zone-aware stack (3 latitude × 3 distance-from-shore) translates today's chl-a into a Secchi depth, then nudges it for storm-driven bottom stir, river/precip runoff, tidal mixing, kelp shading, and substrate. The 'best estimate' is the median; hover any cell to see the value."
                  : layer === "swell"
                  ? "Hs (height) is the headline number; Tp (period) tells you whether the wave train is a clean groundswell or short-period chop. 12+ s is groundswell, 8–12 s is mixed, <8 s is windswell. Direction (Dp) is reported \"from\" — a NW swell at 295° is propagating SE toward the shore."
                  : "Clouds wipe out single-day satellite imagery off CA, especially the summer marine layer. A 2- or 3-day window backfills with the most recent valid pixel — 1-day is freshest, 3-day has the best coverage."}
              </p>
            </div>
            <div className="info-section">
              <h4 className="info-h">Sources & cadence</h4>
              <p
                className="info-p"
                style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10.5 }}
              >
                {layer === "sst"
                  ? activeSstMode === "forecast"
                    ? "NOAA MUR L4 / NOAA blended SST anchor. Beta forecast generated from observed-trend persistence and refreshed with the daily ocean-data job."
                    : "NOAA MUR L4 SST plus NOAA blended SST fallback. Daily gap-filled satellite analysis loaded as a 7-day historical trend plus the legacy 1/2/3-day composites."
                  : layer === "wind"
                  ? "NOAA HRRR (3-km, hourly). 10-m UGRD/VGRD via NOMADS byte-range fetch. Regridded to ~5 km."
                  : layer === "current"
                  ? "IOOS/NDBC HFRNet US West Coast 6-km surface currents where available, blended with NOAA CO-OPS tide range, lunar spring/neap phase, and wind drift. Refreshed with the wind/swell cycle."
                  : layer === "viz"
                  ? "MUR SST · VIIRS chl-a · HRRR + GFS wind (7d) · WaveWatch III (3d max) · CPC precip · USGS river discharge · NOAA CO-OPS tides · MODIS-Aqua climatology. Recomputed daily."
                  : layer === "swell"
                  ? "NOAA WaveWatch III (gfswave wcoast 0.16°). HTSGW + PERPW + DIRPW pulled per hour via NOMADS byte-range fetch. 5-day forecast with hourly resolution; refreshed every cycle."
                  : "NOAA Coral Reef Watch · NASA OB.DAAC MODIS-Aqua · Copernicus GLO-MFC. Daily L3 composites, ~1 km grid, regridded to bounding box."}
              </p>
            </div>
          </div>
        </>)}
      </div>

      <div className={"panel spots-bl" + (spotsOpen ? "" : " collapsed")}>
        <div
          className="panel-header"
          style={{ cursor: "pointer", userSelect: "none" }}
          onClick={() => setSpotsOpen((v) => !v)}
        >
          <span className="panel-title">Saved Spots</span>
          <Chevron open={spotsOpen} />
        </div>
        {spotsOpen && <div className="panel-body">
          <div className="spots-list">
            {SAVED_SPOTS.map((s) => {
              let v, valTxt, unit, col;
              if (layer === "sst") {
                v = getSST(s.lng, s.lat, activeComposite);
                if (Number.isFinite(v)) {
                  valTxt = units === "F" ? `${(v * 9 / 5 + 32).toFixed(1)}` : `${v.toFixed(1)}`;
                  col = sstColor(v);
                } else {
                  valTxt = "—";
                  col = "var(--ink-3)";
                }
                unit = `°${units}`;
              } else if (layer === "chl") {
                v = getChl(s.lng, s.lat, activeComposite);
                if (Number.isFinite(v)) {
                  valTxt = `${v.toFixed(2)}`;
                  col = chlColor(v);
                } else {
                  valTxt = "—";
                  col = "var(--ink-3)";
                }
                unit = "mg/m³";
              } else if (layer === "wind") {
                v = getWindSpeed(s.lng, s.lat, activeComposite);
                valTxt = Number.isFinite(v) ? `${v.toFixed(1)}` : "—";
                unit = "kt";
                col = "var(--ink-2)";
              } else if (layer === "current") {
                v = getCurrentSpeed(s.lng, s.lat, activeComposite);
                valTxt = Number.isFinite(v) ? `${v.toFixed(1)}` : "—";
                unit = "kt";
                col = "var(--ink-2)";
              } else if (layer === "swell") {
                const w = getSwell5dStats(s.lng, s.lat, activeComposite);
                v = Number.isFinite(w.hs) ? w.hs * 3.28084 : NaN;
                valTxt = Number.isFinite(v) ? `${v.toFixed(1)}` : "—";
                unit = "ft";
                col = "var(--ink-2)";
              } else {
                v = getVizFt(s.lng, s.lat, activeComposite);
                valTxt = Number.isFinite(v) ? `~${Math.round(v)}` : "—";
                unit = "ft";
                col = "var(--ink-2)";
              }
              return (
                <div
                  key={s.id}
                  className={"spot" + (activeSpot === s.id ? " active" : "")}
                  onClick={() => setActiveSpot(s.id)}
                >
                  <div>
                    <div className="spot-name">
                      <span className="pin" style={{ color: col }}></span>
                      {s.name}
                    </div>
                    <div className="spot-meta mono">
                      {s.lat.toFixed(2)}°N {Math.abs(s.lng).toFixed(2)}°W
                    </div>
                    {/* Trend pill + sparkline only for SST. Both fall
                        back to null when history isn't loaded yet, so
                        non-SST layers and first-paint stay clean. */}
                    {layer === "sst" && (
                      <div className="spot-trend">
                        <SstTrendChip lng={s.lng} lat={s.lat} units={units} />
                        <SstSparkline lng={s.lng} lat={s.lat} units={units} />
                      </div>
                    )}
                  </div>
                  <div className="spot-val mono">
                    {valTxt}
                    <span className="unit">{unit}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>}
      </div>

      <div className={"panel legend-br" + (legendOpen ? "" : " collapsed")}>
        <div
          className="panel-header"
          style={{ cursor: "pointer", userSelect: "none" }}
          onClick={() => setLegendOpen((v) => !v)}
        >
          <span className="panel-title" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {layer === "sst" ? "Sea Surface Temperature"
              : layer === "chl" ? "Water Clarity (Chlorophyll-a)"
              : layer === "wind" ? "Wind Speed (10 m)"
              : layer === "swell" ? "Swell · Hs / Tp / Dp"
              : layer === "current" ? "Surface Current"
              : "Predicted Visibility"}
            {layer === "viz" && <span className="predicted-badge">PREDICTED</span>}
            {layer === "current" && <span className="beta-badge">BETA</span>}
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="panel-title mono" style={{ color: "var(--ink-3)" }}>
              {layer === "sst" ? `°${units}`
                : layer === "chl" ? "mg/m³"
                : layer === "wind" ? "kt"
                : layer === "swell" ? "ft Hs"
                : layer === "current" ? "kt"
                : "ft"}
            </span>
            <Chevron open={legendOpen} />
          </span>
        </div>
        {legendOpen && <div className="panel-body">
          <div className={`legend-bar ${layer}`}></div>
          <div className="legend-ticks">
            {layer === "sst" ? (
              units === "F" ? (
                <>
                  <span>48</span><span>55</span><span>61</span><span>66</span><span>72</span><span>77</span>
                </>
              ) : (
                <>
                  <span>9</span><span>13</span><span>16</span><span>19</span><span>22</span><span>25</span>
                </>
              )
            ) : layer === "chl" ? (
              <>
                <span>0.05</span><span>0.3</span><span>1.0</span><span>3.5</span><span>20+</span>
              </>
            ) : layer === "wind" ? (
              <>
                <span>0</span><span>5</span><span>10</span><span>15</span><span>20</span><span>25</span><span>35+</span>
              </>
            ) : layer === "swell" ? (
              <>
                <span>0</span><span>1</span><span>3</span><span>5</span><span>8</span><span>12</span><span>20+</span>
              </>
            ) : layer === "current" ? (
              <>
                <span>0</span><span>0.4</span><span>0.8</span><span>1.2</span><span>1.8</span><span>2.5</span><span>3+</span>
              </>
            ) : (
              <>
                <span>0</span><span>10</span><span>20</span><span>30</span><span>50+</span>
              </>
            )}
          </div>
          <div className="legend-meta">
            <span>
              {layer === "sst" ? "Cold upwelling → Heatwave"
                : layer === "chl" ? "Gin-clear → Bloom"
                : layer === "wind" ? "Calm → Gale"
                : layer === "swell" ? "Glassy → Storm seas"
                : layer === "current" ? "Weak → ripping"
                : "Poor → Excellent"}
            </span>
            <span>
              {(() => {
                // Mirror the cursor's reading in the legend metadata when
                // the user is hovering over the map. Falls back to the
                // active window / source line when there's nothing to
                // mirror — so the strip doesn't go blank on idle.
                const hv = hover ? hoverReadout(layer, hover, activeComposite, units) : null;
                if (hv) return <strong>{hv}</strong>;
                const suffix =
                  layer === "sst"   ? ` · MUR trend`
                  : layer === "wind"  ? ` · ${windSource(activeComposite) || "HRRR"}`
                  : layer === "current" ? ` · ${currentSource(activeComposite) || "surface estimate"}`
                  : layer === "viz" ? ` · model output`
                  : layer === "swell" ? ` · WaveWatch III`
                  : ` · ${composite}-day composite`;
                return (
                  <>
                    <strong>{compositeText}</strong>
                    {suffix}
                    {!layerIsReal && dataState?.ready && " · no data"}
                  </>
                );
              })()}
            </span>
          </div>
        </div>}
      </div>

      <div className="zoom-ctl">
        <button aria-label="Zoom in" onClick={() => zoomAt(size.w / 2, size.h / 2, 1 / 1.4)}>+</button>
        <button aria-label="Zoom out" onClick={() => zoomAt(size.w / 2, size.h / 2, 1.4)}>−</button>
        <button aria-label="Recenter" onClick={resetView}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 1v3M12 20v3M1 12h3M20 12h3" />
          </svg>
        </button>
      </div>

      <div className="attribution">
        {(() => {
          const r = activeRegion();
          const b = BBOX;
          const tag =
            r === "pnw"      ? "Pacific NW (beta)" :
            r === "tropical" ? "FL + Caribbean (beta)" :
            "CA Coast";
          return `${tag} bbox ${b.latMin.toFixed(1)}°→${b.latMax.toFixed(1)}°N · ${b.lngMin.toFixed(1)}°→${b.lngMax.toFixed(1)}°W`;
        })()}
      </div>

      {/* Coronados / CONANP disclaimer is CA-specific (the Coronados sit in
          Mexican waters at the south end of the CA bbox). For PNW + tropical
          the MPA layer doesn't even render CA data so the disclaimer is
          irrelevant. Region-gate it explicitly. */}
      {mpaOn && activeRegion() === "ca" && (
        <CoronadosBanner vb={renderVb} size={size} />
      )}

      {selectedMpa && (
        <MpaPopup mpa={selectedMpa} onClose={() => setSelectedMpa(null)} />
      )}

      {selectedBathy && (
        <BathyPopup feature={selectedBathy} onClose={() => setSelectedBathy(null)} />
      )}
    </div>
    {/* MobileShell sits OUTSIDE .map-stage so the map can shrink to
        leave room for the peek strip on phones (without the strip
        overlapping the bottom of the bbox). On desktop this branch
        doesn't mount at all. */}
    {isMobile && (
      <MobileSheet
        layer={layer} setLayer={setLayer}
        composite={composite} setComposite={setComposite}
        sstMode={sstMode} setSstMode={setSstMode}
        sstActiveSel={sstActiveSel} setSstActiveSel={setSstActiveSel}
        activeSstMode={activeSstMode}
        hasSstTimeline={hasSstTimeline}
        windSel={windSel} setWindSel={setWindSel}
        swellSel={swellSel} setSwellSel={setSwellSel}
        currentSel={currentSel} setCurrentSel={setCurrentSel}
        activeComposite={activeComposite}
        dataState={dataState}
        setMpaOn={updateMpaOn}
        setBathyOn={updateBathyOn}
        activeSpot={activeSpot} setActiveSpot={setActiveSpot}
        timeOpts={timeOpts}
        compositeText={compositeText}
        layerIsReal={layerIsReal}
        hover={hover}
        setHover={setHover}
      />
    )}
    </>
  );
}





