// SST 5-day forecast loader. Same pattern as sst7d: fetch summary,
// decode per-day PNGs in parallel, write into state.layers.sst[<slot>]
// keyed by forecast slot (f0..f4/f6) with forecast:true so the
// timeline UI can distinguish it from observed history.
//
// Carved out of dataSource.js loadManifest's if/else if chain on
// 2026-05-09 (Tier-1 follow-up).
//
// Defensive slot synthesis (added 2026-05-09 fix):
//   The committed summary.json that ships from fetch_sst_5day.py
//   used to omit the `slot` field on each day. Without it the
//   loader's `if (!d?.slot)` guard silently dropped every day,
//   nothing got written to state.layers.sst.f0..fN, and the
//   forecast slider had no data to render. The pipeline is fixed
//   to emit `slot: "f<offset>"` going forward; this loader also
//   synthesizes the slot from `offset` (or the day's index in the
//   array) so a legacy summary still loads correctly. Same for
//   range/scale fallbacks: the loader now treats summary-level
//   `range_c` and a default linear `scale` as valid, matching
//   what fetch_sst_5day.py emits and what fetch.py emits.

import { decodePng } from "./decoders.js";
import { rewriteManifestUrls } from "../region.js";

export async function loadSst5d(info, state) {
  try {
    const sres = await fetch(info.summary_url, { cache: "no-cache" });
    if (!sres.ok) throw new Error(`sst forecast summary ${info.summary_url} ${sres.status}`);
    // Region rewrite: see sst7d.js for the rationale.
    const summary = rewriteManifestUrls(await sres.json());
    state.layers.sst5d = { summary };
    state.layers.sst = state.layers.sst || {};
    const scale = info.scale || summary.scale || "linear";
    // Tolerate either ``range`` (preferred — matches fetch.py) or
    // ``range_c`` (legacy from fetch_sst_5day.py before today's fix).
    const range = info.range || summary.range || summary.range_c;
    if (!range) throw new Error("sst5d has no range");
    const tasks = [];
    (summary.days || []).forEach((d, idx) => {
      if (!d?.url) return;
      // Slot resolution waterfall: explicit slot > "f<offset>" >
      // "f<idx>". The first arm matches fetch.py emissions; the
      // second matches fetch_sst_5day.py emissions (offset is
      // always present); the third is a last-resort fallback.
      const slot =
        d.slot ||
        (Number.isInteger(d.offset) ? `f${d.offset}` : `f${idx}`);
      tasks.push(
        decodePng(d.url, scale, range)
          .then((decoded) => {
            state.layers.sst[slot] = {
              ...decoded,
              dates: d.date ? [d.date] : [],
              forecast: true,
              stats: d,
            };
          })
          .catch((e) => console.warn(`sst5d ${slot} failed`, e))
      );
    });
    await Promise.all(tasks);
  } catch (e) {
    console.warn("dataSource: sst5d summary load failed", e);
  }
}
