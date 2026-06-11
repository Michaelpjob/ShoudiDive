// DesktopLayout — desktop-only chrome that sits on top of the map stage:
// the hover Tooltip, the four collapsible panels (Layer chips,
// How-to-read, Saved Spots, Legend), the zoom +/- recenter buttons,
// and the bbox attribution strip.
//
// Extracted from MapShell.jsx as Stage 4b of the App.jsx refactor on
// 2026-05-24. Pure mechanical relocation: every JSX element, helper,
// and ref is byte-equivalent to what MapShell was rendering before.
// MapShell still owns the gesture handlers, the SVG content, the
// timeline scrubbers, and the popup state; this component receives
// what it needs as props.
//
// The panel-open state (controlsOpen / infoOpen / spotsOpen /
// legendOpen) is intentionally local to DesktopLayout — no consumer
// outside this file looks at it, and putting it here keeps the
// panel UX self-contained.
//
// Rendered unconditionally (no `!isMobile && (...)` gate) — the four
// panels were CSS-hidden on mobile before Stage 4b and stay that way,
// preserving the prior behavior exactly. The Tooltip inside has its
// own `!isMobile` gate, also unchanged.

import { useState } from "react";

import Tooltip from "./Tooltip.jsx";
import { SstTrendChip, SstSparkline } from "./SstTrendBits.jsx";
import {
  SstCurrentCard,
  SstModeToggle,
} from "./SstTimeline.jsx";
import { WindCurrentSelectionCard } from "./WindDayGrid.jsx";
import { SwellCurrentCard } from "./SwellTimeline.jsx";
import { CurrentCurrentCard } from "./CurrentTimeline.jsx";

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
  getSwell5dStats,
} from "../lib/dataSource.js";
import { sstColor, chlColor, SAVED_SPOTS, BBOX } from "../lib/mapData.js";
import { activeRegion } from "../lib/region.js";
import ConfidenceDot from "./ConfidenceDot.jsx";

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

// Compact value readout for the legend metadata strip when the user is
// hovering over the map. Returns null if the cursor doesn't have data.
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

export default function DesktopLayout({
  // Layer + sst-mode state (driven by App.jsx + useTimelineSelections)
  layer, setLayer,
  composite, setComposite,
  sstMode, setSstMode, sstActiveSel, activeSstMode,
  hasSstTimeline, hasSstHistory, hasSstForecast,
  windSel, setWindSel,
  swellSel, currentSel,
  // Prefs read by panels
  units,
  // Map-driven UI state
  hover,
  // Derived values computed in MapShell + passed down
  activeComposite, compositeText, timeOpts, layerIsReal,
  // Saved-spots panel state (analytics-wrapped setter lives in MapShell)
  activeSpot, setActiveSpot,
  // MPA/bathy toggles (state owned by App, side-effect wrappers in MapShell)
  mpaOn, bathyOn, updateMpaOn, updateBathyOn,
  fieldReportsOn, updateFieldReportsOn,
  // Map viewport (zoom +/− buttons + recenter)
  size, zoomAt, resetView,
  // Manifest data state (legend "no data" indicator)
  dataState,
  // Mobile guard — Tooltip honors !isMobile inside this component
  isMobile,
}) {
  const [infoOpen, setInfoOpen] = useState(true);
  const [controlsOpen, setControlsOpen] = useState(true);
  const [spotsOpen, setSpotsOpen] = useState(true);
  const [legendOpen, setLegendOpen] = useState(true);

  return (
    <>
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
            <button
              type="button"
              className={"mpa-pill" + (fieldReportsOn ? " active" : "")}
              onClick={(e) => { e.stopPropagation(); updateFieldReportsOn(!fieldReportsOn); }}
              title={fieldReportsOn ? "Field reports visible · click to hide" : "Field reports hidden · click to show"}
              aria-pressed={fieldReportsOn}
            >
              Reports
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
              <ConfidenceDot layer="sst" className="lt-conf" />
              <span className="lt-label">Temp</span>
              <span className="lt-sub">°{units}</span>
            </button>
            <button
              className={layer === "chl" ? "active" : ""}
              onClick={() => setLayer("chl")}
              title="Chlorophyll-a concentration from VIIRS — visibility proxy"
            >
              <ConfidenceDot layer="chl" className="lt-conf" />
              <span className="lt-label">Chl</span>
              <span className="lt-sub">mg/m³</span>
            </button>
            <button
              className={layer === "wind" ? "active" : ""}
              onClick={() => setLayer("wind")}
              title="10 m wind from HRRR + GFS"
            >
              <ConfidenceDot layer="wind" className="lt-conf" />
              <span className="lt-label">Wind</span>
              <span className="lt-sub">kt</span>
            </button>
            <button
              className={layer === "swell" ? "active" : ""}
              onClick={() => setLayer("swell")}
              title="Significant wave height + period + direction from NOAA WaveWatch III"
            >
              <ConfidenceDot layer="swell" className="lt-conf" />
              <span className="lt-label">Swell</span>
              <span className="lt-sub">ft Hs</span>
            </button>
            <button
              className={layer === "current" ? "active" : ""}
              onClick={() => setLayer("current")}
              title="Surface current speed and direction from HFR observations plus tide/wind inference (BETA — model blends sparse HF-radar coverage with bulk wind/tide inference; verify against local knowledge)."
            >
              <ConfidenceDot layer="current" className="lt-conf" />
              <span className="lt-label">Current</span>
              <span className="lt-sub">kt</span>
              <span className="lt-beta">Beta</span>
            </button>
            <button
              className={layer === "viz" ? "active" : ""}
              onClick={() => setLayer("viz")}
              title="Predicted dive visibility (BETA — model unvalidated for NorCal; use as advisory only). Output is feet, not a direct measurement."
            >
              <ConfidenceDot layer="viz" className="lt-conf" />
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
                hasHistory={hasSstHistory}
                hasForecast={hasSstForecast}
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
    </>
  );
}
