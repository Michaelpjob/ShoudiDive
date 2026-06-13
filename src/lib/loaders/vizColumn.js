// viz_column loader (PRD water-column V-group) — decodes the
// two-layer water-column rasters + per-spot sidecar + the regional
// bathy grid the tap-to-slice readout anchors to.
//
// State shape written:
//   state.layers.viz_column = {
//     now: { below: {data,w,h}, cliff: {data,w,h}, valid_at },
//     bathy: {data,w,h} | null,   // depth METERS (560x440 grid)
//     spots: { <id>: {...} } | null,  // viz_column_spots.json
//     swing_ft, beta, method,
//   }
//
// Bathy note: bathy.png/.json are static files outside the manifest
// layers map (BathyLayer renders markers from the geojson instead),
// so this loader fetches them directly — the column is the first
// consumer that needs gridded depth on the frontend.
import { decodePng, fillNearestInPlace } from "./decoders.js";
import { dataPath } from "../region.js";

export async function loadVizColumn(info, state) {
  const vc = (state.layers.viz_column = state.layers.viz_column || {});
  vc.swing_ft = info.swing_ft ?? null;
  vc.beta = !!info.beta;
  vc.method = info.method || null;

  for (const [slot, w] of Object.entries(info.windows || {})) {
    if (!w?.url || !w?.cliff_url) continue;
    const [below, cliff] = await Promise.all([
      decodePng(w.url, "linear", info.range_ft),
      decodePng(w.cliff_url, "linear", info.cliff_range_ft),
    ]);
    // Same shoreline gap-fill the surface viz layer gets, so a tap on
    // a beach cell still resolves a column.
    fillNearestInPlace(below, 30);
    fillNearestInPlace(cliff, 30);
    vc[slot] = { below, cliff, valid_at: w.valid_at };
  }

  // Optional extras — each degrades to null without blocking the map
  // (PRD error rule). Fetched in parallel.
  const [spots, bathy] = await Promise.all([
    info.spots_url
      ? fetch(dataPath(info.spots_url), { cache: "no-cache" })
          .then((r) => (r.ok ? r.json() : null))
          .then((doc) => doc?.spots || null)
          .catch(() => null)
      : Promise.resolve(null),
    fetch(dataPath("/data/bathy.json"), { cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : null))
      .then((meta) => {
        if (!meta?.depth_range_m) return null;
        const [lo, hi] = meta.depth_range_m;
        return decodePng(dataPath("/data/bathy.png"), "linear", [lo, hi]);
      })
      .catch(() => null),
  ]);
  vc.spots = spots;
  vc.bathy = bathy;
}
