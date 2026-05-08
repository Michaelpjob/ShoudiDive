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

import { decodePng } from "./decoders.js";

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
      state.layers[layer][win] = { ...decoded, dates: w.dates || [] };
    } catch (e) {
      console.warn(`dataSource: ${layer}/${win} decode failed`, e);
    }
  }
}
