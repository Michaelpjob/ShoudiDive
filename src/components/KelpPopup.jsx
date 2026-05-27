// Kelp-bed popup describing a single CDFW Administrative Kelp Bed.
// Cloned from MpaPopup.jsx as part of the Kelp Bed Zones MVP
// (Phase 1, Task C in outputs/KELP-M~1.MD).
//
// IMPORTANT: These are management/reference boundaries for commercial
// kelp harvest — NOT observed canopy. The disclaimer at the bottom
// makes that explicit so divers don't mistake the polygons for "kelp
// is currently here."

import { useEffect, useState } from "react";
import { styleForStatus } from "./KelpLayer.jsx";
import { dataPath } from "../lib/region.js";

// PR-K5-3: load kelp storm-strip risk JSON once per app session.
// Single shared promise mirrors the kelp/MPA fetch pattern; null
// resolves when the file doesn't exist (non-CA regions, or before
// the pipeline has computed it).
let stormPromise = null;
function loadKelpStormRisk() {
  if (stormPromise) return stormPromise;
  stormPromise = fetch(dataPath("/data/kelp-storm-risk.json"))
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return stormPromise;
}

function labelForStatus(status) {
  if (!status) return "Unknown";
  const s = String(status).toLowerCase();
  if (s === "open") return "Open";
  if (s === "leasable") return "Leasable";
  if (s === "leased") return "Leased";
  if (s === "closed") return "Closed";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export default function KelpPopup({ kelp, onClose, onZoomTo }) {
  const style = styleForStatus(kelp.status);
  const officialUrl =
    "https://wildlife.ca.gov/Conservation/Marine/Kelp";
  // PR-K5-3: storm-strip risk banner. Fetch the global risk record
  // (per-bed precision is a v2 enhancement — most CA beds share wave
  // conditions so the bbox-wide flag is meaningful for v1).
  const [stormRisk, setStormRisk] = useState(null);
  useEffect(() => {
    let cancelled = false;
    loadKelpStormRisk().then((r) => {
      if (!cancelled) setStormRisk(r);
    });
    return () => { cancelled = true; };
  }, []);
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
        {/* PR-K5-3: storm-strip risk banner — shown above the body
            so divers see it before status/area details. Yellow tone
            mirrors the legend "sketchy" Hs band. */}
        {stormRisk?.active && stormRisk.peak && (
          <div className="kelp-storm-banner" role="alert">
            <span className="kelp-storm-banner-icon" aria-hidden="true">⚠</span>
            <div className="kelp-storm-banner-text">
              <strong>Recent / forecast storm Hs {stormRisk.peak.max_hs_ft} ft</strong>
              {" — canopy may be stripped, viz could degrade for "}
              {stormRisk.warning_tail_days || 7}{" days."}
              {stormRisk.peak.date && (
                <span className="kelp-storm-banner-meta mono">
                  {" "}peak {stormRisk.peak.date} {stormRisk.peak.bucket || ""}
                </span>
              )}
            </div>
          </div>
        )}
        <p className="mpa-popup-body">
          Administrative kelp beds are CDFW management boundaries for
          commercial kelp harvest. Lease status indicates whether the
          bed is open to harvest, available for lease, currently leased,
          or closed.
        </p>
        {/* Lease detail when present — surfaced 2026-05-27 once we
            verified the actual ds3135 schema includes Lessee +
            TermEnds. Not every bed has these (open beds have neither;
            leasable beds have neither either; leased beds usually
            have both). */}
        {(kelp.lessee || kelp.termEnds) && (
          <p className="mpa-popup-meta">
            {kelp.lessee && <>Lessee: <strong>{kelp.lessee}</strong></>}
            {kelp.lessee && kelp.termEnds && " · "}
            {kelp.termEnds && <>Lease ends: <strong>{kelp.termEnds}</strong></>}
          </p>
        )}
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
        {/* PR-K2-3: "Zoom to bed" action — jumps the viewBox to fit
            the bed polygon with padding. Hidden when onZoomTo isn't
            wired so the popup stays usable in test/storybook contexts. */}
        {onZoomTo && kelp._geometry && (
          <button
            type="button"
            className="mpa-popup-zoom"
            onClick={() => {
              onZoomTo(kelp._geometry);
              onClose();
            }}
          >
            ⤢ Zoom to bed
          </button>
        )}
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
