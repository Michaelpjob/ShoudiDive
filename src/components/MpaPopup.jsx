// MPA popup + helper functions describing CDFW marine protected areas.
// Carved out of App.jsx (2026-05-09) as part of the Tier-1 architecture
// split.

import { useEffect } from "react";
import { styleForType } from "./MpaLayer.jsx";

function verdictForType(type) {
  if (!type) return { kind: "limited", icon: "⚠", label: "CHECK RULES" };
  const t = type.toUpperCase();
  if (t.includes("NO-TAKE") || t === "SMR" || t === "FMR")
    return { kind: "no", icon: "⛔", label: "NO TAKE" };
  if (t === "SMP" || t === "SMRMA")
    return { kind: "ok", icon: "✓", label: "TAKE ALLOWED" };
  return { kind: "limited", icon: "⚠", label: "LIMITED TAKE" };
}

function fullNameForType(type) {
  const map = {
    SMR: "State Marine Reserve",
    SMCA: "State Marine Conservation Area",
    "SMCA (No-Take)": "State Marine Conservation Area · No Take",
    SMP: "State Marine Park",
    SMRMA: "State Marine Recreational Management Area",
    FMR: "Federal Marine Reserve",
    FMCA: "Federal Marine Conservation Area",
    "Special Closure": "Special Closure",
  };
  return map[type] || "Marine Protected Area";
}

export default function MpaPopup({ mpa, onClose }) {
  const style = styleForType(mpa.type);
  const officialUrl =
    "https://wildlife.ca.gov/Conservation/Marine/MPAs/Network";
  const verdict = verdictForType(mpa.type);
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
        aria-label={`${mpa.name} MPA details`}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="mpa-popup-close"
          onClick={onClose}
          aria-label="Close MPA details"
        >
          ×
        </button>
        <div className="mpa-popup-head">
          <div>
            <div className="mpa-popup-name">{mpa.name}</div>
            <div className="mpa-popup-fullname">{fullNameForType(mpa.type)}</div>
          </div>
          <span className="mpa-pill" style={{ background: style.fill, borderColor: style.stroke, color: style.stroke }}>
            {mpa.type}
          </span>
        </div>
        <div className={"mpa-verdict mpa-verdict-" + verdict.kind}>
          <span className="mpa-verdict-icon">{verdict.icon}</span>
          <strong>{verdict.label}</strong>
        </div>
        <p className="mpa-popup-body">
          {verdict.kind === "no" && (
            <>Take of any living marine resource is generally prohibited inside this area.</>
          )}
          {verdict.kind === "limited" && (
            <>Limited recreational take is allowed — specific species and methods only. <strong>Verify with CDFW before harvesting.</strong></>
          )}
          {verdict.kind === "ok" && (
            <>Most recreational take is allowed within this area; specific exclusions may apply.</>
          )}
        </p>
        <p className="mpa-popup-meta mono">
          {mpa.areaKm2 ? `${mpa.areaKm2} km² · ` : ""}
          {mpa.ccrCitation || "CCR Title 14 §632"}
        </p>
        <a
          className="mpa-popup-link"
          href={officialUrl}
          target="_blank"
          rel="noreferrer"
        >
          ↗ Official CDFW regulation page
        </a>
        <p className="mpa-popup-disclaimer">
          Information shown is for planning purposes only. Verify with CDFW before harvesting.
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
