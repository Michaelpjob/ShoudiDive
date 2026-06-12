// Small confidence indicator dot next to a layer chip. Renders the
// 5-tier score from src/lib/confidence.js as a colored circle with a
// hover tooltip explaining the source, the calibration state, and any
// dynamic modulation (e.g. "only 30% chl coverage today").
//
// Used by both DesktopLayout (layer-toggle) and MobileSheet (ms-chip).

import { getLayerConfidence } from "../lib/confidence.js";

export default function ConfidenceDot({ layer, size = 8, className = "" }) {
  const conf = getLayerConfidence(layer);
  if (!conf) return null;
  const reasons = [conf.reason, ...conf.modReasons].filter(Boolean).join(" · ");
  const title =
    `${conf.label} (${conf.score}/5)\n` +
    `Source: ${conf.source}\n` +
    `${reasons}` +
    (conf.score < conf.ceilingScore
      ? `\n(today's score is ${conf.ceilingScore - conf.score} below ceiling)`
      : "");
  return (
    <span
      className={`confidence-dot ${className}`.trim()}
      title={title}
      aria-label={`${conf.label}, ${conf.score} of 5`}
      style={{
        display: "inline-block",
        width: size,
        height: size,
        borderRadius: "50%",
        background: conf.color,
        verticalAlign: "middle",
        boxShadow: "0 0 0 1px rgba(0,0,0,0.18)",
      }}
    />
  );
}
