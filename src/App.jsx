import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
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
  SAVED_SPOTS,
} from "./lib/mapData.js";
import {
  loadManifest,
  subscribe,
  getSST,
  getChl,
  getWindSpeed,
  getWindUV,
  getVizFt,
  windCompass,
  windCardinal,
  windSource,
  dataDates,
  isReal,
  getDataState,
} from "./lib/dataSource.js";

// Reactive viewport-width hook. Returns true at <760 px so we can branch the
// layout between the floating-panel desktop UI and a bottom-sheet mobile UI.
const MOBILE_QUERY = "(max-width: 760px)";
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

// Minimal stroke-based freediver — head, body streamlined diagonally,
// monofin chevron at the tail. Inherits color from `currentColor` so it
// adapts to light/dark themes via the surrounding `.brand-mark` color.
function FreediverLogo() {
  return (
    <svg
      className="brand-mark"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {/* monofin chevron at the tail */}
      <path d="M2.5 4.5 L4.5 6.5 L2.5 8.5" />
      {/* body line from fin to shoulder */}
      <path d="M5 6.5 L13 13.5" />
      {/* head */}
      <circle cx="14.4" cy="14.6" r="1.7" fill="currentColor" stroke="none" />
      {/* arms streamlined forward into the depth */}
      <path d="M15.7 16 L21 21" />
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

// Time filter is layer-aware: SST/chl use composite windows, wind uses
// forecast slots, viz (predicted) is a single 'now' slot today.
const TIME_OPTIONS = {
  sst:  { label: "Composite",      helper: "rolling window",      buttons: ["1 Day", "2 Day", "3 Day"],         tags: ["freshest", "balanced", "best cover"] },
  chl:  { label: "Composite",      helper: "rolling window",      buttons: ["1 Day", "2 Day", "3 Day"],         tags: ["freshest", "balanced", "best cover"] },
  wind: { label: "Forecast Step",  helper: "HRRR + GFS",          buttons: ["Now",   "+6h",   "+24h", "+72h"],  tags: ["analysis", "afternoon", "tomorrow", "3-day"] },
  viz:  { label: "Prediction",     helper: "model output",        buttons: ["Now"],                              tags: ["best estimate"] },
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const dataState = useDataVersion();

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
        opacity={prefs.opacity}
        units={prefs.units}
        dataState={dataState}
        mpaOn={prefs.mpaOn}
        setMpaOn={(v) => setPref("mpaOn", v)}
        bathyOn={prefs.bathyOn}
        setBathyOn={(v) => setPref("bathyOn", v)}
      />
    </div>
  );
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
          Sea Temp · Water Clarity · Wind · CA Coast 31.8°–37.6°N
        </span>
      </div>
      <div className="topbar-meta">
        <span>
          <span className="dot"></span>
          <strong>{status}</strong> · Last update{" "}
          <span className="mono">{lastUpdate}</span>
        </span>
        <span>
          Sources: <strong>NOAA · NASA OB.DAAC · Copernicus</strong>
        </span>
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

function SettingsPopover({ prefs, setPref }) {
  return (
    <div className="settings-pop" role="dialog" aria-label="Settings">
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

function DesktopView({ layer, setLayer, composite, setComposite, opacity, units, dataState, mpaOn, setMpaOn, bathyOn, setBathyOn }) {
  const isMobile = useIsMobile();
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
    e.preventDefault?.();
    const r = stageRef.current.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    zoomAt(x, y, e.deltaY < 0 ? 1 / 1.2 : 1.2);
  }

  function onMouseDown(e) {
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

  // ---- Touch handlers: 1-finger pan, 2-finger pinch zoom -----------------
  const touchStateRef = useRef(null);

  function onTouchStart(e) {
    const r = stageRef.current.getBoundingClientRect();
    if (e.touches.length === 1) {
      const t = e.touches[0];
      touchStateRef.current = {
        kind: "pan",
        startScreenX: t.clientX - r.left,
        startScreenY: t.clientY - r.top,
        startVb: vb,
      };
      setHover(null);
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
      setHover(null);
    }
  }

  function onTouchMove(e) {
    const ts = touchStateRef.current;
    if (!ts) return;
    e.preventDefault();
    const r = stageRef.current.getBoundingClientRect();
    if (ts.kind === "pan" && e.touches.length === 1) {
      const t = e.touches[0];
      const x = t.clientX - r.left;
      const y = t.clientY - r.top;
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

  function onTouchEnd(e) {
    if (e.touches.length === 0) {
      touchStateRef.current = null;
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
    }
  }

  const fallbackText =
    layer === "wind"
      ? "now"
      : layer === "viz"
      ? "now"
      : composite === 1
      ? "Apr 24, 2026"
      : composite === 2
      ? "Apr 23–24, 2026"
      : "Apr 22–24, 2026";
  const compositeText = formatWindow(dataDates(layer, composite), fallbackText, layer);
  const layerIsReal = isReal(layer, composite);
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

        {/* Sea + graticule under everything */}
        <SeaBasemap width={size.w} height={size.h} />

        {/* No-data hatch — visible only where the data overlay has
            transparent cells (i.e. NaN / missing satellite data). */}
        <rect
          x="0"
          y="0"
          width={size.w}
          height={size.h}
          fill="url(#noDataHatch)"
          pointerEvents="none"
        />

        {/* Data overlay sits on the sea; land on top will clip it visually */}
        <DataOverlay
          width={size.w}
          height={size.h}
          layer={layer}
          composite={composite}
          opacity={opacity}
          dataReady={dataState?.ready}
        />
        <foreignObject x="0" y="0" width={size.w} height={size.h}>
          <WindParticles
            width={size.w}
            height={size.h}
            composite={composite}
            dataReady={dataState?.ready}
            active={layer === "wind"}
          />
        </foreignObject>

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

      <MapLabels labels={allLabels} vb={vb} size={size} />

      {hover && (
        <Tooltip
          x={hover.x}
          y={hover.y}
          layer={layer}
          composite={composite}
          lng={hover.lng}
          lat={hover.lat}
          units={units}
        />
      )}

      {isMobile && (
        <MobileSheet
          layer={layer} setLayer={setLayer}
          composite={composite} setComposite={setComposite}
          units={units}
          dataState={dataState}
          mpaOn={mpaOn} setMpaOn={setMpaOn}
          bathyOn={bathyOn} setBathyOn={setBathyOn}
          activeSpot={activeSpot} setActiveSpot={setActiveSpot}
          timeOpts={timeOpts}
          compositeText={compositeText}
          layerIsReal={layerIsReal}
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
          <div className="layer-toggle layer-toggle-4" role="tablist">
            <button
              className={layer === "sst" ? "active" : ""}
              onClick={() => setLayer("sst")}
            >
              <span className="lt-label">Sea Temp</span>
              <span className="lt-sub">°{units}</span>
            </button>
            <button
              className={layer === "chl" ? "active" : ""}
              onClick={() => setLayer("chl")}
            >
              <span className="lt-label">Visibility</span>
              <span className="lt-sub">mg/m³</span>
            </button>
            <button
              className={layer === "wind" ? "active" : ""}
              onClick={() => setLayer("wind")}
            >
              <span className="lt-label">Wind</span>
              <span className="lt-sub">10 m · kt</span>
            </button>
            <button
              className={layer === "viz" ? "active" : ""}
              onClick={() => setLayer("viz")}
              title="Predicted visibility — model output, not a measurement"
            >
              <span className="lt-label">Forecast</span>
              <span className="lt-sub">predicted</span>
            </button>
          </div>
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
              <span>{layer === "wind" ? "Valid" : "Window"}</span>
              <span className="mono">{compositeText}</span>
            </div>
          </div>
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
                  <span className="swatch" style={{ background: "rgb(10,50,140)" }}></span>
                  <strong>Deep blue</strong> = gin-clear, low-productivity water. Best
                  visibility for divers and spearos.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(60,200,180)" }}></span>
                  <strong>Teal</strong> = moderate productivity. Normal coastal viz.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(110,210,90)" }}></span>
                  <strong>Green</strong> = productive — fish food, but viz drops to a few feet.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(50,130,40)" }}></span>
                  <strong>Dense green</strong> = bloom or red tide. Avoid if water smells off.
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
                  <strong>Poor</strong> — 0–10 ft. Pea-soup; consider another day.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(234,179,8)" }}></span>
                  <strong>Fair</strong> — 10–20 ft. Murky but workable.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(132,204,22)" }}></span>
                  <strong>Good</strong> — 20–30 ft. Solid CA-coast day.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(22,163,74)" }}></span>
                  <strong>Very Good</strong> — 30–50 ft. Strong; gin-clear pockets.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(14,165,233)" }}></span>
                  <strong>Excellent</strong> — 50 ft+. Tropical-grade.
                </p>
              </div>
            )}
            <div className="info-section">
              <h4 className="info-h">{
                layer === "wind"  ? "Forecast slots"
                : layer === "viz" ? "How the model works"
                : "Why composite windows?"
              }</h4>
              <p className="info-p">
                {layer === "wind"
                  ? "HRRR is NOAA's hourly 3-km weather model. Now is the freshest analysis. +6h is your afternoon look-ahead. +24h is tomorrow morning. Updated every hour."
                  : layer === "viz"
                  ? "A zone-aware stack (3 latitude × 3 distance-from-shore) translates today's chl-a into a Secchi depth, then nudges it for storm-driven bottom stir, river/precip runoff, tidal mixing, kelp shading, and substrate. The 'best estimate' is the median; hover any cell to see the value."
                  : "Clouds wipe out single-day satellite imagery off CA, especially the summer marine layer. A 2- or 3-day window backfills with the most recent valid pixel — 1-day is freshest, 3-day has the best coverage."}
              </p>
            </div>
            <div className="info-section">
              <h4 className="info-h">Sources & cadence</h4>
              <p
                className="info-p"
                style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10.5 }}
              >
                {layer === "wind"
                  ? "NOAA HRRR (3-km, hourly). 10-m UGRD/VGRD via NOMADS byte-range fetch. Regridded to ~5 km."
                  : layer === "viz"
                  ? "MUR SST · VIIRS chl-a · HRRR + GFS wind (5d) · WaveWatch III (3d max) · CPC precip · USGS river discharge · NOAA CO-OPS tides · MODIS-Aqua climatology. Recomputed daily."
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
                v = getSST(s.lng, s.lat, composite);
                if (Number.isFinite(v)) {
                  valTxt = units === "F" ? `${(v * 9 / 5 + 32).toFixed(1)}` : `${v.toFixed(1)}`;
                  col = sstColor(v);
                } else {
                  valTxt = "—";
                  col = "var(--ink-3)";
                }
                unit = `°${units}`;
              } else if (layer === "chl") {
                v = getChl(s.lng, s.lat, composite);
                if (Number.isFinite(v)) {
                  valTxt = `${v.toFixed(2)}`;
                  col = chlColor(v);
                } else {
                  valTxt = "—";
                  col = "var(--ink-3)";
                }
                unit = "mg/m³";
              } else if (layer === "wind") {
                v = getWindSpeed(s.lng, s.lat, composite);
                valTxt = Number.isFinite(v) ? `${v.toFixed(1)}` : "—";
                unit = "kt";
                col = "var(--ink-2)";
              } else {
                v = getVizFt(s.lng, s.lat, composite);
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
              : "Predicted Visibility"}
            {layer === "viz" && <span className="predicted-badge">PREDICTED</span>}
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="panel-title mono" style={{ color: "var(--ink-3)" }}>
              {layer === "sst" ? `°${units}` : layer === "chl" ? "mg/m³" : layer === "wind" ? "kt" : "ft"}
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
                : "Poor → Excellent"}
            </span>
            <span>
              <strong>{compositeText}</strong>
              {layer === "wind"
                ? ` · ${windSource(composite) || "HRRR"}`
                : layer === "viz"
                ? ` · model output`
                : ` · ${composite}-day composite`}
              {!layerIsReal && dataState?.ready && " · no data"}
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

        {Array.isArray(feature.commonSpecies) && feature.commonSpecies.length > 0 && (
          <p className="mpa-popup-body" style={{ marginTop: -4 }}>
            <strong>Commonly targeted:</strong> {feature.commonSpecies.join(", ")}.
          </p>
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
function CoronadosBanner({ vb, size }) {
  if (!vb || !size.h) return null;
  const [, visibleSouthLat] = unproject(0, vb.y + vb.h, size.w, size.h);
  if (visibleSouthLat > 32.534) return null;
  return (
    <div className="mpa-banner">
      MPA data covers California waters only. The Coronados sit inside Mexico's
      Islas del Pacífico Biosphere Reserve — see{" "}
      <a href="https://www.gob.mx/conanp" target="_blank" rel="noreferrer">CONANP</a>.
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
        : f < 65 ? "Cool · 5 mm"
        : f < 70 ? "Mild · 3 mm"
        : f < 75 ? "Warm · springsuit"
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
