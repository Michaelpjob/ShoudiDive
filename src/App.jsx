import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { setFillMode } from "./lib/mapData.js";
import { SeaBasemap, LandBasemap, PLACE_LABELS } from "./components/Basemap.jsx";
import DataOverlay from "./components/DataOverlay.jsx";
import WindParticles from "./components/WindParticles.jsx";
import MpaLayer, { styleForType } from "./components/MpaLayer.jsx";
import BathyLayer, {
  styleForClass,
  loadBathyFeatures,
  visibleBathyFeatures,
  bathyLabels,
} from "./components/BathyLayer.jsx";
import MapLabels from "./components/MapLabels.jsx";
import MobileSheet from "./components/MobileSheet.jsx";
import {
  project,
  unproject,
  sstColor,
  chlColor,
  getFitted,
  SAVED_SPOTS,
} from "./lib/mapData.js";
import {
  loadManifest,
  subscribe,
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
  getDataState,
  getWind5dSummary,
  getCurrent5dSummary,
  getSstHistorySummary,
} from "./lib/dataSource.js";
import WindDayGrid, {
  WindCurrentSelectionCard,
  defaultWindSelection,
  selectionHasData,
  selToSlotKey,
} from "./components/WindDayGrid.jsx";
import WindTimeline from "./components/WindTimeline.jsx";
import SwellTimeline, { SwellCurrentCard } from "./components/SwellTimeline.jsx";
import CurrentTimeline, {
  CurrentCurrentCard,
  currentSelToSlotKey,
  currentSelectionHasData,
  defaultCurrentSelection,
} from "./components/CurrentTimeline.jsx";
import SstTimeline, {
  SstCurrentCard,
  defaultSstSelection,
  sstSelectionHasData,
  sstSelToSlotKey,
} from "./components/SstTimeline.jsx";
import { MoonWidget } from "./components/MoonIcon.jsx";
import {
  getSwell5dSummary,
  getSwell5dStats,
} from "./lib/dataSource.js";

// Reactive viewport-width hook. Returns true at <760 px so we can branch the
// layout between the floating-panel desktop UI and a bottom-sheet mobile UI.
// Why this query rather than just (max-width: 760px):
//
//   * iPhone Pro Max in landscape is 932px wide — the old 760px
//     ceiling wrongly served those users the desktop layout, where
//     panels stretch into a horizontal sprawl and the layer chip row
//     is buried inside the collapsed Layer panel (no obvious way to
//     switch layers — exactly the bug the user reported).
//   * iPad portrait starts at 744px (mini) but goes up to 1024px
//     (12.9"). All sizes are touch-primary phones-grade UX, not
//     desktop-with-mouse UX. Same fix applies.
//
// The (hover: none) and (pointer: coarse) pair is the modern feature-
// detection idiom for "primary input is a finger, not a mouse" — true
// for every iOS/Android device, false for every regular laptop, even
// touchscreen laptops where a mouse is the primary input. Width
// 1024px catches the same devices via a different axis so older
// browsers without media-feature support still get sensible behaviour.
const MOBILE_QUERY = "(max-width: 1024px), (hover: none) and (pointer: coarse)";
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

// Dive flag — the universal "diver below" maritime symbol. Red square,
// white diagonal stripe. Reads instantly at any size (the previous
// freediver silhouette degraded into a fuzzy Y at the topbar's ~20 px
// rendering). Explicit colors so it stays legible in both light and
// dark themes without depending on currentColor.
function FreediverLogo() {
  return (
    <svg
      className="brand-mark"
      viewBox="0 0 32 32"
      aria-hidden="true"
      role="img"
    >
      <rect x="3" y="3" width="26" height="26" rx="5" fill="#dc2626" />
      <path
        d="M27 6 L 6 27"
        stroke="#ffffff"
        strokeWidth="5.2"
        strokeLinecap="round"
      />
    </svg>
  );
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

function useDataVersion() {
  const [, setTick] = useState(0);
  useEffect(() => {
    const unsub = subscribe(() => setTick((t) => t + 1));
    loadManifest();
    return unsub;
  }, []);
  return getDataState();
}

// Compact value readout for the legend metadata strip when the user is
// hovering over the map. Returns null if the cursor doesn't have data
// to display (caller falls back to the static window/source line).
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

const PREF_KEY = "ca-coast-conditions:prefs:v1";
const DEFAULT_PREFS = { theme: "light", opacity: 0.62, units: "F", mpaOn: true, bathyOn: false };

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    return raw ? { ...DEFAULT_PREFS, ...JSON.parse(raw) } : DEFAULT_PREFS;
  } catch {
    return DEFAULT_PREFS;
  }
}

export default function App() {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [layer, setLayer] = useState("sst");
  const [composite, setComposite] = useState(2);
  // Timeline layers each maintain their own selection. SST is historical
  // daily data; wind + swell are forecasts. The helper for each layer turns
  // its selection into the slot key the data layer understands.
  const [sstSel, setSstSel] = useState({ slot: "d0" });
  const [windSel, setWindSel] = useState({ day: 0, bucket: "morning", hour: null });
  const [swellSel, setSwellSel] = useState({ day: 0, bucket: "morning", hour: null });
  const [currentSel, setCurrentSel] = useState({ day: 0, bucket: "midday" });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const dataState = useDataVersion();

  // Reconcile timeline selections against their loaded summaries. Today's
  // morning + pre-dawn buckets get dropped from summary.json once they're
  // past, so a hardcoded initial selection often points at a non-existent
  // slot. SST history can also be shorter than seven days during upstream
  // gaps, so it gets the same data-driven check.
  useEffect(() => {
    const tSummary = getSstHistorySummary();
    if (tSummary && !sstSelectionHasData(tSummary, sstSel)) {
      setSstSel(defaultSstSelection(tSummary));
    }
    const wSummary = getWind5dSummary();
    if (wSummary && !selectionHasData(wSummary, windSel)) {
      setWindSel(defaultWindSelection(wSummary));
    }
    const sSummary = getSwell5dSummary();
    if (sSummary && !selectionHasData(sSummary, swellSel)) {
      setSwellSel(defaultWindSelection(sSummary));
    }
    const cSummary = getCurrent5dSummary();
    if (cSummary && !currentSelectionHasData(cSummary, currentSel)) {
      setCurrentSel(defaultCurrentSelection(cSummary));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataState?.ready, dataState?.manifest?.generated_at]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", prefs.theme);
    document.body.setAttribute("data-theme", prefs.theme);
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
    } catch { /* ignore quota */ }
  }, [prefs]);

  function setPref(key, val) {
    setPrefs((p) => ({ ...p, [key]: val }));
  }

  // Moon-phase icon should track the active time slider when those
  // layers are active, otherwise show "now". Computed at render time
  // so it updates whenever the selected time/layer changes.
  const viewingDate = selToDate(layer, sstSel, windSel, swellSel, currentSel);

  return (
    <div className="app">
      <TopBar
        onSettings={() => setSettingsOpen((v) => !v)}
        settingsOpen={settingsOpen}
        dataState={dataState}
      />
      {settingsOpen && (
        <SettingsPopover prefs={prefs} setPref={setPref} onClose={() => setSettingsOpen(false)} />
      )}
      <DesktopView
        layer={layer}
        setLayer={setLayer}
        composite={composite}
        setComposite={setComposite}
        sstSel={sstSel}
        setSstSel={setSstSel}
        windSel={windSel}
        setWindSel={setWindSel}
        swellSel={swellSel}
        setSwellSel={setSwellSel}
        currentSel={currentSel}
        setCurrentSel={setCurrentSel}
        opacity={prefs.opacity}
        units={prefs.units}
        dataState={dataState}
        mpaOn={prefs.mpaOn}
        setMpaOn={(v) => setPref("mpaOn", v)}
        bathyOn={prefs.bathyOn}
        setBathyOn={(v) => setPref("bathyOn", v)}
        viewingDate={viewingDate}
      />
    </div>
  );
}

// Map a timeline selection to a real Date so the moon icon can update
// with the selected time. Returns null when the active layer has no
// timeline (chl/viz), so the widget falls back to "now".
function selToDate(layer, sstSel, windSel, swellSel, currentSel) {
  if (layer === "sst") {
    const summary = getSstHistorySummary();
    const slot = sstSelToSlotKey(sstSel, summary);
    const dayInfo = summary?.days?.find((d) => d.slot === slot);
    return dayInfo?.date ? new Date(`${dayInfo.date}T12:00:00Z`) : null;
  }
  let sel = null;
  let summary = null;
  if (layer === "wind") {
    sel = windSel;
    summary = getWind5dSummary();
  } else if (layer === "swell") {
    sel = swellSel;
    summary = getSwell5dSummary();
  } else if (layer === "current") {
    sel = currentSel;
    summary = getCurrent5dSummary();
  }
  if (!sel || !summary) return null;
  const dayInfo = summary.days?.find((d) => d.day === sel.day);
  if (!dayInfo?.date) return null;
  const [y, m, d] = dayInfo.date.split("-").map(Number);
  if (!y || !m || !d) return null;
  const hour =
    sel.hour != null
      ? sel.hour
      : { predawn: 5, morning: 8, midday: 12, afternoon: 16, evening: 20 }[
          sel.bucket
        ] ?? 12;
  return new Date(y, m - 1, d, hour, 0, 0);
}

function TopBar({ onSettings, settingsOpen, dataState }) {
  const generated = dataState?.manifest?.generated_at;
  const lastUpdate = generated
    ? new Date(generated).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
        timeZoneName: "short",
      })
    : "Apr 24, 2026 06:42 UTC";
  const status = !dataState?.ready
    ? "Loading"
    : generated
    ? "Live"
    : "Demo data";
  return (
    <div className="topbar">
      <div className="brand">
        <FreediverLogo />
        <div>
          <div className="brand-name">ShouldIDive</div>
        </div>
        <span className="brand-tag">
          Sea Temp · Water Clarity · Wind · Current · CA Coast 31.8°–37.6°N
        </span>
      </div>
      <div className="topbar-meta">
        <span>
          <span className="dot"></span>
          <strong>{status}</strong> · Last update{" "}
          <span className="mono">{lastUpdate}</span>
        </span>
        <span>
          Sources: <strong>NOAA · IOOS · NASA OB.DAAC · Copernicus</strong>
        </span>
        {/* The MobileShell peek strip carries layer/value/time info on
            phones — the topbar just keeps the brand mark + settings cog
            on small screens (timestamp + sources are hidden via the
            mobile-shell @media block in app.css; that media query
            mirrors MOBILE_QUERY above so JS and CSS agree on what
            counts as "mobile"). */}
        <button
          className="icon-btn"
          aria-label="Settings"
          aria-pressed={settingsOpen}
          onClick={onSettings}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>
    </div>
  );
}

function SettingsPopover({ prefs, setPref, onClose }) {
  return (
    <div className="settings-pop" role="dialog" aria-label="Settings">
      {/* Close button — was missing entirely; on mobile the popover
          covers most of the screen and there was no way to dismiss
          short of tapping the gear again, which most users wouldn't
          discover. */}
      {onClose && (
        <button
          type="button"
          className="sp-close"
          aria-label="Close settings"
          onClick={onClose}
        >
          ×
        </button>
      )}
      <div className="sp-section">
        <div className="sp-h">Theme</div>
        <div className="sp-row">
          <span>Appearance</span>
          <div className="sp-seg">
            <button
              className={prefs.theme === "light" ? "active" : ""}
              onClick={() => setPref("theme", "light")}
            >
              Light
            </button>
            <button
              className={prefs.theme === "dark" ? "active" : ""}
              onClick={() => setPref("theme", "dark")}
            >
              Dark
            </button>
          </div>
        </div>
      </div>
      <div className="sp-section">
        <div className="sp-h">Map</div>
        <div className="sp-row">
          <span>Overlay opacity</span>
          <span className="sp-val mono">{Math.round(prefs.opacity * 100)}%</span>
        </div>
        <input
          type="range"
          min={20}
          max={100}
          step={2}
          value={Math.round(prefs.opacity * 100)}
          onChange={(e) => setPref("opacity", Number(e.target.value) / 100)}
        />
      </div>
      <div className="sp-section">
        <div className="sp-h">Units</div>
        <div className="sp-row">
          <span>Temperature</span>
          <div className="sp-seg">
            <button
              className={prefs.units === "F" ? "active" : ""}
              onClick={() => setPref("units", "F")}
            >
              °F
            </button>
            <button
              className={prefs.units === "C" ? "active" : ""}
              onClick={() => setPref("units", "C")}
            >
              °C
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function DesktopView({ layer, setLayer, composite, setComposite, sstSel, setSstSel, windSel, setWindSel, swellSel, setSwellSel, currentSel, setCurrentSel, opacity, units, dataState, mpaOn, setMpaOn, bathyOn, setBathyOn, viewingDate }) {
  // Timeline layers use a slot-key string derived from their selection
  // state; helpers fall back to a valid slot if the requested one has no
  // data. Chl/viz keep the legacy integer composite.
  const sstHistorySummary = getSstHistorySummary();
  const activeComposite =
    layer === "sst"   ? (sstHistorySummary ? sstSelToSlotKey(sstSel, sstHistorySummary) : composite)
    : layer === "wind"  ? selToSlotKey(windSel,  getWind5dSummary())
    : layer === "swell" ? selToSlotKey(swellSel, getSwell5dSummary())
    : layer === "current" ? currentSelToSlotKey(currentSel, getCurrent5dSummary())
    : composite;
  const isMobile = useIsMobile();

  // Mirror mobile detection into mapData's getFitted module flag so
  // every project()/unproject()/data-overlay/wind-particles render
  // consistently fills the screen instead of leaving huge cream +
  // sky-blue letterbox margins above/below the data on a portrait
  // phone. Desktop keeps the aspect-preserving margins because the
  // side panels (Layer / Saved Spots / How to Read) cover them.
  //
  // Set during render (not useEffect) so child components see the
  // correct fill mode on the SAME render — useEffect would lag by
  // one paint and cause a flicker.
  setFillMode(isMobile);

  const stageRef = useRef(null);
  const [size, setSize] = useState({ w: 1200, h: 700 });
  const [hover, setHover] = useState(null);
  const [activeSpot, setActiveSpot] = useState("lajolla");
  const [infoOpen, setInfoOpen] = useState(true);
  const [controlsOpen, setControlsOpen] = useState(true);
  const [spotsOpen, setSpotsOpen] = useState(true);
  const [legendOpen, setLegendOpen] = useState(true);
  const [selectedMpa, setSelectedMpa] = useState(null);
  const [selectedBathy, setSelectedBathy] = useState(null);
  const [bathyFeatures, setBathyFeatures] = useState(null);

  // Lazy-load bathy features whenever the layer flips on (used for both the
  // SVG markers and the screen-space labels).
  useEffect(() => {
    if (!bathyOn || bathyFeatures) return;
    let cancelled = false;
    loadBathyFeatures().then((fc) => {
      if (cancelled || !fc) return;
      setBathyFeatures(fc.features || []);
    });
    return () => { cancelled = true; };
  }, [bathyOn, bathyFeatures]);

  // Pan/zoom state — viewBox in original svg coords. Initial = full extent.
  const [vb, setVb] = useState({ x: 0, y: 0, w: 1, h: 1 });
  const [isPanning, setIsPanning] = useState(false);
  const panStateRef = useRef(null);
  const MAX_ZOOM = 8;

  useEffect(() => {
    function measure() {
      if (!stageRef.current) return;
      const r = stageRef.current.getBoundingClientRect();
      setSize({ w: r.width, h: r.height });
    }
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
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

  // Stale hover state from the previous layer carries an incompatible val
  // shape (number for sst/chl, {u,v,kt} object for wind). Drop it on switch.
  useEffect(() => {
    setHover(null);
  }, [layer]);

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
    const t = e.target;
    if (t && typeof t.closest === "function" &&
        t.closest(".wind-timeline, .swell-timeline, .mobile-shell, .panel, .moon-widget, .zoom-ctl")) {
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
    const t = e.target;
    return !!(
      t &&
      typeof t.closest === "function" &&
      t.closest(".wind-timeline, .swell-timeline, .mobile-shell, .panel, .moon-widget, .zoom-ctl")
    );
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
  const SLIDER_GUARD_PX = 140;

  function onTouchEnd(e) {
    if (e.touches.length === 0) {
      // All fingers up. If the gesture was actually a tap, drop a pin so
      // the value is readable on phones (which have no hover state).
      const tap = touchTapRef.current;
      const inSliderGuard =
        ((layer === "sst" && sstHistorySummary) || layer === "wind" || layer === "swell" || layer === "current") &&
        tap && tap.startY < SLIDER_GUARD_PX;
      if (tap && !tap.moved && Date.now() - tap.startTime < 350 && !inSliderGuard) {
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

  // Current zoom factor: ratio of full-extent width to visible viewBox width.
  const zoomLevel = size.w > 0 && vb.w > 0 ? size.w / vb.w : 1;

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
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
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
            <>
              {/* No-data hatch — only inside the bbox area; outside is just sea. */}
              <rect
                x={f.marginX}
                y={f.marginY}
                width={f.innerW}
                height={f.innerH}
                fill="url(#noDataHatch)"
                pointerEvents="none"
              />

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
            </>
          );
        })()}

        {/* Real coastline + islands on top of data — naturally masks the overlay */}
        <LandBasemap width={size.w} height={size.h} />

        <MpaLayer
          width={size.w}
          height={size.h}
          active={mpaOn}
          onSelect={setSelectedMpa}
        />

        <BathyLayer
          width={size.w}
          height={size.h}
          active={bathyOn}
          zoomLevel={zoomLevel}
          onSelect={setSelectedBathy}
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
        const cssLeft = ((f.marginX - vb.x) / vb.w) * size.w;
        const cssTop  = ((f.marginY - vb.y) / vb.h) * size.h;
        const scaleX = size.w / vb.w;
        const scaleY = size.h / vb.h;
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

      <MapLabels labels={allLabels} vb={vb} size={size} />

      {/* Moon-phase legend — anchored top-right of the map. On wind/swell
          layers it tracks the time slider (the parent passes the active
          slot's anchor as viewingDate); other layers show "now".
          On mobile, wind/swell also have a top-center timeline; the
          .below-timeline class shifts the moon down to clear it. */}
      <MoonWidget
        date={viewingDate}
        className={((layer === "sst" && sstHistorySummary) || layer === "wind" || layer === "swell" || layer === "current") ? "below-timeline" : ""}
      />

      {/* Timeline scrubbers. Wind/swell are forecasts; SST is historical
          daily satellite data. The map heatmap updates on every drag tick. */}
      {layer === "sst" && sstHistorySummary && (
        <SstTimeline sel={sstSel} setSel={setSstSel} units={units} />
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

      {hover && (
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
              onClick={(e) => { e.stopPropagation(); setMpaOn(!mpaOn); }}
              title={mpaOn ? "MPAs visible · click to hide" : "MPAs hidden · click to show"}
              aria-pressed={mpaOn}
            >
              MPAs
            </button>
            <button
              type="button"
              className={"mpa-pill" + (bathyOn ? " active" : "")}
              onClick={(e) => { e.stopPropagation(); setBathyOn(!bathyOn); }}
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
              title="Surface current speed and direction from HFR observations plus tide/wind inference"
            >
              <span className="lt-label">Current</span>
              <span className="lt-sub">kt</span>
            </button>
            <button
              className={layer === "viz" ? "active" : ""}
              onClick={() => setLayer("viz")}
              title="Predicted dive visibility — model output in feet, not a direct measurement"
            >
              <span className="lt-label">Vis</span>
              <span className="lt-sub">ft</span>
            </button>
          </div>
          {layer === "sst" && sstHistorySummary ? (
            <div className="composite wind-grid-host">
              <div className="composite-label">
                <span>Sea temp trend</span>
                <span className="hint">drag the timeline below</span>
              </div>
              <SstCurrentCard sel={sstSel} units={units} />
              <div className="composite-window">
                <span>Day</span>
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
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(40,130,210)" }}></span>
                  <strong>Blue</strong> means cold — typical Central Coast (54–57°F) and
                  upwelling near Pt. Conception.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(120,220,220)" }}></span>
                  <strong>Cyan</strong> is the transition zone — comfortable for divers in
                  spring suits.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(240,220,110)" }}></span>
                  <strong>Yellow</strong> is warm SoCal summer water (66–70°F). Trunks weather.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(170,20,35)" }}></span>
                  <strong>Red</strong> means anomaly — possible marine heatwave. Watch for
                  kelp stress and harmful algal blooms.
                </p>
              </div>
            ) : layer === "chl" ? (
              <div className="info-section">
                <h4 className="info-h">Water Clarity (Chlorophyll-a)</h4>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(31,58,85)" }}></span>
                  <strong>Deep navy</strong> = gin-clear, low-productivity water. Best
                  visibility for divers and spearos.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(91,141,181)" }}></span>
                  <strong>Mid blue</strong> = typical clear nearshore viz.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(127,160,90)" }}></span>
                  <strong>Olive green</strong> = upwelling — fish food, but viz drops.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(122,90,60)" }}></span>
                  <strong>Warm brown</strong> = peak upwelling or mild bloom. Avoid if water smells off.
                </p>
              </div>
            ) : layer === "wind" ? (
              <div className="info-section">
                <h4 className="info-h">Wind Speed (10 m)</h4>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(170,210,240)" }}></span>
                  <strong>Light blue</strong> = 5 kt or less. Glassy. Paddleable, divable.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(120,200,160)" }}></span>
                  <strong>Green</strong> = ~10 kt. Light breeze. Surface texture, still mellow.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(220,220,100)" }}></span>
                  <strong>Yellow</strong> = ~15 kt. Moderate chop. Boat-handling matters.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(240,160,70)" }}></span>
                  <strong>Orange</strong> = ~20 kt. Small craft advisory territory.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(140,30,90)" }}></span>
                  <strong>Magenta</strong> = 35 kt+. Gale. Stay home.
                </p>
                <p className="info-p">
                  Particles trace direction (the line is "from where wind is coming"). Hover
                  for the exact knots and compass bearing.
                </p>
              </div>
            ) : layer === "current" ? (
              <div className="info-section">
                <h4 className="info-h">Surface Current</h4>
                <p className="info-p">
                  <strong>Beta estimate.</strong> Use this as planning context only, and
                  verify with local observations, boat drift, and in-water feel before
                  committing to a dive.
                </p>
                <p className="info-p">
                  Color shows estimated surface-current speed in knots. Particle trails show
                  where the water is setting, which matters for drift, anchoring, and how stable
                  a kelp-bed dive window will feel.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(125,211,252)" }}></span>
                  <strong>Blue</strong> is weak current, generally easier diving.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(94,234,212)" }}></span>
                  <strong>Teal</strong> is noticeable set. Watch the boat, floatline, and exit.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(250,204,21)" }}></span>
                  <strong>Yellow</strong> is strong enough to matter for most freedivers.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(220,38,38)" }}></span>
                  <strong>Red/purple</strong> is high-risk surface set. Treat as a no-go unless
                  you have strong local confirmation.
                </p>
                <p className="info-p" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                  This is a surface-current product. Reef-depth current can differ near points,
                  kelp, shelves, and island structure.
                </p>
              </div>
            ) : layer === "swell" ? (
              <div className="info-section">
                <h4 className="info-h">Swell · Hs / Tp / Dp</h4>
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
                  <strong>Storm seas</strong> — 20+ ft. Mavericks/Cortes territory.
                </p>
                <p className="info-p" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                  Period flips the feel: a 4 ft / <strong>16 s</strong> day is a clean
                  long-period groundswell; same 4 ft / <strong>8 s</strong> is choppy
                  windswell. Tooltip + the timeline badge expose Tp and Dp directly.
                </p>
              </div>
            ) : (
              <div className="info-section">
                <h4 className="info-h">Predicted Visibility · model output</h4>
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
                  <strong>Good</strong> — 20–30 ft. Cyan; typical CA kelp diving.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(3,105,161)" }}></span>
                  <strong>Very Good</strong> — 30–50 ft. Blue; clean blue water.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(31,77,117)" }}></span>
                  <strong>Excellent</strong> — 50 ft+. Deep navy; once-a-year clarity.
                </p>
              </div>
            )}
            <div className="info-section">
              <h4 className="info-h">{
                layer === "sst"    ? "Historical trend"
                : layer === "wind" ? "Forecast slots"
                : layer === "current" ? "Consistency and reversals"
                : layer === "viz"  ? "How the model works"
                : layer === "swell"? "Period vs height"
                : "Why composite windows?"
              }</h4>
              <p className="info-p">
                {layer === "sst"
                  ? "Sea-surface temperature is not forecast here. The timeline shows the most recent daily MUR satellite analyses so you can see whether a zone is warming, cooling, or holding steady before a dive window."
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
                  ? "NOAA MUR L4 SST. Daily gap-filled satellite analysis, loaded as a 7-day historical trend plus the legacy 1/2/3-day composites."
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
        zoom 7 · 34.6°N −120.3°W · CA Coast bbox 31.8°→37.6°N · −124.0°→−116.8°W
      </div>

      {mpaOn && <CoronadosBanner vb={vb} size={size} />}

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
        sstSel={sstSel} setSstSel={setSstSel}
        windSel={windSel} setWindSel={setWindSel}
        swellSel={swellSel} setSwellSel={setSwellSel}
        currentSel={currentSel} setCurrentSel={setCurrentSel}
        activeComposite={activeComposite}
        units={units}
        dataState={dataState}
        mpaOn={mpaOn} setMpaOn={setMpaOn}
        bathyOn={bathyOn} setBathyOn={setBathyOn}
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

function BathyPopup({ feature, onClose }) {
  const sty = styleForClass(feature.class);
  const isCommunity = feature.class === "community-spot";
  const classLabel =
    feature.class === "seamount" ? "Seamount"
    : feature.class === "bank" ? "Bank"
    : feature.class === "reef" ? "Reef"
    : feature.class === "basin" ? "Basin"
    : feature.class === "trough" ? "Trough"
    : feature.class === "anchorage" ? "Anchorage"
    : feature.class === "landmark" ? "Landmark"
    : "Community spot";
  return (
    <div className="mpa-popup-overlay" onClick={onClose}>
      <div className="mpa-popup" onClick={(e) => e.stopPropagation()}>
        <div className="mpa-popup-head">
          <div>
            <div className="mpa-popup-name">{feature.name}</div>
            <div className="mpa-popup-fullname">{classLabel}</div>
          </div>
          <span
            className="mpa-pill"
            style={{
              background: "transparent",
              borderColor: sty.color,
              color: sty.color,
            }}
          >
            {sty.glyph} {feature.shortName || feature.name}
          </span>
        </div>

        {(feature.minDepthFt || feature.minDepthM) && (
          <p className="mpa-popup-meta mono">
            {feature.minDepthFt ? `Min depth ${feature.minDepthFt} ft` : ""}
            {feature.minDepthFt && feature.minDepthM ? ` (${feature.minDepthM} m)` : ""}
            {!feature.minDepthFt && feature.minDepthM ? `Min depth ${feature.minDepthM} m` : ""}
          </p>
        )}

        {feature.description && (
          <p className="mpa-popup-body">{feature.description}</p>
        )}

        <p className="mpa-popup-meta mono">
          Source: {feature.source || "n/a"}
        </p>

        {isCommunity && (
          <p className="mpa-popup-disclaimer">
            Community-sourced. Verify locally and stay clear of MPAs.
          </p>
        )}
        {!isCommunity && (
          <p className="mpa-popup-disclaimer">
            For navigation, verify with current NOAA charts.
          </p>
        )}
      </div>
    </div>
  );
}

// US-Mexico maritime boundary is at ~32.534°N. When the MPA layer is on
// AND the visible viewBox dips below that, surface a small disclaimer.
// Dismissable: an × button hides it for the rest of the page session
// (mobile users repeatedly hit it covering the bottom strip when
// they're zoomed in on Coronados, which is half the reason to look at
// that part of the map).
function CoronadosBanner({ vb, size }) {
  const [dismissed, setDismissed] = useState(false);
  if (!vb || !size.h) return null;
  if (dismissed) return null;
  const [, visibleSouthLat] = unproject(0, vb.y + vb.h, size.w, size.h);
  if (visibleSouthLat > 32.534) return null;
  return (
    <div className="mpa-banner">
      <span>
        MPA data covers California waters only. The Coronados sit inside
        Mexico's Islas del Pacífico Biosphere Reserve — see{" "}
        <a href="https://www.gob.mx/conanp" target="_blank" rel="noreferrer">CONANP</a>.
      </span>
      <button
        className="mpa-banner-close"
        onClick={() => setDismissed(true)}
        aria-label="Dismiss notice"
      >
        ×
      </button>
    </div>
  );
}

function MpaPopup({ mpa, onClose }) {
  const style = styleForType(mpa.type);
  const officialUrl =
    "https://wildlife.ca.gov/Conservation/Marine/MPAs/Network";
  const verdict = verdictForType(mpa.type);
  return (
    <div className="mpa-popup-overlay" onClick={onClose}>
      <div className="mpa-popup" onClick={(e) => e.stopPropagation()}>
        <div className="mpa-popup-head">
          <div>
            <div className="mpa-popup-name">{mpa.name}</div>
            <div className="mpa-popup-fullname">{fullNameForType(mpa.type)}</div>
          </div>
          <span className="mpa-pill" style={{ background: style.fill, borderColor: style.stroke, color: style.stroke }}>
            {mpa.type}
          </span>
        </div>
        <div className={"mpa-verdict mpa-verdict-" + verdict.kind}>
          <span className="mpa-verdict-icon">{verdict.icon}</span>
          <strong>{verdict.label}</strong>
        </div>
        <p className="mpa-popup-body">
          {verdict.kind === "no" && (
            <>Take of any living marine resource is generally prohibited inside this area.</>
          )}
          {verdict.kind === "limited" && (
            <>Limited recreational take is allowed — specific species and methods only. <strong>Verify with CDFW before harvesting.</strong></>
          )}
          {verdict.kind === "ok" && (
            <>Most recreational take is allowed within this area; specific exclusions may apply.</>
          )}
        </p>
        <p className="mpa-popup-meta mono">
          {mpa.areaKm2 ? `${mpa.areaKm2} km² · ` : ""}
          {mpa.ccrCitation || "CCR Title 14 §632"}
        </p>
        <a
          className="mpa-popup-link"
          href={officialUrl}
          target="_blank"
          rel="noreferrer"
        >
          ↗ Official CDFW regulation page
        </a>
        <p className="mpa-popup-disclaimer">
          Information shown is for planning purposes only. Verify with CDFW before harvesting.
        </p>
      </div>
    </div>
  );
}

function verdictForType(type) {
  if (!type) return { kind: "limited", icon: "⚠", label: "CHECK RULES" };
  const t = type.toUpperCase();
  if (t.includes("NO-TAKE") || t === "SMR" || t === "FMR")
    return { kind: "no", icon: "⛔", label: "NO TAKE" };
  if (t === "SMP" || t === "SMRMA")
    return { kind: "ok", icon: "✓", label: "TAKE ALLOWED" };
  return { kind: "limited", icon: "⚠", label: "LIMITED TAKE" };
}

function fullNameForType(type) {
  const map = {
    SMR: "State Marine Reserve",
    SMCA: "State Marine Conservation Area",
    "SMCA (No-Take)": "State Marine Conservation Area · No Take",
    SMP: "State Marine Park",
    SMRMA: "State Marine Recreational Management Area",
    FMR: "Federal Marine Reserve",
    FMCA: "Federal Marine Conservation Area",
    "Special Closure": "Special Closure",
  };
  return map[type] || "Marine Protected Area";
}

function Tooltip({ x, y, layer, composite, lng, lat, units }) {
  let title, big, sub;
  if (layer === "sst") {
    const val = getSST(lng, lat, composite);
    title = "Sea Surface Temp";
    if (!Number.isFinite(val)) {
      big = "—";
      sub = "no data here";
    } else {
      const f = val * 9 / 5 + 32;
      big = units === "F" ? `${f.toFixed(1)}°F` : `${val.toFixed(1)}°C`;
      sub =
        f < 55 ? "Frigid · drysuit"
        : f < 60 ? "Cold · 7 mm"
        : f < 70 ? "Cool · 5 mm"
        : f < 75 ? "Mild · 3 mm"
        : f < 80 ? "Warm · springsuit"
        : "Hot · trunks";
    }
  } else if (layer === "chl") {
    const val = getChl(lng, lat, composite);
    title = "Chl-a · Water Clarity";
    if (!Number.isFinite(val)) {
      big = "—";
      sub = "no data here";
    } else {
      big = `${val.toFixed(2)} mg/m³`;
      sub =
        val < 0.3 ? "Gin clear"
        : val < 1.0 ? "Clear"
        : val < 3.5 ? "Moderate"
        : val < 10  ? "Productive"
        : "Bloom";
    }
  } else if (layer === "wind") {
    title = "Wind · 10 m";
    const { u, v } = getWindUV(lng, lat, composite);
    const kt = getWindSpeed(lng, lat, composite);
    if (Number.isFinite(kt) && Number.isFinite(u) && Number.isFinite(v)) {
      const deg = windCompass(u, v);
      big = `${kt.toFixed(1)} kt`;
      sub = `from ${windCardinal(deg)} (${Math.round(deg)}°)`;
    } else {
      big = "—";
      sub = "no data";
    }
  } else if (layer === "current") {
    title = "Surface Current";
    const { u, v } = getCurrentUV(lng, lat, composite);
    const kt = getCurrentSpeed(lng, lat, composite);
    if (Number.isFinite(kt) && Number.isFinite(u) && Number.isFinite(v)) {
      const deg = (Math.atan2(u, v) * 180 / Math.PI + 360) % 360;
      big = `${kt.toFixed(1)} kt`;
      sub = `setting to ${windCardinal(deg)} (${Math.round(deg)} deg)`;
    } else {
      big = "—";
      sub = "no current estimate";
    }
  } else if (layer === "swell") {
    title = "Swell · WaveWatch III";
    const w = getSwell5dStats(lng, lat, composite);
    if (Number.isFinite(w.hs)) {
      const ft = w.hs * 3.28084;
      const tpStr = Number.isFinite(w.tp) ? ` · ${w.tp.toFixed(0)} s` : "";
      const dpStr = Number.isFinite(w.dp)
        ? ` · ${windCardinal(w.dp)} ${Math.round(w.dp)}°`
        : "";
      const periodTag =
        !Number.isFinite(w.tp) ? ""
        : w.tp >= 12 ? "long-period groundswell"
        : w.tp >= 8  ? "mixed swell"
        : "short-period windswell";
      big = `${ft.toFixed(1)} ft`;
      sub = `${tpStr.replace(/^ · /, "")}${dpStr}${periodTag ? `\n${periodTag}` : ""}`.trim();
    } else {
      big = "—";
      sub = "no data";
    }
  } else {
    // viz layer — model prediction, NOT a measurement
    title = "Predicted Visibility";
    const ft = getVizFt(lng, lat, composite);
    if (Number.isFinite(ft)) {
      const cat =
        ft < 10 ? "Poor"
        : ft < 20 ? "Fair"
        : ft < 30 ? "Good"
        : ft < 50 ? "Very Good"
        : "Excellent";
      big = `~${Math.round(ft)} ft`;
      sub = `${cat} · model output`;
    } else {
      big = "—";
      sub = "no prediction here";
    }
  }
  return (
    <div className="tooltip" style={{ left: x, top: y }}>
      <div className="tooltip-title">{title}</div>
      <div className="tooltip-val">{big}</div>
      <div className="tooltip-sub">{sub}</div>
      <div className="tooltip-coord">
        {lat.toFixed(3)}°N · {Math.abs(lng).toFixed(3)}°W
      </div>
    </div>
  );
}
