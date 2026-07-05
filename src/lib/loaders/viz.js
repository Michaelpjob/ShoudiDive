// Predicted-visibility loader — OBSERVED-ONLY.
//
// Historically this ran fillNearestInPlace at 30 iterations to smear valid
// cells into the marine-layer / cloud holes that riddle SoCal chl coverage,
// so the user saw a colored map instead of a hatched bight. That is exactly
// the "a blind cell gets a neighbour's value" behavior we now reject: a cell
// with no real observation must read blank, not borrow. So the smear is gone,
// and cells whose clarity is an ESTIMATE (interpolated / model-predicted /
// climatology, per viz_quality.png) are blanked to NaN — neither drawn on
// the map nor returned by the spot sampler.
//
// Carved out of dataSource.js loadManifest's if/else if chain on
// 2026-05-09 (Tier-1 follow-up).

import { decodePng, decodeRawPng } from "./decoders.js";

export async function loadViz(info, state) {
  state.layers.viz = state.layers.viz || {};
  const range = info.range_ft;
  for (const [slot, w] of Object.entries(info.windows || {})) {
    const decoded = await decodePng(w.url, "linear", range);
    // viz_quality.png tiers each cell: 1=OBSERVED_1D / 2=OBSERVED_3D are real
    // retrievals (keep); 0=no-data and 3+ (INTERPOLATED / PREDICTED_* /
    // CLIMATOLOGY_ONLY) are estimates derived from neighbours or seasonal
    // averages — blank them (NaN). Categorical raster: decode RAW.
    if (w.quality_url) {
      try {
        const q = await decodeRawPng(w.quality_url);
        if (q.width === decoded.width && q.height === decoded.height) {
          let blanked = 0;
          for (let i = 0; i < q.codes.length; i++) {
            const c = q.codes[i];
            if (c === 0 || c >= 3) {
              decoded.data[i] = NaN;
              blanked++;
            }
          }
          if (blanked) {
            console.info(`dataSource: viz/${slot} observed-only — blanked ${blanked} estimate cells`);
          }
        }
      } catch {
        /* no quality sidecar → leave the grid as decoded */
      }
    }
    state.layers.viz[slot] = { ...decoded, valid_at: w.valid_at };
  }
}
