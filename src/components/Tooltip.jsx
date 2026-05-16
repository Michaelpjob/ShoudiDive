// Hover tooltip — shows the layer's value at the cursor position with
// a layer-specific human-readable subtitle. Carved out of App.jsx
// (2026-05-09) as part of the Tier-1 architecture split.

import {
  getSST,
  getChl,
  getWindSpeed,
  getWindUV,
  getCurrentSpeed,
  getCurrentUV,
  getVizFt,
  getSwell5dStats,
  windCompass,
  windCardinal,
} from "../lib/dataSource.js";

export default function Tooltip({ x, y, layer, composite, lng, lat, units }) {
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
