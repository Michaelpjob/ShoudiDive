// Region switcher chip in the top bar. Flips between CA / PNW /
// FL+Caribbean. Stored in `localStorage.region` + the `?region=` URL
// param so a refreshed tab and a bookmarked URL both reach the same
// view. See `src/lib/region.js` for the resolution logic.

import { activeRegion, setActiveRegion } from "../lib/region.js";

const REGIONS = [
  { id: "ca",       label: "California" },
  { id: "pnw",      label: "Pacific NW (beta)" },
  { id: "tropical", label: "FL + Caribbean (beta)" },
];

export default function RegionSwitcher() {
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
