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
import {
  getSST,
  getChl,
  getWindSpeed,
  getWindUV,
  getVizFt,
  getSwell5dStats,
  windSource,
  windCompass,
  windCardinal,
} from "../lib/dataSource.js";
import { WindCurrentSelectionCard } from "./WindDayGrid.jsx";
import { SwellCurrentCard } from "./SwellTimeline.jsx";

const LAYERS = [
  { id: "sst",   label: "Temp",  unit: "°{U}" },
  { id: "chl",   label: "Chl",   unit: "mg/m³" },
  { id: "wind",  label: "Wind",  unit: "kt" },
  { id: "swell", label: "Swell", unit: "ft" },
  { id: "viz",   label: "Vis",   unit: "ft" },
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
  windSel, setWindSel,
  swellSel, setSwellSel,
  activeComposite,
  units, dataState,
  mpaOn, setMpaOn,
  bathyOn, setBathyOn,
  activeSpot, setActiveSpot,
  timeOpts,
  compositeText,
  layerIsReal,
  hover,
  setHover,
}) {
  const [open, setOpen] = useState(false);
  // wind + swell use the slot-key string; sst/chl/viz use integer composite.
  const lookupKey = activeComposite ?? composite;
  const focal = focalPoint(hover, activeSpot);
  const focalValue = valueAt(layer, focal.lng, focal.lat, lookupKey, units);

  // Time slice label — short version for the status row.
  const timeLabel =
    layer === "wind" || layer === "swell"
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
                : layer === "swell" ? "Swell · 5-day forecast"
                : timeOpts.label}
              <span className="ms-section-sub">{timeOpts.helper}</span>
            </div>
            {layer === "wind" ? (
              <WindCurrentSelectionCard sel={windSel} setSel={setWindSel} />
            ) : layer === "swell" ? (
              <SwellCurrentCard sel={swellSel} />
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
                className={"mpa-pill" + (mpaOn ? " active" : "")}
                onClick={() => setMpaOn(!mpaOn)}
              >
                MPAs
              </button>
              <button
                className={"mpa-pill" + (bathyOn ? " active" : "")}
                onClick={() => setBathyOn(!bathyOn)}
              >
                Bottom Detail
              </button>
            </div>
          </section>

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
        </div>
      )}

      {/* Always-visible peek strip ------------------------------------- */}
      <div className="ms-peek">
        {/* Status line — layer name on left, value at focal point in the
            middle, time on the right. Tells the user at a glance what
            they're looking at without opening anything. */}
        <div className="ms-status">
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
                <span className="ms-chip-label">{L.label}</span>
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
    : "Visibility";
}

// ---- Saved spots list (used inside the open sheet) ------------------------

function SpotsList({ layer, composite, units, activeSpot, setActiveSpot }) {
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
        } else if (layer === "swell") {
          const w = getSwell5dStats(s.lng, s.lat, composite);
          const ft = Number.isFinite(w.hs) ? w.hs * 3.28084 : NaN;
          valTxt = Number.isFinite(ft) ? ft.toFixed(1) : "—";
          unit = "ft";
          col = "var(--ink-2)";
        } else {
          const v = getVizFt(s.lng, s.lat, composite);
          valTxt = Number.isFinite(v) ? `~${Math.round(v)}` : "—";
          unit = "ft";
          col = "var(--ink-2)";
        }
        return (
          <button
            key={s.id}
            className={"ms-spot" + (activeSpot === s.id ? " active" : "")}
            onClick={() => setActiveSpot(s.id)}
          >
            <span className="ms-spot-pin" style={{ color: col }}></span>
            <span className="ms-spot-name">{s.name}</span>
            <span className="ms-spot-val mono">
              {valTxt}<span className="unit">{unit}</span>
            </span>
          </button>
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
    : "Predicted Visibility";
  const unitLabel =
    layer === "sst" ? `°${units}`
    : layer === "chl" ? "mg/m³"
    : layer === "wind" ? "kt"
    : layer === "swell" ? "ft Hs"
    : "ft";
  return (
    <div className="ms-legend">
      <div className="ms-legend-head">
        <strong>{title}</strong>
        {layer === "viz" && <span className="predicted-badge">PREDICTED</span>}
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
            : "Poor → Excellent"}
        </span>
        <span>
          <strong>{compositeText}</strong>
          {layer === "wind"
            ? ` · ${windSource(composite) || "HRRR"}`
            : layer === "viz"
            ? ` · model output`
            : layer === "swell"
            ? ` · WaveWatch III`
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
            <span className="swatch" style={{ background: "rgb(10,50,140)" }}></span>
            <strong>Deep blue</strong> = gin-clear, low-productivity water.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(60,200,180)" }}></span>
            <strong>Teal</strong> = moderate. Normal coastal viz.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(110,210,90)" }}></span>
            <strong>Green</strong> = productive. Fish food, low viz.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(50,130,40)" }}></span>
            <strong>Dense green</strong> = bloom / red tide.
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
            <span className="swatch" style={{ background: "rgb(234,179,8)" }}></span>
            <strong>Fair</strong> 10–20 ft · washed out.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(132,204,22)" }}></span>
            <strong>Good</strong> 20–30 ft · typical kelp diving.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(6,182,212)" }}></span>
            <strong>Very Good</strong> 30–50 ft · clean blue.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(3,105,161)" }}></span>
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
