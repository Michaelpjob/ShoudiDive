// Region switcher chip in the top bar. Flips between regions that
// the current hostname permits. Stored in `localStorage.region` + the
// `?region=` URL param so a refreshed tab and a bookmarked URL both
// reach the same view. See `src/lib/region.js` for the resolution
// logic.

import { activeRegion, setActiveRegion, isRegionLocked, validRegionsForHost } from "../lib/region.js";

const REGION_LABELS = {
  ca:       "California",
  pnw:      "Pacific NW (beta)",
  tropical: "FL + Caribbean (beta)",
  baja:     "Baja Mexico",
};

export default function RegionSwitcher() {
  // On pnw-beta.shouldidive.pages.dev / tropical-beta.shouldidive.pages.dev
  // / baja-beta.shouldidive.pages.dev the hostname locks the region —
  // switching here would be a no-op since activeRegion() reads hostname
  // first. Hide the chip entirely so visitors don't see a misleading
  // control.
  if (isRegionLocked()) return null;
  // Filter chips to whatever the current host actually serves:
  //   * shouldidive.com (prod)         → ca + baja
  //   * dev.shouldidive.pages.dev      → all four
  //   * localhost / preview branches   → all four
  // Hide the chip entirely when only one region is available — there's
  // nothing to switch to.
  const allowed = validRegionsForHost();
  if (allowed.length <= 1) return null;
  const current = activeRegion();
  return (
    <label className="region-switcher" title="Switch ocean region">
      <span className="region-switcher-label">Region</span>
      <select
        value={current}
        onChange={(e) => setActiveRegion(e.target.value)}
        aria-label="Region"
      >
        {allowed.map((id) => (
          <option key={id} value={id}>{REGION_LABELS[id] || id}</option>
        ))}
      </select>
    </label>
  );
}
