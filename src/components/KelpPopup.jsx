// Kelp-bed popup describing a single CDFW Administrative Kelp Bed.
// Cloned from MpaPopup.jsx as part of the Kelp Bed Zones MVP
// (Phase 1, Task C in outputs/KELP-M~1.MD).
//
// IMPORTANT: These are management/reference boundaries for commercial
// kelp harvest — NOT observed canopy. The disclaimer at the bottom
// makes that explicit so divers don't mistake the polygons for "kelp
// is currently here."

import { useEffect } from "react";
import { styleForStatus } from "./KelpLayer.jsx";

function labelForStatus(status) {
  if (!status) return "Unknown";
  const s = String(status).toLowerCase();
  if (s === "open") return "Open";
  if (s === "leasable") return "Leasable";
  if (s === "leased") return "Leased";
  if (s === "closed") return "Closed";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export default function KelpPopup({ kelp, onClose }) {
  const style = styleForStatus(kelp.status);
  const officialUrl =
    "https://wildlife.ca.gov/Conservation/Marine/Kelp";
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);
  return (
    <div className="mpa-popup-overlay" onClick={onClose} role="presentation">
      <div
        className="mpa-popup"
        role="dialog"
        aria-modal="true"
        aria-label={`${kelp.name} kelp bed details`}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="mpa-popup-close"
          onClick={onClose}
          aria-label="Close kelp bed details"
        >
          ×
        </button>
        <div className="mpa-popup-head">
          <div>
            <div className="mpa-popup-name">{kelp.name}</div>
            <div className="mpa-popup-fullname">
              CDFW Administrative Kelp Bed
              {kelp.bedNumber != null ? ` · #${kelp.bedNumber}` : ""}
            </div>
          </div>
          {kelp.status && (
            <span
              className="mpa-pill"
              style={{ background: style.fill, borderColor: style.stroke, color: style.stroke }}
            >
              {labelForStatus(kelp.status)}
            </span>
          )}
        </div>
        <p className="mpa-popup-body">
          Administrative kelp beds are CDFW management boundaries for
          commercial kelp harvest. Lease status indicates whether the
          bed is open to harvest, available for lease, currently leased,
          or closed.
        </p>
        <p className="mpa-popup-meta mono">
          {kelp.areaKm2 ? `${kelp.areaKm2} km²` : ""}
        </p>
        <a
          className="mpa-popup-link"
          href={officialUrl}
          target="_blank"
          rel="noreferrer"
        >
          ↗ Official CDFW kelp page
        </a>
        <p className="mpa-popup-disclaimer">
          Kelp bed zones are management / reference boundaries and may
          not represent current kelp canopy.
        </p>
        <button
          type="button"
          className="mpa-popup-done"
          onClick={onClose}
        >
          Back to map
        </button>
      </div>
    </div>
  );
}
