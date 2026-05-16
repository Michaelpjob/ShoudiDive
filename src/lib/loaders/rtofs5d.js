// RTOFS ocean-model 5-day forecast loader.
//
// Parallel forecast track to sst5d: NOAA RTOFS Global publishes daily
// ocean-model forecast cycles at NOMADS. Where sst5d is anomaly-decay
// persistence (simple, fast, no physics), rtofs5d carries actual ocean
// dynamics — advection, wind forcing, thermocline. Plus surface currents,
// which fill the HFRNet zero-coverage Caribbean gap.
//
// Sampled at +1d / +3d / +5d / +7d (4 leads). The pipeline writes:
//   public/data/<region>/rtofs/
//     summary.json       — { generated_at, init_cycle, days[] }
//     sst_d{1,3,5,7}.png — SST PNG (linear, range from manifest)
//     uv_d{1,3,5,7}.png  — RGBA currents (R=u, G=v over [-2,2] m/s, A=0 NaN)
//
// Output shape on `state.layers.rtofs5d`:
//   {
//     summary,                              // raw summary.json, region-rewritten
//     sst:    { d1: decoded, d3: decoded, d5: decoded, d7: decoded },
//     uv:     { d1: decoded, d3: decoded, d5: decoded, d7: decoded },
//     model:  "NOAA_RTOFS_Global_2ds_prog",
//     init_cycle: "20260513T00:00:00Z",
//   }
//
// `decoded` from decodePng / decodeUVPng — same shape the other
// forecast layers use, so the future UI compare-toggle can sample
// pixel values with the existing decoder helpers.
//
// UI wiring is intentionally NOT done here. The data flows into state
// and is inspectable via DevTools (`window.__appState?.layers?.rtofs5d`)
// for ad-hoc validation. A toggle / overlay / difference-map is a
// product decision pending; this loader plumbs the data so that work,
// when it lands, doesn't have to plumb pipeline → manifest → state.

import { decodePng, decodeUVPng } from "./decoders.js";
import { rewriteManifestUrls } from "../region.js";

export async function loadRtofs5d(info, state) {
  try {
    const sres = await fetch(info.summary_url, { cache: "no-cache" });
    if (!sres.ok) {
      throw new Error(`rtofs5d summary ${info.summary_url} ${sres.status}`);
    }
    const summary = rewriteManifestUrls(await sres.json());
    state.layers.rtofs5d = {
      summary,
      sst: {},
      uv: {},
      model: info.model || summary.model || "RTOFS",
      init_cycle: info.init_cycle || summary.init_cycle || null,
    };

    // SST decode range: manifest `range` (matches sst5d range for the
    // region), falling back to summary `sst_range_c`.
    const sstRange = info.range || summary.sst_range_c;
    if (!sstRange) {
      throw new Error("rtofs5d has no SST range");
    }
    // UV decode range: manifest `uv_range`, falling back to summary
    // `uv_range_ms`. Same RGBA encoding the wind UV PNGs use.
    const uvRange = info.uv_range || summary.uv_range_ms;
    if (!uvRange) {
      throw new Error("rtofs5d has no UV range");
    }

    const tasks = [];
    (summary.days || []).forEach((d) => {
      const dayOffset = Number.isInteger(d?.day_offset) ? d.day_offset : null;
      if (dayOffset === null) return;
      const slot = `d${dayOffset}`;

      if (d.sst_url) {
        tasks.push(
          decodePng(d.sst_url, "linear", sstRange)
            .then((decoded) => {
              state.layers.rtofs5d.sst[slot] = {
                ...decoded,
                dates: d.date ? [d.date] : [],
                stats: d,
              };
            })
            .catch((e) => console.warn(`rtofs5d sst ${slot} failed`, e)),
        );
      }
      if (d.uv_url) {
        tasks.push(
          // decodeUVPng signature: (url, [lo, hi]) — tuple, not separate args.
          decodeUVPng(d.uv_url, uvRange)
            .then((decoded) => {
              state.layers.rtofs5d.uv[slot] = {
                ...decoded,
                dates: d.date ? [d.date] : [],
                stats: d,
              };
            })
            .catch((e) => console.warn(`rtofs5d uv ${slot} failed`, e)),
        );
      }
    });
    await Promise.all(tasks);
  } catch (e) {
    console.warn("dataSource: rtofs5d load failed", e);
  }
}
