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

import { decodePng, decodeRawPng, fillNearestInPlace } from "./decoders.js";

export async function loadViz(info, state) {
  state.layers.viz = state.layers.viz || {};
  const range = info.range_ft;
  for (const [slot, w] of Object.entries(info.windows || {})) {
    const decoded = await decodePng(w.url, "linear", range);
    fillNearestInPlace(decoded, 30);
    // Per-cell quality flag (viz_quality.png): 0=no-data, 1=OBSERVED_1D,
    // 2=OBSERVED_3D, 3=INTERPOLATED, 4-6=PREDICTED_*, 7=CLIMATOLOGY_ONLY.
    // Kept RAW (never smeared by fillNearestInPlace) so the confidence veil
    // in DataOverlay can fade cells whose clarity is a gap-fill / model
    // estimate rather than a direct satellite observation. Optional: the
    // layer still renders if the sidecar is absent or mis-sized.
    let quality = null;
    if (w.quality_url) {
      try {
        const q = await decodeRawPng(w.quality_url);
        if (q.width === decoded.width && q.height === decoded.height) {
          quality = q.codes;
        }
      } catch {
        quality = null;
      }
    }
    state.layers.viz[slot] = { ...decoded, quality, valid_at: w.valid_at };
  }
}
