// Legacy 4-slot wind loader (now / +6h / +24h / +72h). Each slot has
// a matching speed PNG (linear, m/s) and a uv PNG (signed components).
//
// Carved out of dataSource.js loadManifest's if/else if chain on
// 2026-05-09 (Tier-1 follow-up).

import { decodePng, decodeUVPng } from "./decoders.js";

export async function loadWind(info, state) {
  state.layers.wind = state.layers.wind || {};
  const speedRange = info.speed_range;
  const uvRange = info.uv_range;
  for (const [slot, w] of Object.entries(info.windows || {})) {
    const speed = await decodePng(w.speed_url, "linear", speedRange);
    const uv = await decodeUVPng(w.uv_url, uvRange);
    state.layers.wind[slot] = {
      ...speed,           // .data, .width, .height for speed
      uvU: uv.u,
      uvV: uv.v,
      valid_at: w.valid_at,
      fcst_hour: w.fcst_hour,
      source: w.source || null,  // "HRRR" / "GFS" — for the legend
    };
  }
}
