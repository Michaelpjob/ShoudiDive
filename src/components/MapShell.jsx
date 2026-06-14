// MapShell — owns the map stage SVG + DataOverlay + all overlay
// layers + timeline scrubbers + popups, and routes to DesktopLayout
// (chrome that sits over the map) and MobileSheet (mobile bottom
// sheet UI).
//
// Was called `DesktopView` and lived inline at the bottom of
// App.jsx (1400+ lines). Extracted into its own file 2026-05-23
// as Stage 4 of the refactor. Then Stage 4b on 2026-05-24 pulled
// out the desktop chrome (Tooltip + 4 collapsible panels +
// zoom-ctl + attribution) into DesktopLayout.jsx, dropping this
// file from ~1620 LOC to ~800.
//
// What lives where now:
//   * MapShell (this file): map stage, SVG content, basemap, data
//     overlay, wind particles, timeline scrubbers, popups, gesture
//     handlers, viewport state. Renders <DesktopLayout/> +
//     <MobileSheet/> as siblings under the map.
//   * DesktopLayout: hover Tooltip + Layer/How-to-read/Saved Spots/
//     Legend panels + zoom-ctl + attribution.
//   * MobileSheet: mobile bottom-sheet UI.

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
import SpotDetailView from "./SpotDetailView.jsx";
import CoronadosBanner from "./CoronadosBanner.jsx";
import { selToSlotKey } from "./WindDayGrid.jsx";
import WindTimeline from "./WindTimeline.jsx";
import SwellTimeline from "./SwellTimeline.jsx";
import CurrentTimeline, { currentSelToSlotKey } from "./CurrentTimeline.jsx";
import SstTimeline, { sstSelToSlotKey } from "./SstTimeline.jsx";
import { MoonWidget } from "./MoonIcon.jsx";
import DesktopLayout from "./DesktopLayout.jsx";

import { useMapViewport } from "../hooks/useMapViewport.js";
import { usePopupState } from "../hooks/usePopupState.js";
import { usePrefs } from "../contexts/PrefsContext.jsx";
import {
  project,
  unproject,
  getFitted,
  SAVED_SPOTS,
} from "../lib/mapData.js";
import {
  dataDates,
  isReal,
  getWind5dSummary,
  getCurrent5dSummary,
  getSwell5dSummary,
} from "../lib/dataSource.js";
import { activeRegion, dataPath } from "../lib/region.js";
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

export default function MapShell({ layer, setLayer, composite, setComposite, sstMode, setSstMode, sstActiveSel, setSstActiveSel, activeSstMode, sstTimelineSummary, hasSstTimeline, hasSstHistory, hasSstForecast, windSel, setWindSel, swellSel, setSwellSel, currentSel, setCurrentSel, dataState, viewingDate }) {
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
  // Spot Detail (Phase 1B): which saved spots have a pre-computed
  // bundle (public/data/spots/index.json, written by
  // pipeline/build_spot_bundles.py), and which spot's detail overlay
  // is currently open. Regions without bundles no-op gracefully.
  const [bundledSpots, setBundledSpots] = useState(new Set());
  const [spotDetailFor, setSpotDetailFor] = useState(null);
  useEffect(() => {
    let cancelled = false;
    fetch(dataPath("/data/spots/index.json"))
      .then((r) => (r.ok ? r.json() : null))
      .then((idx) => {
        if (cancelled || !idx?.spots) return;
        setBundledSpots(new Set(idx.spots));
      })
      .catch(() => { /* no bundles in this region — graceful no-op */ });
    return () => { cancelled = true; };
  }, []);
  // Escape clears the readout pin (desktop affordance; mobile has a
  // tap-X on the status row). Bound once for the lifetime of the shell.
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") setHover(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  function openSpotDetail(spotId) {
    const s = SAVED_SPOTS.find((x) => x.id === spotId);
    if (s) setSpotDetailFor(s);
  }
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

  // The readout pin holds across layer switches by design (drop it once,
  // read temp → chl → wind → current → viz at the SAME point). Safe because
  // the pin stores only lng/lat — each layer's value is recomputed from the
  // coordinate, never a cached per-layer shape.

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

  function onMouseUp(e) {
    const ps = panStateRef.current;
    // A click that didn't turn into a pan = drop (or move) the readout
    // pin. The pin is the single point every layer's value reads from
    // (no cursor-follow; see onMove). Skip if the click landed on a
    // saved-spot pin (that owns its own selection) or a gesture child.
    const onSpotPin = e?.target?.closest?.(".spot-pins, .map-pin");
    if (ps && !ps.moved && !isMobile && !onSpotPin
        && !isMapGestureChildTarget(e?.target)) {
      const r = stageRef.current.getBoundingClientRect();
      const vbX = vb.x + (ps.startScreenX / r.width) * vb.w;
      const vbY = vb.y + (ps.startScreenY / r.height) * vb.h;
      const [lng, lat] = unproject(vbX, vbY, size.w, size.h);
      setHover({ x: ps.startScreenX, y: ps.startScreenY, lng, lat, pinned: true });
      track("map_pin_set", { layer });
    }
    panStateRef.current = null;
    setIsPanning(false);
  }

  function onMove(e) {
    // Over a gesture child (timeline scrubber, panel) we don't pan — but
    // we must NOT clear the pin here. The pin is persistent; clearing it
    // on mouse-over was what made the value revert to the area mean the
    // instant you reached for the time slider.
    if (isMapGestureChildTarget(e.target)) {
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
      // The pin is geographic — keep it through a pan (its on-screen
      // position is reprojected from lng/lat each render).
      return;
    }
    // Require-a-pin (2026-06-13): moving the cursor no longer sets a
    // transient readout. The single point every layer reads from is the
    // pin dropped on click (onMouseUp) — so "12.8 kt" is always anchored
    // to a visible marker rather than wherever the mouse happens to be.
  }
  function onLeave() {
    // Do NOT clear the pin on mouse-leave — it persists until the next
    // click moves it (or the user clears it).
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

  // The readout pin is geographic; its on-screen position drifts as the
  // map pans/zooms. Reproject lng/lat → live screen px each render so the
  // value tooltip (which positions off hover.x/y) tracks the marker
  // instead of sticking at the original click point. Same viewBox→screen
  // mapping the fitted-box overlay uses (cssLeft/cssTop below).
  const hoverForUI = (() => {
    if (!hover?.pinned || !Number.isFinite(hover.lng) || !size.w) return hover;
    const [vbX, vbY] = project(hover.lng, hover.lat, size.w, size.h);
    return {
      ...hover,
      x: ((vbX - renderVb.x) / renderVb.w) * size.w,
      y: ((vbY - renderVb.y) / renderVb.h) * size.h,
    };
  })();
  const clearPin = () => setHover(null);

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

        {/* Readout pin — the single point every layer's value reads from.
            Projected from lng/lat like the spot pins, so it stays put on
            the map through pan/zoom. A crosshair + ring reads as "measure
            here", visually distinct from the round saved-spot pins. */}
        {hover?.pinned && Number.isFinite(hover.lng) && (() => {
          const [px, py] = project(hover.lng, hover.lat, size.w, size.h);
          const rr = 9 / zoomLevel;
          const arm = 14 / zoomLevel;
          return (
            <g className="map-pin" style={{ pointerEvents: "none" }}>
              <circle cx={px} cy={py} r={rr} fill="none"
                stroke="var(--accent)" strokeWidth={2 / zoomLevel} />
              <circle cx={px} cy={py} r={2.5 / zoomLevel} fill="var(--accent)" />
              <line x1={px - arm} y1={py} x2={px - rr} y2={py}
                stroke="var(--accent)" strokeWidth={1.6 / zoomLevel} />
              <line x1={px + rr} y1={py} x2={px + arm} y2={py}
                stroke="var(--accent)" strokeWidth={1.6 / zoomLevel} />
              <line x1={px} y1={py - arm} x2={px} y2={py - rr}
                stroke="var(--accent)" strokeWidth={1.6 / zoomLevel} />
              <line x1={px} y1={py + rr} x2={px} y2={py + arm}
                stroke="var(--accent)" strokeWidth={1.6 / zoomLevel} />
            </g>
          );
        })()}
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
        <WindTimeline sel={windSel} setSel={setWindSel} hover={hoverForUI} />
      )}
      {layer === "swell" && (
        <SwellTimeline sel={swellSel} setSel={setSwellSel} hover={hoverForUI} />
      )}
      {layer === "current" && (
        <CurrentTimeline sel={currentSel} setSel={setCurrentSel} hover={hoverForUI} />
      )}

      <DesktopLayout
        layer={layer} setLayer={setLayer}
        composite={composite} setComposite={setComposite}
        sstMode={sstMode} setSstMode={setSstMode}
        sstActiveSel={sstActiveSel}
        activeSstMode={activeSstMode}
        hasSstTimeline={hasSstTimeline}
        hasSstHistory={hasSstHistory}
        hasSstForecast={hasSstForecast}
        windSel={windSel}
        setWindSel={setWindSel}
        swellSel={swellSel}
        currentSel={currentSel}
        units={units}
        hover={hoverForUI}
        clearPin={clearPin}
        activeComposite={activeComposite}
        compositeText={compositeText}
        timeOpts={timeOpts}
        layerIsReal={layerIsReal}
        activeSpot={activeSpot} setActiveSpot={setActiveSpot}
        mpaOn={mpaOn} bathyOn={bathyOn}
        updateMpaOn={updateMpaOn} updateBathyOn={updateBathyOn}
        size={size} zoomAt={zoomAt} resetView={resetView}
        dataState={dataState}
        isMobile={isMobile}
        bundledSpots={bundledSpots}
        openSpotDetail={openSpotDetail}
      />

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
    {/* Spot Detail overlay (Phase 1B) — fixed full-screen, mounted
        outside .map-stage so the wide map's layout is undisturbed. */}
    {spotDetailFor && (
      <SpotDetailView
        spot={spotDetailFor}
        onClose={() => setSpotDetailFor(null)}
        isMobile={isMobile}
      />
    )}
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
        hasSstHistory={hasSstHistory}
        hasSstForecast={hasSstForecast}
        windSel={windSel} setWindSel={setWindSel}
        swellSel={swellSel} setSwellSel={setSwellSel}
        currentSel={currentSel} setCurrentSel={setCurrentSel}
        activeComposite={activeComposite}
        dataState={dataState}
        setMpaOn={updateMpaOn}
        setBathyOn={updateBathyOn}
        activeSpot={activeSpot} setActiveSpot={setActiveSpot}
        bundledSpots={bundledSpots}
        openSpotDetail={openSpotDetail}
        timeOpts={timeOpts}
        compositeText={compositeText}
        layerIsReal={layerIsReal}
        hover={hoverForUI}
        setHover={setHover}
      />
    )}
    </>
  );
}





