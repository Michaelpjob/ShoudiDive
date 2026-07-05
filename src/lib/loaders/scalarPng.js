// Generic scalar-PNG loader for layers that ship a plain `range` +
// `scale` + `windows` map. Used by sst (legacy 1d/2d/3d composites)
// and chl + kd490.
//
// Wave + precip live in the manifest as inputs to the visibility
// pipeline (server-side); the frontend has no wave/precip overlays
// to paint, so they are NOT in the loader registry — they get
// silently skipped. (Trying to decode them would just throw on the
// missing `range` field and take down the entire loader's outer
// try/catch — exactly the bug that nuked every layer including
// wind5d before the per-layer guards landed.)
//
// Carved out of dataSource.js loadManifest's if/else if chain on
// 2026-05-09 (Tier-1 follow-up).

import { decodePng, decodeRawPng } from "./decoders.js";

// chl source-priority codes that mean "gap-filled / spatially-interpolated
// product" (chl_1d_source.png stores the winning source priority per cell):
// 4/5 = NOAA DINEOF, 6 = Copernicus GlobColour "gap-free". Those values are
// derived from NEIGHBOURING cells, not a retrieval at the cell itself.
// Priorities 1-3 (NASA MODIS/VIIRS/OLCI direct) and 7 (raw VIIRS) ARE direct
// retrievals. Observed-only view: blank the gap-fill cells (set NaN) so a
// smoothed "gin-clear" value — which washes out nearshore blooms — is never
// shown or sampled. A blank cell is honest ("no observation here"); it is
// NOT backfilled from its neighbours.
const GAP_FILL_SOURCE_CODES = new Set([4, 5, 6]);

export async function loadScalarPng(layer, info, state) {
  state.layers[layer] = state.layers[layer] || {};
  const scale = info.scale || "linear";
  const range = info.range;
  if (!range) {
    console.warn(`dataSource: ${layer} has no range, skipping`);
    return;
  }
  for (const [win, w] of Object.entries(info.windows || {})) {
    try {
      const decoded = await decodePng(w.url, scale, range);
      // Observed-only blanking from the per-cell source sidecar (chl ships
      // one on its 1d window). Opt-in: layers/windows without source_url are
      // unaffected (SST legacy, etc.). Source raster is categorical — decode
      // RAW, never smeared.
      if (w.source_url) {
        try {
          const s = await decodeRawPng(w.source_url);
          if (s.width === decoded.width && s.height === decoded.height) {
            let blanked = 0;
            for (let i = 0; i < s.codes.length; i++) {
              if (GAP_FILL_SOURCE_CODES.has(s.codes[i])) {
                decoded.data[i] = NaN;
                blanked++;
              }
            }
            if (blanked) {
              console.info(
                `dataSource: ${layer}/${win} observed-only — blanked ${blanked} gap-fill cells`,
              );
            }
          }
        } catch {
          /* no source sidecar → leave the grid as decoded */
        }
      }
      state.layers[layer][win] = { ...decoded, dates: w.dates || [] };
    } catch (e) {
      console.warn(`dataSource: ${layer}/${win} decode failed`, e);
    }
  }
}
