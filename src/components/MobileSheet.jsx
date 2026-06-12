// Mobile shell — replaces the desktop floating panels (Layer, Saved Spots,
// Legend, Info) on screens <=760 px.
//
// Layout (bottom-up):
//   1. Peek strip — always visible. Status row (layer + value + time) on
//      top, drag handle bar, then horizontal-scroll layer chips. The
//      active chip mirrors the value at the focused location, so you can
//      read the map at a glance without opening anything.
//   2. Pull-up sheet — tap the handle (or any chip a second time) to
//      slide it up. Single scroll containing time controls, overlays,
//      saved spots, legend, and info. No tabs.
//
// Tap-to-pin on the map (handled in App.jsx) sets `hover` to a focal
// point. The peek strip mirrors that point's value. With no pin, we fall
// back to the active saved spot so the strip is never empty.
//
// Shares state with DesktopView via props — no parallel state.

import { useState } from "react";
import { sstColor, chlColor, SAVED_SPOTS } from "../lib/mapData.js";
import { SstTrendChip } from "./SstTrendBits.jsx";
import {
  getSST,
  getChl,
  getWindSpeed,
  getWindUV,
  getCurrentSpeed,
  getCurrentUV,
  getVizFt,
  getColumnAt,
  getColumnSpot,
  getSwell5dStats,
  getCurrent5dSummary,
  windSource,
  currentSource,
  windCompass,
  windCardinal,
} from "../lib/dataSource.js";
import WaterColumn from "./micro/WaterColumn.jsx";
import WindDayGrid from "./WindDayGrid.jsx";
import { SwellCurrentCard } from "./SwellTimeline.jsx";
import { SstCurrentCard, SstModeToggle } from "./SstTimeline.jsx";
import { CurrentCurrentCard } from "./CurrentTimeline.jsx";
import { usePrefs } from "../contexts/PrefsContext.jsx";
import ConfidenceDot from "./ConfidenceDot.jsx";

const LAYERS = [
  { id: "sst",   label: "Temp",  unit: "°{U}" },
  { id: "chl",   label: "Chl",   unit: "mg/m³" },
  { id: "wind",  label: "Wind",  unit: "kt" },
  { id: "swell", label: "Swell", unit: "ft" },
  // current is beta — HFR coverage is sparse on the CA coast and the
  // inference blend (HFR + tide + wind) underestimates submesoscale
  // features like upwelling jets. The chip renders a "Beta" pill.
  { id: "current", label: "Current", unit: "kt", beta: true },
  // viz is marked beta until NorCal ground truth lands — see
  // pipeline/fetch_visibility.py manifest-write block for the rationale.
  // The chip below renders a "Beta" pill on this entry.
  { id: "viz",   label: "Vis",   unit: "ft", beta: true },
];

// Compact value-at-point readout. Returns "—" when the layer has no data
// for this location, so the chip never goes empty.
function valueAt(layer, lng, lat, composite, units) {
  if (layer === "sst") {
    const v = getSST(lng, lat, composite);
    if (!Number.isFinite(v)) return "—";
    return units === "F"
      ? `${(v * 9 / 5 + 32).toFixed(0)}°F`
      : `${v.toFixed(0)}°C`;
  }
  if (layer === "chl") {
    const v = getChl(lng, lat, composite);
    return Number.isFinite(v) ? `${v.toFixed(2)}` : "—";
  }
  if (layer === "wind") {
    const kt = getWindSpeed(lng, lat, composite);
    if (!Number.isFinite(kt)) return "—";
    const { u, v } = getWindUV(lng, lat, composite);
    const dir =
      Number.isFinite(u) && Number.isFinite(v)
        ? ` ${windCardinal(windCompass(u, v))}`
        : "";
    return `${kt.toFixed(0)} kt${dir}`;
  }
  if (layer === "current") {
    const kt = getCurrentSpeed(lng, lat, composite);
    if (!Number.isFinite(kt)) return "—";
    const { u, v } = getCurrentUV(lng, lat, composite);
    const dir =
      Number.isFinite(u) && Number.isFinite(v)
        ? ` to ${windCardinal((Math.atan2(u, v) * 180 / Math.PI + 360) % 360)}`
        : "";
    return `${kt.toFixed(1)} kt${dir}`;
  }
  if (layer === "swell") {
    const w = getSwell5dStats(lng, lat, composite);
    if (!Number.isFinite(w.hs)) return "—";
    return `${(w.hs * 3.28084).toFixed(1)} ft`;
  }
  if (layer === "viz") {
    const ft = getVizFt(lng, lat, composite);
    return Number.isFinite(ft) ? `~${Math.round(ft)} ft` : "—";
  }
  return "—";
}

function focalPoint(hover, activeSpot) {
  if (hover && Number.isFinite(hover.lng) && Number.isFinite(hover.lat)) {
    return { lng: hover.lng, lat: hover.lat, label: "pinned" };
  }
  const s = SAVED_SPOTS.find((x) => x.id === activeSpot) || SAVED_SPOTS[0];
  return { lng: s.lng, lat: s.lat, label: s.name };
}

export default function MobileShell({
  layer, setLayer,
  composite, setComposite,
  sstMode, setSstMode,
  sstActiveSel, setSstActiveSel,
  activeSstMode,
  hasSstTimeline,
  hasSstHistory,
  hasSstForecast,
  windSel, setWindSel,
  swellSel, setSwellSel,
  currentSel, setCurrentSel,
  activeComposite,
  dataState,
  setMpaOn,
  setBathyOn,
  setKelpOn,
  kelpAvailable,
  bundledSpots,
  openSpotDetail,
  activeSpot, setActiveSpot,
  timeOpts,
  compositeText,
  layerIsReal,
  hover,
  setHover,
}) {
  const [open, setOpen] = useState(false);
  // Stage 5c: units/mpaOn/bathyOn come from context (PrefsProvider in
  // src/main.jsx). setMpaOn/setBathyOn stay as props because MapShell
  // wraps them with a popup-clearing side effect before passing them in
  // (see updateMpaOn/updateBathyOn there).
  const { prefs } = usePrefs();
  const { units, mpaOn, bathyOn, kelpOn } = prefs;
  // wind + swell + current use slot keys; sst uses history/forecast slots
  // when loaded; chl/viz use integer composites.
  //
  // Stage 5b (2026-05-23): activeSstMode + sstActiveSel + hasSstTimeline
  // now arrive pre-resolved from the useTimelineSelections hook (via
  // MapShell). MobileSheet used to recompute them locally with a
  // slightly-different inline conditional than MapShell did — both
  // call sites collapse to the hook's single resolveSstMode call now.
  const hasCurrentSummary = Boolean(getCurrent5dSummary());
  const lookupKey = activeComposite ?? composite;
  const focal = focalPoint(hover, activeSpot);
  const focalValue = valueAt(layer, focal.lng, focal.lat, lookupKey, units);

  // Time slice label — short version for the status row.
  const timeLabel =
    (layer === "sst" && hasSstTimeline) || layer === "wind" || layer === "swell" || layer === "current"
      ? compositeText
      : `${composite}-day · ${compositeText}`;

  return (
    <div className={"mobile-shell" + (open ? " open" : "")}>
      {/* Pull-up sheet — only mounted when open so off-screen content
          isn't sitting in the DOM eating layout. */}
      {open && (
        <div className="ms-sheet" role="dialog" aria-label="Conditions panel">
          <button
            className="ms-close"
            onClick={() => setOpen(false)}
            aria-label="Close panel"
          >
            ×
          </button>

          {/* TIME / FORECAST ----------------------------------------------- */}
          <section className="ms-section">
            <div className="ms-section-h">
              {layer === "wind" ? "Wind · 5-day forecast"
                : layer === "sst" && hasSstTimeline ? `Sea temp · ${activeSstMode === "forecast" ? "beta forecast" : "historical trend"}`
                : layer === "swell" ? "Swell · 5-day forecast"
                : layer === "current" ? "Surface current · 5-day"
                : timeOpts.label}
              <span className="ms-section-sub">
                {layer === "sst" && hasSstTimeline
                  ? activeSstMode === "forecast" ? "trend persistence" : "recent MUR days"
                  : timeOpts.helper}
              </span>
            </div>
            {layer === "sst" && hasSstTimeline ? (
              <>
                <SstModeToggle
                  mode={activeSstMode}
                  setMode={setSstMode}
                  hasHistory={hasSstHistory}
                  hasForecast={hasSstForecast}
                />
                <SstCurrentCard sel={sstActiveSel} units={units} mode={activeSstMode} />
              </>
            ) : layer === "wind" ? (
              <WindDayGrid sel={windSel} setSel={setWindSel} layout="stack" />
            ) : layer === "swell" ? (
              <SwellCurrentCard sel={swellSel} />
            ) : layer === "current" && hasCurrentSummary ? (
              <CurrentCurrentCard sel={currentSel} />
            ) : (
              <>
                <div
                  className="ms-time-row"
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
                        <span className="ms-time-label">{label}</span>
                        <span className="ms-time-tag">{timeOpts.tags[i]}</span>
                      </button>
                    );
                  })}
                </div>
                <div className="ms-window">
                  <span>Window</span>
                  <span className="mono">{compositeText}</span>
                </div>
              </>
            )}
          </section>

          {/* OVERLAYS ----------------------------------------------------- */}
          <section className="ms-section">
            <div className="ms-section-h">Overlays</div>
            <div className="ms-overlay-row">
              <button
                type="button"
                className={"mpa-pill" + (mpaOn ? " active" : "")}
                onClick={(e) => {
                  e.stopPropagation();
                  setMpaOn(!mpaOn);
                }}
                aria-pressed={mpaOn}
              >
                MPAs
              </button>
              <button
                type="button"
                className={"mpa-pill" + (bathyOn ? " active" : "")}
                onClick={(e) => {
                  e.stopPropagation();
                  setBathyOn(!bathyOn);
                }}
                aria-pressed={bathyOn}
              >
                Bottom Detail
              </button>
              {kelpAvailable && (
                <button
                  type="button"
                  className={"mpa-pill" + (kelpOn ? " active" : "")}
                  onClick={(e) => {
                    e.stopPropagation();
                    setKelpOn(!kelpOn);
                  }}
                  aria-pressed={kelpOn}
                >
                  Kelp Beds
                </button>
              )}
            </div>
          </section>

          {/* WATER COLUMN (PRD water-column V4 — mobile dock) -------------- */}
          {layer === "viz" && prefs.waterColumnOn && (() => {
            // Pinned tap wins; otherwise the selected saved spot
            // (whose sidecar adds the 24 h cliff series).
            let col = null, title = null, series = null;
            if (hover && Number.isFinite(hover.lng)) {
              col = getColumnAt(hover.lng, hover.lat);
              if (col) title = `${hover.lat.toFixed(3)}°N ${Math.abs(hover.lng).toFixed(3)}°W`;
            }
            if (!col && activeSpot) {
              const sc = getColumnSpot(activeSpot);
              if (sc) { col = sc; title = sc.name; series = sc.cliff_series_ft; }
            }
            return (
              <section className="ms-section">
                <div className="ms-section-h">Water column</div>
                <WaterColumn col={col} title={title} series={series} compact />
              </section>
            );
          })()}

          {/* PIN / SPOTS -------------------------------------------------- */}
          <section className="ms-section">
            <div className="ms-section-h">
              Saved spots
              {hover && (
                <button
                  className="ms-pin-clear"
                  onClick={() => setHover(null)}
                  aria-label="Clear pin"
                >
                  Clear pin
                </button>
              )}
            </div>
            <SpotsList
              layer={layer}
              composite={lookupKey}
              units={units}
              activeSpot={activeSpot}
              setActiveSpot={setActiveSpot}
              bundledSpots={bundledSpots}
              openSpotDetail={openSpotDetail}
            />
          </section>

          {/* LEGEND ------------------------------------------------------- */}
          <section className="ms-section">
            <div className="ms-section-h">Legend</div>
            <Legend
              layer={layer}
              units={units}
              composite={lookupKey}
              compositeText={compositeText}
              layerIsReal={layerIsReal}
              dataReady={dataState?.ready}
            />
          </section>

          {/* INFO -------------------------------------------------------- */}
          <section className="ms-section">
            <div className="ms-section-h">How to read this</div>
            <Info layer={layer} />
          </section>
          {/* Tip-jar moved 2026-05-08: now lives in the topbar (the
              tiny fish "click for WSB" link), visible on every layer
              from the start. The mobile peek strip already hides the
              topbar at narrow widths, so the topbar tip falls back
              into view in the open sheet via the .topbar inside the
              fixed shell — same affordance, less duplication. */}
        </div>
      )}

      <div className="ms-overlay-quick" aria-label="Map overlays">
        <button
          type="button"
          className={"mpa-pill" + (mpaOn ? " active" : "")}
          onClick={(e) => {
            e.stopPropagation();
            setMpaOn(!mpaOn);
          }}
          aria-pressed={mpaOn}
        >
          MPAs
        </button>
        <button
          type="button"
          className={"mpa-pill" + (bathyOn ? " active" : "")}
          onClick={(e) => {
            e.stopPropagation();
            setBathyOn(!bathyOn);
          }}
          aria-pressed={bathyOn}
        >
          Bottom
        </button>
        {kelpAvailable && (
          <button
            type="button"
            className={"mpa-pill" + (kelpOn ? " active" : "")}
            onClick={(e) => {
              e.stopPropagation();
              setKelpOn(!kelpOn);
            }}
            aria-pressed={kelpOn}
          >
            Kelp
          </button>
        )}
      </div>

      {/* Always-visible peek strip ------------------------------------- */}
      <div className="ms-peek">
        {/* Status line — layer name on left, value at focal point in the
            middle, time on the right. Tells the user at a glance what
            they're looking at without opening anything. Also doubles
            as a tap target to open the sheet (the .ms-handle bar below
            is just a visual affordance — most users tap the visible
            info row, not a 14px stripe). */}
        <div
          className="ms-status"
          role="button"
          tabIndex={0}
          aria-label={open ? "Close conditions panel" : "Open conditions panel"}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setOpen((v) => !v);
            }
          }}
        >
          <span className="ms-status-layer">
            <span className="dot" />
            <strong>{layerNameFor(layer)}</strong>
          </span>
          <span className="ms-status-val mono">
            {focalValue}
            <span className="ms-status-where">at {focal.label}</span>
          </span>
          <span className="ms-status-time mono">{timeLabel}</span>
        </div>

        {/* Drag handle — tap (or drag-up later) to expand the sheet. */}
        <button
          className="ms-handle"
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? "Close panel" : "Open panel"}
          aria-expanded={open}
        >
          <span className="ms-handle-bar" />
        </button>

        {/* Layer chip row — always one tap to switch layer. The active
            chip's "sub" line shows the current value-at-focal-point so
            you don't have to open the sheet to read the map. */}
        <div className="ms-chips" role="tablist">
          {LAYERS.map((L) => {
            const active = layer === L.id;
            const sub = active
              ? focalValue
              : L.unit.replace("{U}", units);
            return (
              <button
                key={L.id}
                className={"ms-chip" + (active ? " active" : "")}
                onClick={() => setLayer(L.id)}
                role="tab"
                aria-selected={active}
              >
                <ConfidenceDot layer={L.id} className="ms-chip-conf" />
                <span className="ms-chip-label">
                  {L.label}
                  {L.beta && <span className="ms-chip-beta">Beta</span>}
                </span>
                <span className="ms-chip-sub mono">{sub}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function layerNameFor(layer) {
  return layer === "sst" ? "Sea Temp"
    : layer === "chl" ? "Chlorophyll"
    : layer === "wind" ? "Wind"
    : layer === "swell" ? "Swell"
    : layer === "current" ? "Current"
    : "Visibility";
}

// ---- Saved spots list (used inside the open sheet) ------------------------

function SpotsList({ layer, composite, units, activeSpot, setActiveSpot, bundledSpots, openSpotDetail }) {
  return (
    <div className="ms-spots">
      {SAVED_SPOTS.map((s) => {
        let valTxt, unit, col;
        if (layer === "sst") {
          const v = getSST(s.lng, s.lat, composite);
          if (Number.isFinite(v)) {
            valTxt = units === "F"
              ? `${(v * 9 / 5 + 32).toFixed(1)}`
              : `${v.toFixed(1)}`;
            col = sstColor(v);
          } else {
            valTxt = "—"; col = "var(--ink-3)";
          }
          unit = `°${units}`;
        } else if (layer === "chl") {
          const v = getChl(s.lng, s.lat, composite);
          if (Number.isFinite(v)) {
            valTxt = v.toFixed(2);
            col = chlColor(v);
          } else {
            valTxt = "—"; col = "var(--ink-3)";
          }
          unit = "mg/m³";
        } else if (layer === "wind") {
          const v = getWindSpeed(s.lng, s.lat, composite);
          valTxt = Number.isFinite(v) ? v.toFixed(1) : "—";
          unit = "kt";
          col = "var(--ink-2)";
        } else if (layer === "current") {
          const v = getCurrentSpeed(s.lng, s.lat, composite);
          valTxt = Number.isFinite(v) ? v.toFixed(1) : "—";
          unit = "kt";
          col = "var(--ink-2)";
        } else if (layer === "swell") {
          const w = getSwell5dStats(s.lng, s.lat, composite);
          const ft = Number.isFinite(w.hs) ? w.hs * 3.28084 : NaN;
          valTxt = Number.isFinite(ft) ? ft.toFixed(1) : "—";
          unit = "ft";
          col = "var(--ink-2)";
        } else {
          const v = getVizFt(s.lng, s.lat, composite);
          // Two-number viz row (PRD water-column V4): surface→below
          // when the spot's column sidecar is loaded and has a cliff.
          const sc = getColumnSpot(s.id);
          if (Number.isFinite(v) && sc && !sc.no_cliff && sc.below_ft != null) {
            valTxt = `~${Math.round(v)}→${Math.round(sc.below_ft)}`;
          } else {
            valTxt = Number.isFinite(v) ? `~${Math.round(v)}` : "—";
          }
          unit = "ft";
          col = "var(--ink-2)";
        }
        const isBundled = bundledSpots?.has(s.id);
        const isActive = activeSpot === s.id;
        return (
          <div key={s.id} className="ms-spot-row">
            <button
              className={"ms-spot" + (isActive ? " active" : "")}
              onClick={() => setActiveSpot(s.id)}
            >
              <span className="ms-spot-pin" style={{ color: col }}></span>
              <span className="ms-spot-name">{s.name}</span>
              {/* SST mobile rows get the trend chip inline so direction
                  reads at a glance without crowding. Sparkline omitted on
                  mobile — too small to be readable in this row height. */}
              {layer === "sst" && (
                <SstTrendChip lng={s.lng} lat={s.lat} units={units} />
              )}
              <span className="ms-spot-val mono">
                {valTxt}<span className="unit">{unit}</span>
              </span>
            </button>
            {/* Spot Detail launch — mobile mirror of the desktop
                button. Shows only for the active row to keep the
                list compact on small screens. */}
            {isBundled && isActive && (
              <button
                type="button"
                className="spot-detail-open"
                onClick={(e) => {
                  e.stopPropagation();
                  openSpotDetail?.(s.id);
                }}
              >
                View detailed map →
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---- Legend (used inside the open sheet) ---------------------------------

function Legend({ layer, units, composite, compositeText, layerIsReal, dataReady }) {
  const title =
    layer === "sst" ? "Sea Surface Temperature"
    : layer === "chl" ? "Water Clarity (Chlorophyll-a)"
    : layer === "wind" ? "Wind Speed (10 m)"
    : layer === "swell" ? "Swell · Hs"
    : layer === "current" ? "Surface Current"
    : "Predicted Visibility · surface";
  const unitLabel =
    layer === "sst" ? `°${units}`
    : layer === "chl" ? "mg/m³"
    : layer === "wind" ? "kt"
    : layer === "swell" ? "ft Hs"
    : layer === "current" ? "kt"
    : "ft";
  return (
    <div className="ms-legend">
      <div className="ms-legend-head">
        <strong>{title}</strong>
        {layer === "viz" && <span className="predicted-badge">PREDICTED</span>}
        {layer === "current" && <span className="beta-badge">BETA</span>}
        <span className="mono" style={{ color: "var(--ink-3)", marginLeft: "auto" }}>
          {unitLabel}
        </span>
      </div>
      <div className={`legend-bar ${layer}`}></div>
      <div className="legend-ticks">
        {layer === "sst" ? (
          units === "F" ? (
            <><span>48</span><span>55</span><span>61</span><span>66</span><span>72</span><span>77</span></>
          ) : (
            <><span>9</span><span>13</span><span>16</span><span>19</span><span>22</span><span>25</span></>
          )
        ) : layer === "chl" ? (
          <><span>0.05</span><span>0.3</span><span>1.0</span><span>3.5</span><span>20+</span></>
        ) : layer === "wind" ? (
          <><span>0</span><span>5</span><span>10</span><span>15</span><span>20</span><span>25</span><span>35+</span></>
        ) : layer === "swell" ? (
          <><span>0</span><span>1</span><span>3</span><span>5</span><span>8</span><span>12</span><span>20+</span></>
        ) : layer === "current" ? (
          <><span>0</span><span>0.4</span><span>0.8</span><span>1.2</span><span>1.8</span><span>2.5</span><span>3+</span></>
        ) : (
          <><span>0</span><span>10</span><span>20</span><span>30</span><span>50+</span></>
        )}
      </div>
      <div className="legend-meta" style={{ marginTop: 10 }}>
        <span>
          {layer === "sst" ? "Cold upwelling → Heatwave"
            : layer === "chl" ? "Gin-clear → Bloom"
            : layer === "wind" ? "Calm → Gale"
            : layer === "swell" ? "Glassy → Storm seas"
            : layer === "current" ? "Weak → ripping"
            : "Poor → Excellent"}
        </span>
        <span>
          <strong>{compositeText}</strong>
          {layer === "wind"
            ? ` · ${windSource(composite) || "HRRR"}`
            : layer === "current"
            ? ` · ${currentSource(composite) || "surface estimate"}`
            : layer === "viz"
            ? ` · model output`
            : layer === "swell"
            ? ` · WaveWatch III`
            : layer === "sst"
            ? ` · MUR trend`
            : ` · composite`}
          {!layerIsReal && dataReady && " · no data"}
        </span>
      </div>
    </div>
  );
}

// ---- Info copy (used inside the open sheet) ------------------------------

function Info({ layer }) {
  return (
    <div className="ms-info">
      {layer === "sst" && (
        <>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(40,130,210)" }}></span>
            <strong>Blue</strong> = cold (54–57°F upwelling).
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(120,220,220)" }}></span>
            <strong>Cyan</strong> = transition. Spring suit.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(240,220,110)" }}></span>
            <strong>Yellow</strong> = warm SoCal summer (66–70°F).
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(170,20,35)" }}></span>
            <strong>Red</strong> = anomaly. Watch for HABs.
          </p>
        </>
      )}
      {layer === "chl" && (
        <>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(31,58,85)" }}></span>
            <strong>Deep navy</strong> = gin-clear, low-productivity water.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(91,141,181)" }}></span>
            <strong>Mid blue</strong> = typical clear nearshore viz.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(127,160,90)" }}></span>
            <strong>Olive green</strong> = upwelling. Fish food, low viz.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(122,90,60)" }}></span>
            <strong>Warm brown</strong> = peak upwelling or mild bloom.
          </p>
        </>
      )}
      {layer === "wind" && (
        <>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(170,210,240)" }}></span>
            <strong>≤5 kt</strong> — glassy.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(120,200,160)" }}></span>
            <strong>~10 kt</strong> — light breeze.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(220,220,100)" }}></span>
            <strong>~15 kt</strong> — moderate chop.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(240,160,70)" }}></span>
            <strong>~20 kt</strong> — small craft advisory.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(140,30,90)" }}></span>
            <strong>35+ kt</strong> — gale.
          </p>
        </>
      )}
      {layer === "swell" && (
        <>
          <p className="info-p">
            Three numbers per cell: significant height (Hs), peak period
            (Tp), and primary direction (Dp). Color shows Hs.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(103,232,249)" }}></span>
            <strong>1–3 ft</strong> — calm nearshore.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(132,204,22)" }}></span>
            <strong>3–5 ft</strong> — workable.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(234,179,8)" }}></span>
            <strong>5–8 ft</strong> — sketchy nearshore.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(220,38,38)" }}></span>
            <strong>12–20 ft</strong> — don't dive.
          </p>
          <p className="info-p" style={{ fontSize: 11, color: "var(--ink-3)" }}>
            Tp ≥ 12 s = clean groundswell · Tp &lt; 8 s = windswell.
          </p>
        </>
      )}
      {layer === "current" && (
        <>
          <p className="info-p">
            <strong>Beta estimate.</strong> Use as planning context only, and verify with
            local conditions before committing to a dive.
          </p>
          <p className="info-p">
            Surface-current speed and set direction. The near-term field uses
            HFR observations where available, then decays into tide/wind inference.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(125,211,252)" }}></span>
            <strong>Blue</strong> = weak current.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(94,234,212)" }}></span>
            <strong>Teal</strong> = noticeable set.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(250,204,21)" }}></span>
            <strong>Yellow</strong> = strong enough to shape the dive plan.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(220,38,38)" }}></span>
            <strong>Red/purple</strong> = high-risk surface set.
          </p>
          <p className="info-p" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
            Surface current only; reef-depth current can differ around structure and kelp.
          </p>
        </>
      )}
      {layer === "viz" && (
        <>
          <p className="info-p">
            <strong>This is a prediction, not a measurement.</strong> A
            zone-aware model blends today's chl-a, SST, wind, swell,
            precipitation, river runoff, tide, and seasonal climatology
            into a Secchi-equivalent depth in feet.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(194,65,12)" }}></span>
            <strong>Poor</strong> 0–10 ft · silty / blown out.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(34,197,94)" }}></span>
            <strong>Fair</strong> 10–20 ft · diveable but washed out.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(6,182,212)" }}></span>
            <strong>Good</strong> 20–30 ft · typical kelp diving.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(3,105,161)" }}></span>
            <strong>Very Good</strong> 30–50 ft · clean blue.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(31,77,117)" }}></span>
            <strong>Excellent</strong> 50+ ft · once-a-year clarity.
          </p>
          <p className="info-p" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
            Treat outputs as ±20%.
          </p>
        </>
      )}
      <p className="info-p" style={{ marginTop: 8, color: "var(--ink-3)", fontSize: 11 }}>
        Diagonal stripes mean the satellite or model didn't capture this
        cell — we show it as missing rather than fake the value.
      </p>
    </div>
  );
}
