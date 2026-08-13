// Popup for a clicked temperature break: rough start/end coordinates and
// a copy-paste block ready for a GPS or chartplotter. Reuses the
// .mpa-popup shell styles so it inherits both themes for free.

import { useEffect, useState } from "react";
import { BBOX } from "../lib/mapData.js";
import { BREAK_STRONG_C_PER_KM } from "../lib/sstBreaks.js";
import {
  buildGpsText,
  gridToLngLat,
  formatWaypoint,
} from "../lib/breaksGps.js";

export default function BreaksPopup({ front, grid, dataDate, onClose }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // BreaksLayer hands the grid as {gw, gh}; the format lib wants
  // {width, height}. Normalize once so both callees agree.
  const g = { width: grid.gw ?? grid.width, height: grid.gh ?? grid.height };
  const gpsText = buildGpsText(front, g, BBOX, dataDate);
  const ends = [front.points[0], front.points[front.points.length - 1]].map(
    (p) => formatWaypoint(gridToLngLat(p[0], p[1], g.width, g.height, BBOX))
  );

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(gpsText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API can be denied — the text is selectable below, so
      // manual copy still works; just don't claim success.
      setCopied(false);
    }
  };

  return (
    <div className="mpa-popup-overlay" onClick={onClose} role="presentation">
      <div
        className="mpa-popup"
        role="dialog"
        aria-modal="true"
        aria-label="Temperature break details"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="mpa-popup-close"
          onClick={onClose}
          aria-label="Close temperature break details"
        >
          ×
        </button>
        <div className="mpa-popup-head">
          <div>
            <div className="mpa-popup-name">
              {front.maxGradient >= BREAK_STRONG_C_PER_KM
                ? "Temperature break — strong"
                : "Temperature break — moderate"}
            </div>
            <div className="mpa-popup-fullname">
              ~{front.spanKm} km front
              {dataDate ? ` · satellite data ${dataDate}` : ""}
            </div>
          </div>
        </div>
        <p className="mpa-popup-body">
          Runs from <strong>{ends[0].ddm}</strong> to{" "}
          <strong>{ends[1].ddm}</strong>. Waypoints below are ready for a
          GPS or chartplotter — degrees decimal minutes, with decimal
          degrees in parentheses.
        </p>
        <pre className="break-gps-block mono">{gpsText}</pre>
        <button type="button" className="mpa-popup-done" onClick={copy}>
          {copied ? "✓ Copied" : "Copy waypoints"}
        </button>
        <p className="mpa-popup-disclaimer">
          Traced from gap-filled satellite analysis (MUR). Fronts move with
          wind and tide — expect it near, not exactly on, these positions.
        </p>
        <button type="button" className="mpa-popup-done" onClick={onClose}>
          Back to map
        </button>
      </div>
    </div>
  );
}
