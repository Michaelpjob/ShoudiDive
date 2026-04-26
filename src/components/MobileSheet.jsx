// Mobile-only bottom sheet — replaces the four floating panels (Layer,
// Saved Spots, Legend, Info) on screens <=760 px. Tab bar at the bottom,
// active tab expands its content as a sheet above the tabs. Map fills
// the rest of the viewport.
//
// Shares state with DesktopView via props — no parallel state.

import { useState } from "react";
import { sstColor, chlColor, SAVED_SPOTS } from "../lib/mapData.js";
import {
  getSST,
  getChl,
  getWindSpeed,
  getVizFt,
  windSource,
} from "../lib/dataSource.js";
import { styleForClass } from "./BathyLayer.jsx";

const TABS = [
  { id: "layers", label: "Layers" },
  { id: "spots",  label: "Spots"  },
  { id: "legend", label: "Legend" },
  { id: "info",   label: "Read"   },
];

export default function MobileSheet({
  layer, setLayer,
  composite, setComposite,
  units, dataState,
  mpaOn, setMpaOn,
  bathyOn, setBathyOn,
  activeSpot, setActiveSpot,
  timeOpts,
  compositeText,
  layerIsReal,
}) {
  const [tab, setTab] = useState(null);
  const open = tab !== null;

  return (
    <>
      {/* Sheet content — only when a tab is active */}
      {open && (
        <div className="mobile-sheet-body">
          {tab === "layers" && (
            <LayerSection
              layer={layer} setLayer={setLayer}
              composite={composite} setComposite={setComposite}
              units={units}
              mpaOn={mpaOn} setMpaOn={setMpaOn}
              bathyOn={bathyOn} setBathyOn={setBathyOn}
              timeOpts={timeOpts}
              compositeText={compositeText}
            />
          )}
          {tab === "spots" && (
            <SpotsSection
              layer={layer} composite={composite} units={units}
              activeSpot={activeSpot} setActiveSpot={setActiveSpot}
            />
          )}
          {tab === "legend" && (
            <LegendSection
              layer={layer} units={units} composite={composite}
              compositeText={compositeText}
              layerIsReal={layerIsReal}
              dataReady={dataState?.ready}
            />
          )}
          {tab === "info" && <InfoSection layer={layer} />}
        </div>
      )}

      {/* Tab bar — always visible */}
      <div className="mobile-sheet-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={"mobile-tab" + (tab === t.id ? " active" : "")}
            onClick={() => setTab(tab === t.id ? null : t.id)}
            aria-pressed={tab === t.id}
          >
            {t.label}
          </button>
        ))}
      </div>
    </>
  );
}

// ---- Layer section ---------------------------------------------------------

function LayerSection({
  layer, setLayer,
  composite, setComposite,
  units,
  mpaOn, setMpaOn,
  bathyOn, setBathyOn,
  timeOpts,
  compositeText,
}) {
  return (
    <div className="ms-layers">
      <div className="ms-row-label">Layer</div>
      <div className="ms-layer-toggle ms-layer-toggle-4">
        <button className={layer === "sst" ? "active" : ""} onClick={() => setLayer("sst")}>
          <span className="ms-lt-label">Sea Temp</span>
          <span className="ms-lt-sub">°{units}</span>
        </button>
        <button className={layer === "chl" ? "active" : ""} onClick={() => setLayer("chl")}>
          <span className="ms-lt-label">Visibility</span>
          <span className="ms-lt-sub">mg/m³</span>
        </button>
        <button className={layer === "wind" ? "active" : ""} onClick={() => setLayer("wind")}>
          <span className="ms-lt-label">Wind</span>
          <span className="ms-lt-sub">kt</span>
        </button>
        <button className={layer === "viz" ? "active" : ""} onClick={() => setLayer("viz")}>
          <span className="ms-lt-label">Forecast</span>
          <span className="ms-lt-sub">predicted</span>
        </button>
      </div>

      <div className="ms-row-label">{timeOpts.label}</div>
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
        <span>{layer === "wind" ? "Valid" : "Window"}</span>
        <span className="mono">{compositeText}</span>
      </div>

      <div className="ms-row-label">Overlays</div>
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
    </div>
  );
}

// ---- Spots section ---------------------------------------------------------

function SpotsSection({ layer, composite, units, activeSpot, setActiveSpot }) {
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

// ---- Legend section --------------------------------------------------------

function LegendSection({ layer, units, composite, compositeText, layerIsReal, dataReady }) {
  const title =
    layer === "sst" ? "Sea Surface Temperature"
    : layer === "chl" ? "Water Clarity (Chlorophyll-a)"
    : layer === "wind" ? "Wind Speed (10 m)"
    : "Predicted Visibility";
  const unitLabel =
    layer === "sst" ? `°${units}`
    : layer === "chl" ? "mg/m³"
    : layer === "wind" ? "kt"
    : "ft";
  return (
    <div className="ms-legend">
      <div className="ms-legend-head">
        <strong>{title}</strong>
        {layer === "viz" && <span className="predicted-badge">PREDICTED</span>}
        <span className="mono" style={{ color: "var(--ink-3)", marginLeft: "auto" }}>{unitLabel}</span>
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
        ) : (
          <><span>0</span><span>10</span><span>20</span><span>30</span><span>50+</span></>
        )}
      </div>
      <div className="legend-meta" style={{ marginTop: 10 }}>
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
          {!layerIsReal && dataReady && " · no data"}
        </span>
      </div>
    </div>
  );
}

// ---- Info section ----------------------------------------------------------

function InfoSection({ layer }) {
  return (
    <div className="ms-info">
      {layer === "sst" && (
        <>
          <div className="info-section">
            <h4 className="info-h">Sea Surface Temperature</h4>
            <p className="info-p">
              <span className="swatch" style={{ background: "rgb(40,130,210)" }}></span>
              <strong>Blue</strong> = cold (54–57°F Central Coast / Pt. Conception upwelling).
            </p>
            <p className="info-p">
              <span className="swatch" style={{ background: "rgb(120,220,220)" }}></span>
              <strong>Cyan</strong> = transition. Spring-suit comfortable.
            </p>
            <p className="info-p">
              <span className="swatch" style={{ background: "rgb(240,220,110)" }}></span>
              <strong>Yellow</strong> = warm SoCal summer (66–70°F).
            </p>
            <p className="info-p">
              <span className="swatch" style={{ background: "rgb(170,20,35)" }}></span>
              <strong>Red</strong> = anomaly. Watch for kelp stress / HABs.
            </p>
          </div>
        </>
      )}
      {layer === "chl" && (
        <div className="info-section">
          <h4 className="info-h">Water Clarity (Chlorophyll-a)</h4>
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
            <strong>Dense green</strong> = bloom / red tide. Avoid if water smells off.
          </p>
        </div>
      )}
      {layer === "wind" && (
        <div className="info-section">
          <h4 className="info-h">Wind Speed (10 m)</h4>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(170,210,240)" }}></span>
            <strong>≤5 kt</strong> — glassy, paddleable, divable.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(120,200,160)" }}></span>
            <strong>~10 kt</strong> — light breeze, surface texture.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(220,220,100)" }}></span>
            <strong>~15 kt</strong> — moderate chop.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(240,160,70)" }}></span>
            <strong>~20 kt</strong> — small craft advisory territory.
          </p>
          <p className="info-p">
            <span className="swatch" style={{ background: "rgb(140,30,90)" }}></span>
            <strong>35+ kt</strong> — gale. Stay home.
          </p>
        </div>
      )}
      {layer === "viz" && (
        <div className="info-section">
          <h4 className="info-h">Predicted Visibility · model output</h4>
          <p className="info-p">
            <strong>This is a prediction, not a measurement.</strong> A zone-aware
            model blends today's chl-a, SST, and wind with persistence and a
            climatology baseline. Use the bar below to read the predicted
            Secchi-equivalent visibility in feet.
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
          <p className="info-p" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
            Driven by chl, SST, 5-day wind, 3-day max swell, precipitation,
            USGS river discharge, NOAA tide range, and a monthly climatology.
            Treat outputs as ±20%.
          </p>
        </div>
      )}
      <div className="info-section">
        <h4 className="info-h">Hatched cells</h4>
        <p className="info-p">
          Diagonal stripes mean the satellite or model didn't capture this cell.
          We show it as missing rather than fake the value.
        </p>
      </div>
    </div>
  );
}
