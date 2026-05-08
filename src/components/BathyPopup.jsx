// Bathymetry feature popup — seamounts, banks, reefs, and community
// dive spots. Carved out of App.jsx (2026-05-09) as part of the Tier-1
// architecture split.

import { styleForClass } from "./BathyLayer.jsx";

export default function BathyPopup({ feature, onClose }) {
  const sty = styleForClass(feature.class);
  const isCommunity = feature.class === "community-spot";
  const classLabel =
    feature.class === "seamount" ? "Seamount"
    : feature.class === "bank" ? "Bank"
    : feature.class === "reef" ? "Reef"
    : feature.class === "basin" ? "Basin"
    : feature.class === "trough" ? "Trough"
    : feature.class === "anchorage" ? "Anchorage"
    : feature.class === "landmark" ? "Landmark"
    : "Community spot";
  return (
    <div className="mpa-popup-overlay" onClick={onClose}>
      <div className="mpa-popup" onClick={(e) => e.stopPropagation()}>
        <div className="mpa-popup-head">
          <div>
            <div className="mpa-popup-name">{feature.name}</div>
            <div className="mpa-popup-fullname">{classLabel}</div>
          </div>
          <span
            className="mpa-pill"
            style={{
              background: "transparent",
              borderColor: sty.color,
              color: sty.color,
            }}
          >
            {sty.glyph} {feature.shortName || feature.name}
          </span>
        </div>

        {(feature.minDepthFt || feature.minDepthM) && (
          <p className="mpa-popup-meta mono">
            {feature.minDepthFt ? `Min depth ${feature.minDepthFt} ft` : ""}
            {feature.minDepthFt && feature.minDepthM ? ` (${feature.minDepthM} m)` : ""}
            {!feature.minDepthFt && feature.minDepthM ? `Min depth ${feature.minDepthM} m` : ""}
          </p>
        )}

        {feature.description && (
          <p className="mpa-popup-body">{feature.description}</p>
        )}

        <p className="mpa-popup-meta mono">
          Source: {feature.source || "n/a"}
        </p>

        {isCommunity && (
          <p className="mpa-popup-disclaimer">
            Community-sourced. Verify locally and stay clear of MPAs.
          </p>
        )}
        {!isCommunity && (
          <p className="mpa-popup-disclaimer">
            For navigation, verify with current NOAA charts.
          </p>
        )}
      </div>
    </div>
  );
}
