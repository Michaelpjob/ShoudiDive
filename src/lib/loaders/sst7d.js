// SST 7-day history loader. Fetches summary.json then decodes each
// daily PNG in parallel, writing into state.layers.sst[<slot>] keyed
// by slot name (d0, d1, ..., d6).
//
// Carved out of dataSource.js loadManifest's if/else if chain on
// 2026-05-09 (Tier-1 follow-up). Behaviour is byte-for-byte equivalent
// to the original branch.

import { decodePng } from "./decoders.js";
import { rewriteManifestUrls } from "../region.js";

export async function loadSst7d(info, state) {
  try {
    const sres = await fetch(info.summary_url, { cache: "no-cache" });
    if (!sres.ok) throw new Error(`sst summary ${info.summary_url} ${sres.status}`);
    // Region rewrite: summary.json carries bare `/data/sst/history/d0.png`
    // URLs from the pipeline (CA-relative). For PNW + tropical those
    // resolve to 404 unless we rewrite them to `/data/<region>/...`.
    const summary = rewriteManifestUrls(await sres.json());
    state.layers.sst7d = { summary };
    state.layers.sst = state.layers.sst || {};
    const scale = info.scale || summary.scale || "linear";
    const range = info.range || summary.range;
    if (!range) throw new Error("sst7d has no range");
    const tasks = [];
    for (const d of summary.days || []) {
      if (!d?.slot || !d?.url) continue;
      tasks.push(
        decodePng(d.url, scale, range)
          .then((decoded) => {
            state.layers.sst[d.slot] = {
              ...decoded,
              dates: d.date ? [d.date] : [],
              history: true,
              stats: d,
            };
          })
          .catch((e) => console.warn(`sst7d ${d.slot} failed`, e))
      );
    }
    await Promise.all(tasks);
  } catch (e) {
    console.warn("dataSource: sst7d summary load failed", e);
  }
}
