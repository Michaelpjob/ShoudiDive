// Navy Closures popup — shows a clicked SCI zone's status for the selected
// day: GPS coords, the day's closure window(s) + altitude, and an honest
// "schedule as-of / covers" line. Reuses the .mpa-popup* shell.

import { useEffect } from "react";
import { styleForStatus } from "./ClosuresLayer.jsx";

const STATUS_LABEL = {
  open: "OPEN",
  scheduled: "SCHEDULED OPS",
  restricted: "RESTRICTED",
  unknown: "NO DATA",
};
const KIND_LABEL = {
  safety_zone: "Nearshore safety zone (3 nm)",
  operations_area: "Offshore operations area",
};

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" });
}
function fmtCoord(centroid) {
  if (!Array.isArray(centroid) || centroid.length < 2) return null;
  const [lon, lat] = centroid;
  return `${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(4)}° ${lon >= 0 ? "E" : "W"}`;
}

export default function ClosuresPopup({ data, onClose }) {
  const { zone, date, meta } = data;
  const day = zone.statusByDate?.[date] || { status: "unknown", windows: [] };
  const style = styleForStatus(day.status);
  const coord = fmtCoord(zone.centroid);
  const windows = day.windows || [];

  useEffect(() => {
    const onKeyDown = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="mpa-popup-overlay" onClick={onClose} role="presentation">
      <div
        className="mpa-popup"
        role="dialog"
        aria-modal="true"
        aria-label={`${zone.label} closure details`}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="mpa-popup-close"
          onClick={onClose}
          aria-label="Close closure details"
        >
          ×
        </button>
        <div className="mpa-popup-head">
          <div>
            <div className="mpa-popup-name">{zone.label}</div>
            <div className="mpa-popup-fullname">{KIND_LABEL[zone.kind] || "SCI closure area"}</div>
          </div>
          <span
            className="mpa-pill"
            style={{ background: style.fill, borderColor: style.stroke, color: style.stroke }}
          >
            {STATUS_LABEL[day.status] || "—"}
          </span>
        </div>

        <div className="closures-popup-date">{fmtDate(date)}</div>

        {windows.length > 0 ? (
          <ul className="closures-windows">
            {windows.map((w, i) => (
              <li key={i} className="closures-window">
                <span className="cw-time">{w.start_local} → {w.end_local}</span>
                {w.altitude && <span className="cw-alt">{w.altitude}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mpa-popup-body">
            {day.status === "restricted"
              ? "Standing restriction — closed to the public."
              : day.status === "open"
              ? "No scheduled operations for this day."
              : "No published schedule for this day."}
          </p>
        )}

        {coord && (
          <p className="mpa-popup-meta mono">📍 {coord} (center)</p>
        )}

        <p className="closures-asof">
          Schedule from scisland.org
          {meta?.source_updated ? ` · updated ${meta.source_updated}` : ""}
          {meta?.published_window?.start && meta?.published_window?.end
            ? ` · covers ${meta.published_window.start} → ${meta.published_window.end}`
            : ""}
        </p>
        <a
          className="mpa-popup-link"
          href="https://www.scisland.org/"
          target="_blank"
          rel="noreferrer"
        >
          ↗ Official SCI schedule (scisland.org)
        </a>
        <p className="mpa-popup-disclaimer">
          Closures change on short notice. Always confirm on VHF 16 + the official
          schedule before transiting or diving near San Clemente Island.
        </p>
        <button type="button" className="mpa-popup-done" onClick={onClose}>
          Back to map
        </button>
      </div>
    </div>
  );
}
