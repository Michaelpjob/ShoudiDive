// Region switcher chip in the top bar. Flips between CA / PNW /
// FL+Caribbean. Stored in `localStorage.region` + the `?region=` URL
// param so a refreshed tab and a bookmarked URL both reach the same
// view. See `src/lib/region.js` for the resolution logic.

import { activeRegion, setActiveRegion, isRegionLocked } from "../lib/region.js";

const REGIONS = [
  { id: "ca",       label: "California" },
  { id: "pnw",      label: "Pacific NW (beta)" },
  { id: "tropical", label: "FL + Caribbean (beta)" },
];

export default function RegionSwitcher() {
  // On pnw-beta.shouldidive.pages.dev / tropical-beta.shouldidive.pages.dev
  // the hostname locks the region — switching here would be a no-op
  // since activeRegion() reads hostname first. Hide the chip entirely
  // so visitors to those subdomains don't see a misleading control.
  if (isRegionLocked()) return null;
  const current = activeRegion();
  return (
    <label className="region-switcher" title="Switch ocean region">
      <span className="region-switcher-label">Region</span>
      <select
        value={current}
        onChange={(e) => setActiveRegion(e.target.value)}
        aria-label="Region"
      >
        {REGIONS.map((r) => (
          <option key={r.id} value={r.id}>{r.label}</option>
        ))}
      </select>
    </label>
  );
}
