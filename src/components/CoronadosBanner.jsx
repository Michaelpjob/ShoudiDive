// US-Mexico maritime boundary disclaimer banner. Carved out of App.jsx
// (2026-05-09) as part of the Tier-1 architecture split.
//
// US-Mexico maritime boundary is at ~32.534°N. When the MPA layer is on
// AND the visible viewBox dips below that, surface a small disclaimer.
// Dismissable: an × button hides it for the rest of the page session
// (mobile users repeatedly hit it covering the bottom strip when
// they're zoomed in on Coronados, which is half the reason to look at
// that part of the map).

import { useState } from "react";
import { unproject } from "../lib/mapData.js";

export default function CoronadosBanner({ vb, size }) {
  const [dismissed, setDismissed] = useState(false);
  if (!vb || !size.h) return null;
  if (dismissed) return null;
  const [, visibleSouthLat] = unproject(0, vb.y + vb.h, size.w, size.h);
  if (visibleSouthLat > 32.534) return null;
  return (
    <div className="mpa-banner">
      <span>
        MPA data covers California waters only. The Coronados sit inside
        Mexico's Islas del Pacífico Biosphere Reserve — see{" "}
        <a href="https://www.gob.mx/conanp" target="_blank" rel="noreferrer">CONANP</a>.
      </span>
      <button
        className="mpa-banner-close"
        onClick={() => setDismissed(true)}
        aria-label="Dismiss notice"
      >
        ×
      </button>
    </div>
  );
}
