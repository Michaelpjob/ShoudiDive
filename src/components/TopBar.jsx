// Top bar — brand + last-update timestamp + region switcher + Venmo tip + settings cog.
// Carved out of App.jsx (2026-05-09) as part of the Tier-1 architecture
// split. Lives outside the ErrorBoundary so the status indicator + gear
// stay reachable even if DesktopView crashes during render.

import RegionSwitcher from "./RegionSwitcher.jsx";
import { activeRegion } from "../lib/region.js";
import { getLayerConfidence } from "../lib/confidence.js";
import { track } from "../lib/analytics.js";

const LAYER_NAMES = {
  sst:     "Sea Temp",
  chl:     "Chl-a",
  wind:    "Wind",
  swell:   "Swell",
  current: "Current",
  viz:     "Visibility",
};

const REGION_TAGLINES = {
  ca:       "Sea Temp · Water Clarity · Wind · Current · California Coast",
  pnw:      "Pacific Northwest (beta) · Oregon + Washington + Salish Sea",
  tropical: "FL + Caribbean (beta) · Gulf + Keys + Bahamas + Greater & Lesser Antilles",
  baja:     "Baja Mexico (beta) · Pacific + Sea of Cortez · Ensenada to Cabo + La Paz",
};

// Dive flag — the universal "diver below" maritime symbol. Red square,
// white diagonal stripe. Reads instantly at any size (the previous
// freediver silhouette degraded into a fuzzy Y at the topbar's ~20 px
// rendering). Explicit colors so it stays legible in both light and
// dark themes without depending on currentColor.
function FreediverLogo() {
  return (
    <svg
      className="brand-mark"
      viewBox="0 0 32 32"
      aria-hidden="true"
      role="img"
    >
      <rect x="3" y="3" width="26" height="26" rx="5" fill="#dc2626" />
      <path
        d="M27 6 L 6 27"
        stroke="#ffffff"
        strokeWidth="5.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function TopBar({ onSettings, settingsOpen, dataState, layer }) {
  const generated = dataState?.manifest?.generated_at;
  const lastUpdate = generated
    ? new Date(generated).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
        timeZoneName: "short",
      })
    : "Apr 24, 2026 06:42 UTC";
  const status = !dataState?.ready
    ? "Loading"
    : generated
    ? "Live"
    : "Demo data";
  return (
    <div className="topbar">
      <div className="brand">
        <FreediverLogo />
        <div>
          <div className="brand-name">ShouldIDive</div>
        </div>
        <span className="brand-tag">
          {REGION_TAGLINES[activeRegion()] || REGION_TAGLINES.ca}
        </span>
      </div>
      <div className="topbar-meta">
        <RegionSwitcher />
        {(() => {
          // Active-layer confidence badge. Updates as the user clicks
          // between layer chips (Temp / Chl / Wind / Swell / Current /
          // Vis). The chip-strip dots still show the regional overview
          // for ALL layers at once; this badge zooms into "should I
          // trust the layer I'm currently looking at?" — the actionable
          // read for a diver mid-decision.
          if (!layer) return null;
          const lc = getLayerConfidence(layer);
          if (!lc) return null;
          const layerName = LAYER_NAMES[layer] || layer;
          const tooltipReasons =
            [lc.reason, ...lc.modReasons].filter(Boolean).join("\n");
          return (
            <span
              className="layer-confidence"
              title={
                `${layerName} confidence: ${lc.label} (${lc.score}/5)\n` +
                `Source: ${lc.source}\n` +
                tooltipReasons +
                (lc.score < lc.ceilingScore
                  ? `\n(today's score is ${lc.ceilingScore - lc.score} below ceiling)`
                  : "")
              }
              aria-label={`${layerName} confidence ${lc.label}, ${lc.score} of 5`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "2px 8px",
                borderRadius: 999,
                border: "1px solid var(--line, rgba(0,0,0,0.12))",
                fontSize: 12,
              }}
            >
              <span
                style={{
                  display: "inline-block",
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: lc.color,
                  boxShadow: "0 0 0 1px rgba(0,0,0,0.18)",
                }}
              />
              <span style={{ opacity: 0.72 }}>{layerName}</span>
              <strong>{lc.label}</strong>
              <span style={{ opacity: 0.65 }}>{lc.score}/5</span>
            </span>
          );
        })()}
        <span>
          <span className="dot"></span>
          <strong>{status}</strong> · Last update{" "}
          <span className="mono">{lastUpdate}</span>
        </span>
        <span>
          Sources: <strong>NOAA · IOOS · NASA OB.DAAC · Copernicus</strong>
        </span>
        {/* Tip jar — visible on every layer, every device. The WSB
            joke (white sea bass — the trophy spear fish in SoCal) +
            small fish silhouette reads as a wink to free divers
            specifically. Click opens the Venmo profile in a new tab. */}
        <a
          href="https://venmo.com/u/michaelpjob"
          target="_blank"
          rel="noopener noreferrer"
          className="tip-fish"
          title="Tip the creator on Venmo (@michaelpjob)"
          onClick={() => track("tip_click", { source: "topbar" })}
        >
          <svg
            className="tip-fish-icon"
            width="16"
            height="14"
            viewBox="0 0 32 28"
            fill="currentColor"
            aria-hidden="true"
          >
            {/* Simple silhouette: oval body + triangle tail. Renders
                crisp at 14-18 px, scales to retina without blurring. */}
            <path d="M19 4 C 11 4, 4 9, 2 14 C 4 19, 11 24, 19 24
                     C 23 24, 26 22, 28 20 L 31 24 L 31 4 L 28 8
                     C 26 6, 23 4, 19 4 Z M 21 12 a 1.5 1.5 0 1 1 0 3
                     a 1.5 1.5 0 0 1 0 -3 Z" />
          </svg>
          <span className="tip-fish-text">click for WSB</span>
        </a>
        {/* The MobileShell peek strip carries layer/value/time info on
            phones — the topbar just keeps the brand mark + settings cog
            on small screens (timestamp + sources are hidden via the
            mobile-shell @media block in app.css; that media query
            mirrors MOBILE_QUERY in App.jsx so JS and CSS agree on what
            counts as "mobile"). */}
        <button
          className="icon-btn"
          aria-label="Settings"
          aria-pressed={settingsOpen}
          onClick={onSettings}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>
    </div>
  );
}
