// Field Reports observation popup — shows one real ground-truth report
// (what was measured, when, by whom). Reuses the .mpa-popup* chrome so it
// matches the MPA/Bathy popups without new CSS.

import { styleForKind } from "./FieldReportsLayer.jsx";

const KIND_LABEL = {
  buoy: "Buoy",
  turbidity: "Turbidity sensor",
  dive_report: "Dive report",
};

function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function FieldReportsPopup({ observation, onClose }) {
  const o = observation;
  const sty = styleForKind(o.kind);
  const kindLabel = KIND_LABEL[o.kind] || "Observation";
  return (
    <div className="mpa-popup-overlay" onClick={onClose}>
      <div className="mpa-popup" onClick={(e) => e.stopPropagation()}>
        <div className="mpa-popup-head">
          <div>
            <div className="mpa-popup-name">{o.spot || kindLabel}</div>
            <div className="mpa-popup-fullname">
              {kindLabel}{o.when ? ` · ${fmtWhen(o.when)}` : ""}
            </div>
          </div>
          <span
            className="mpa-pill"
            style={{ background: "transparent", borderColor: sty.color, color: sty.color }}
          >
            {o.what || "obs"}
          </span>
        </div>

        {o.value != null && (
          <p className="mpa-popup-meta mono">
            {o.what}: {o.value}{o.unit ? ` ${o.unit}` : ""}
          </p>
        )}

        <p className="mpa-popup-meta mono">Source: {o.source || "n/a"}</p>

        <p className="mpa-popup-disclaimer">
          A real observation, recorded as reported. Conditions change — verify locally.
        </p>
      </div>
    </div>
  );
}
