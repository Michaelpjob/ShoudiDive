import { useEffect, useRef, useState } from "react";
import Basemap from "./components/Basemap.jsx";
import DataOverlay from "./components/DataOverlay.jsx";
import WindParticles from "./components/WindParticles.jsx";
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
  windCompass,
  windCardinal,
  dataDates,
  isReal,
  getDataState,
} from "./lib/dataSource.js";

// Time filter is layer-aware: SST/chl use composite windows, wind uses forecast slots.
const TIME_OPTIONS = {
  sst:  { label: "Composite",      helper: "rolling window",      buttons: ["1 Day", "2 Day", "3 Day"], tags: ["freshest", "balanced", "best cover"] },
  chl:  { label: "Composite",      helper: "rolling window",      buttons: ["1 Day", "2 Day", "3 Day"], tags: ["freshest", "balanced", "best cover"] },
  wind: { label: "Forecast Step",  helper: "HRRR hourly model",   buttons: ["Now",   "+6h",   "+24h"],  tags: ["analysis", "afternoon", "tomorrow"] },
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
const DEFAULT_PREFS = { theme: "light", opacity: 0.62, units: "F" };

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
        <div className="brand-mark"></div>
        <div>
          <div className="brand-name">CA Coast Conditions</div>
        </div>
        <span className="brand-tag">
          Sea Temp · Water Clarity · CA Coast 32.4°–37.6°N
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

function DesktopView({ layer, setLayer, composite, setComposite, opacity, units, dataState }) {
  const stageRef = useRef(null);
  const [size, setSize] = useState({ w: 1200, h: 700 });
  const [hover, setHover] = useState(null);
  const [activeSpot, setActiveSpot] = useState("lajolla");
  const [infoOpen, setInfoOpen] = useState(true);

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

  function onMove(e) {
    const r = stageRef.current.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    const [lng, lat] = unproject(x, y, size.w, size.h);
    let val;
    if (layer === "sst") val = getSST(lng, lat, composite);
    else if (layer === "chl") val = getChl(lng, lat, composite);
    else val = { ...getWindUV(lng, lat, composite), kt: getWindSpeed(lng, lat, composite) };
    setHover({ x, y, lng, lat, val });
  }
  function onLeave() {
    setHover(null);
  }

  const fallbackText =
    layer === "wind"
      ? "now"
      : composite === 1
      ? "Apr 24, 2026"
      : composite === 2
      ? "Apr 23–24, 2026"
      : "Apr 22–24, 2026";
  const compositeText = formatWindow(dataDates(layer, composite), fallbackText, layer);
  const layerIsReal = isReal(layer, composite);
  const timeOpts = TIME_OPTIONS[layer];

  return (
    <div className="map-stage" ref={stageRef} onMouseMove={onMove} onMouseLeave={onLeave}>
      <svg className="map-svg" viewBox={`0 0 ${size.w} ${size.h}`} preserveAspectRatio="none">
        <Basemap width={size.w} height={size.h} />
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

        <g className="spot-pins">
          {SAVED_SPOTS.map((s) => {
            const [x, y] = project(s.lng, s.lat, size.w, size.h);
            const isActive = s.id === activeSpot;
            return (
              <g
                key={s.id}
                style={{ cursor: "pointer" }}
                onClick={() => setActiveSpot(s.id)}
              >
                <circle
                  cx={x}
                  cy={y}
                  r={isActive ? 7 : 4}
                  fill="var(--bg-panel-solid)"
                  stroke="var(--ink)"
                  strokeWidth={isActive ? 2.2 : 1.4}
                />
                {isActive && <circle cx={x} cy={y} r="3" fill="var(--ink)" />}
                <text
                  x={x + 10}
                  y={y + 4}
                  fontSize="10.5"
                  fill="var(--ink)"
                  fontFamily="Inter, sans-serif"
                  fontWeight="500"
                  style={{
                    paintOrder: "stroke",
                    stroke: "var(--bg)",
                    strokeWidth: 3,
                    strokeLinejoin: "round",
                  }}
                >
                  {s.name}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      {hover && (
        <Tooltip
          x={hover.x}
          y={hover.y}
          layer={layer}
          val={hover.val}
          lng={hover.lng}
          lat={hover.lat}
          units={units}
        />
      )}

      <div className="panel controls-tl">
        <div className="panel-header">
          <span className="panel-title">Layer</span>
          <span className="panel-title mono" style={{ color: "var(--ink-3)" }}>
            v1.0
          </span>
        </div>
        <div className="panel-body">
          <div className="layer-toggle layer-toggle-3" role="tablist">
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
          </div>
          <div className="composite">
            <div className="composite-label">
              <span>{timeOpts.label}</span>
              <span className="hint">{timeOpts.helper}</span>
            </div>
            <div className="composite-buttons">
              {[1, 2, 3].map((d, i) => (
                <button
                  key={d}
                  className={composite === d ? "active" : ""}
                  onClick={() => setComposite(d)}
                >
                  <span className="cb-num">{timeOpts.buttons[i]}</span>
                  <span className="cb-tag">{timeOpts.tags[i]}</span>
                </button>
              ))}
            </div>
            <div className="composite-window">
              <span>{layer === "wind" ? "Valid" : "Window"}</span>
              <span className="mono">{compositeText}</span>
            </div>
          </div>
        </div>
      </div>

      {infoOpen ? (
        <div className="panel info-tr">
          <div className="panel-header">
            <span className="panel-title">How to read this</span>
            <button
              className="icon-btn"
              onClick={() => setInfoOpen(false)}
              aria-label="Collapse"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
          </div>
          <div className="panel-body" style={{ overflowY: "auto" }}>
            {layer === "sst" ? (
              <div className="info-section">
                <h4 className="info-h">Sea Surface Temperature</h4>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(40,130,210)" }}></span>
                  <strong>Blue</strong> means cold — typical Central Coast (12–14°C) and
                  upwelling near Pt. Conception.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(120,220,220)" }}></span>
                  <strong>Cyan</strong> is the transition zone — comfortable for divers in
                  spring suits.
                </p>
                <p className="info-p">
                  <span className="swatch" style={{ background: "rgb(240,220,110)" }}></span>
                  <strong>Yellow</strong> is warm SoCal summer water (19–21°C). Trunks weather.
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
            ) : (
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
            )}
            <div className="info-section">
              <h4 className="info-h">{layer === "wind" ? "Forecast slots" : "Why composite windows?"}</h4>
              <p className="info-p">
                {layer === "wind"
                  ? "HRRR is NOAA's hourly 3-km weather model. Now is the freshest analysis. +6h is your afternoon look-ahead. +24h is tomorrow morning. Updated every hour."
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
                  : "NOAA Coral Reef Watch · NASA OB.DAAC MODIS-Aqua · Copernicus GLO-MFC. Daily L3 composites, ~1 km grid, regridded to bounding box."}
              </p>
            </div>
          </div>
        </div>
      ) : (
        <button
          className="info-toggle"
          style={{ position: "absolute", top: 56, right: 12, zIndex: 20 }}
          onClick={() => setInfoOpen(true)}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
          How to read this
        </button>
      )}

      <div className="panel spots-bl">
        <div className="panel-header">
          <span className="panel-title">Saved Spots</span>
          <button className="icon-btn" aria-label="Add">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
        </div>
        <div className="panel-body">
          <div className="spots-list">
            {SAVED_SPOTS.map((s) => {
              let v, valTxt, unit, col;
              if (layer === "sst") {
                v = getSST(s.lng, s.lat, composite);
                valTxt = units === "F" ? `${(v * 9 / 5 + 32).toFixed(1)}` : `${v.toFixed(1)}`;
                unit = `°${units}`;
                col = sstColor(v);
              } else if (layer === "chl") {
                v = getChl(s.lng, s.lat, composite);
                valTxt = `${v.toFixed(2)}`;
                unit = "mg/m³";
                col = chlColor(v);
              } else {
                v = getWindSpeed(s.lng, s.lat, composite);
                valTxt = Number.isFinite(v) ? `${v.toFixed(1)}` : "—";
                unit = "kt";
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
        </div>
      </div>

      <div className="panel legend-br">
        <div className="panel-header">
          <span className="panel-title">
            {layer === "sst" ? "Sea Surface Temperature"
              : layer === "chl" ? "Water Clarity (Chlorophyll-a)"
              : "Wind Speed (10 m)"}
          </span>
          <span className="panel-title mono" style={{ color: "var(--ink-3)" }}>
            {layer === "sst" ? "°C" : layer === "chl" ? "mg/m³" : "kt"}
          </span>
        </div>
        <div className="panel-body">
          <div className={`legend-bar ${layer}`}></div>
          <div className="legend-ticks">
            {layer === "sst" ? (
              <>
                <span>9</span><span>13</span><span>16</span><span>19</span><span>22</span><span>25</span>
              </>
            ) : layer === "chl" ? (
              <>
                <span>0.05</span><span>0.3</span><span>1.0</span><span>3.5</span><span>20+</span>
              </>
            ) : (
              <>
                <span>0</span><span>5</span><span>10</span><span>15</span><span>20</span><span>25</span><span>35+</span>
              </>
            )}
          </div>
          <div className="legend-meta">
            <span>
              {layer === "sst" ? "Cold upwelling → Heatwave"
                : layer === "chl" ? "Gin-clear → Bloom"
                : "Calm → Gale"}
            </span>
            <span>
              <strong>{compositeText}</strong>
              {layer === "wind" ? " · HRRR" : ` · ${composite}-day composite`}
              {!layerIsReal && dataState?.ready && " · demo"}
            </span>
          </div>
        </div>
      </div>

      <div className="zoom-ctl">
        <button aria-label="Zoom in">+</button>
        <button aria-label="Zoom out">−</button>
        <button aria-label="Recenter">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 1v3M12 20v3M1 12h3M20 12h3" />
          </svg>
        </button>
      </div>

      <div className="attribution">
        zoom 7 · 34.6°N −120.3°W · CA Coast bbox 32.4°→37.6°N · −124.0°→−117.0°W
      </div>
    </div>
  );
}

function Tooltip({ x, y, layer, val, lng, lat, units }) {
  let title, big, sub;
  if (layer === "sst") {
    title = "Sea Surface Temp";
    if (units === "F") {
      big = `${(val * 9 / 5 + 32).toFixed(1)}°F`;
      sub = `${val.toFixed(1)}°C`;
    } else {
      big = `${val.toFixed(1)}°C`;
      sub = `${(val * 9 / 5 + 32).toFixed(1)}°F`;
    }
  } else if (layer === "chl") {
    title = "Chl-a · Water Clarity";
    big = `${val.toFixed(2)} mg/m³`;
    const tier =
      val < 0.3 ? "Gin clear"
      : val < 1.0 ? "Clear"
      : val < 3.5 ? "Moderate"
      : val < 10  ? "Productive"
      : "Bloom";
    sub = tier;
  } else {
    title = "Wind · 10 m";
    if (Number.isFinite(val.kt) && Number.isFinite(val.u) && Number.isFinite(val.v)) {
      const deg = windCompass(val.u, val.v);
      big = `${val.kt.toFixed(1)} kt`;
      sub = `from ${windCardinal(deg)} (${Math.round(deg)}°)`;
    } else {
      big = "—";
      sub = "no data";
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
