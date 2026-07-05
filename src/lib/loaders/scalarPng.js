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

// chl source-priority codes that mean "gap-filled / interpolated product"
// (chl_1d_source.png stores the winning source priority per cell): 4/5 =
// NOAA DINEOF, 6 = Copernicus GlobColour "gap-free". Priorities 1-3 (NASA
// MODIS/VIIRS/OLCI direct) and 7 (raw VIIRS) are real satellite retrievals.
// Cells from a gap-fill source get veiled so a smoothed "gin-clear" value —
// which washes out nearshore blooms, especially while the NASA feed is down
// and the whole grid is GlobColour — doesn't paint as a confident reading.
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
      // Confidence veil from the per-cell source sidecar (chl ships one on
      // its 1d window). Opt-in: layers/windows without source_url are
      // unaffected. Kept RAW (categorical source ids, never smeared).
      let veil = null;
      if (w.source_url) {
        try {
          const s = await decodeRawPng(w.source_url);
          if (s.width === decoded.width && s.height === decoded.height) {
            veil = new Uint8Array(s.codes.length);
            for (let i = 0; i < s.codes.length; i++) {
              veil[i] = GAP_FILL_SOURCE_CODES.has(s.codes[i]) ? 1 : 0;
            }
          }
        } catch {
          veil = null;
        }
      }
      state.layers[layer][win] = { ...decoded, dates: w.dates || [], veil };
    } catch (e) {
      console.warn(`dataSource: ${layer}/${win} decode failed`, e);
    }
  }
}
