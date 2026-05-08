// SST 5-day forecast loader. Same pattern as sst7d: fetch summary,
// decode per-day PNGs in parallel, write into state.layers.sst[<slot>]
// keyed by forecast slot (f0..f4) with history:false / forecast:true
// to differentiate from history slots.
//
// Carved out of dataSource.js loadManifest's if/else if chain on
// 2026-05-09 (Tier-1 follow-up).

import { decodePng } from "./decoders.js";

export async function loadSst5d(info, state) {
  try {
    const sres = await fetch(info.summary_url, { cache: "no-cache" });
    if (!sres.ok) throw new Error(`sst forecast summary ${info.summary_url} ${sres.status}`);
    const summary = await sres.json();
    state.layers.sst5d = { summary };
    state.layers.sst = state.layers.sst || {};
    const scale = info.scale || summary.scale || "linear";
    const range = info.range || summary.range;
    if (!range) throw new Error("sst5d has no range");
    const tasks = [];
    for (const d of summary.days || []) {
      if (!d?.slot || !d?.url) continue;
      tasks.push(
        decodePng(d.url, scale, range)
          .then((decoded) => {
            state.layers.sst[d.slot] = {
              ...decoded,
              dates: d.date ? [d.date] : [],
              forecast: true,
              stats: d,
            };
          })
          .catch((e) => console.warn(`sst5d ${d.slot} failed`, e))
      );
    }
    await Promise.all(tasks);
  } catch (e) {
    console.warn("dataSource: sst5d summary load failed", e);
  }
}
