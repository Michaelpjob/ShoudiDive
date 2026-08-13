// GPS-ready description of a traced temperature break. Pure functions so
// the format is unit-testable without a DOM.
//
// Marine chartplotters overwhelmingly take degrees + decimal minutes
// (DDM, e.g. 33°25.271'N), phone apps take decimal degrees. The copy
// block carries both, one waypoint per line, so a user can paste or type
// either form straight into whatever they navigate with.

// Grid pixel (center) -> [lng, lat] for a grid of w×h over bbox
// {latMin, latMax, lngMin, lngMax}. Row 0 = latMax (fetch.py's flip).
export function gridToLngLat(gx, gy, w, h, bbox) {
  const lng = bbox.lngMin + ((gx + 0.5) / w) * (bbox.lngMax - bbox.lngMin);
  const lat = bbox.latMax - ((gy + 0.5) / h) * (bbox.latMax - bbox.latMin);
  return [lng, lat];
}

// 33.4212 -> "33°25.272'N" — degrees + decimal minutes, 3 decimals
// (≈ 2 m of latitude; far finer than the ~2 km data cell, but standard
// chartplotter entry precision).
export function toDDM(value, isLat) {
  const hemi = isLat ? (value >= 0 ? "N" : "S") : (value >= 0 ? "E" : "W");
  const abs = Math.abs(value);
  const deg = Math.floor(abs);
  const min = (abs - deg) * 60;
  return `${deg}°${min.toFixed(3)}'${hemi}`;
}

export function formatWaypoint([lng, lat]) {
  return {
    ddm: `${toDDM(lat, true)}, ${toDDM(lng, false)}`,
    dec: `${lat.toFixed(4)}, ${lng.toFixed(4)}`,
  };
}

// Pick start / evenly-spaced mids / end along the stem. Always includes
// both endpoints; mids only when the line is long enough to need them.
export function pickWaypoints(points, maxCount = 5) {
  if (points.length <= maxCount) return [...points];
  const out = [];
  for (let i = 0; i < maxCount; i++) {
    out.push(points[Math.round((i * (points.length - 1)) / (maxCount - 1))]);
  }
  return out;
}

/**
 * The copy-paste block for one front.
 * @param front  {spanKm, points} from computeBreakMask
 * @param grid   {width, height}
 * @param bbox   {latMin, latMax, lngMin, lngMax}
 * @param dataDate ISO date string of the SST observation, or null
 */
export function buildGpsText(front, grid, bbox, dataDate) {
  const wps = pickWaypoints(front.points).map((p) =>
    formatWaypoint(gridToLngLat(p[0], p[1], grid.width, grid.height, bbox))
  );
  const labels =
    wps.length === 2
      ? ["Start", "End"]
      : ["Start", ...wps.slice(1, -1).map((_, i) => `Mid ${i + 1}`), "End"];
  const lines = [
    `Temperature break — ~${front.spanKm} km` +
      (dataDate ? ` (satellite data ${dataDate})` : ""),
    ...wps.map((wp, i) => `${labels[i].padEnd(6)} ${wp.ddm}  (${wp.dec})`),
    "Position drifts day to day — treat as a search line, not a pin.",
  ];
  return lines.join("\n");
}
