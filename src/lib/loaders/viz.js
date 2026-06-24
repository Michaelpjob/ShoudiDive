// Predicted-visibility loader. The viz layer ships range_ft instead of
// a generic range field, and runs fillNearestInPlace at 30 iterations
// (much higher than the default 8) because SoCal coverage is patchy:
// chl-a satellite passes get nuked by the marine layer over Catalina /
// SD / Coronados most cycles, and the viz model returns NaN wherever
// any input is missing. Smearing valid neighbours into NaN cells gets
// the user a colored map instead of a hatched bight. LandBasemap
// paints over the land cells filled by the same pass, so coastline
// accuracy is unaffected.
//
// Carved out of dataSource.js loadManifest's if/else if chain on
// 2026-05-09 (Tier-1 follow-up).

import { decodePng, fillNearestInPlace } from "./decoders.js";

export async function loadViz(info, state) {
  state.layers.viz = state.layers.viz || {};
  const range = info.range_ft;
  for (const [slot, w] of Object.entries(info.windows || {})) {
    const decoded = await decodePng(w.url, "linear", range);
    fillNearestInPlace(decoded, 30);
    // Also decode the p10/p90 uncertainty band when the manifest publishes it
    // (it always does today), so the readout can show the range + spread, not
    // just the median. Optional: absence leaves bandP10/bandP90 undefined and
    // the UI falls back to the median alone.
    let bandP10, bandP90;
    if (w.p10_url) { bandP10 = await decodePng(w.p10_url, "linear", range); fillNearestInPlace(bandP10, 30); }
    if (w.p90_url) { bandP90 = await decodePng(w.p90_url, "linear", range); fillNearestInPlace(bandP90, 30); }
    state.layers.viz[slot] = { ...decoded, bandP10, bandP90, valid_at: w.valid_at };
  }
}
