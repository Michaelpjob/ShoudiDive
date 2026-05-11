// 5-day × 5-bucket swell forecast (gfswave). Loads summary.json first;
// bucket + hourly wave PNGs (RGBA Hs/Tp/Dp) load in parallel with
// per-task error tolerance.
//
// Carved out of dataSource.js loadManifest's if/else if chain on
// 2026-05-09 (Tier-1 follow-up).

import { decodeWavePng, bucketKey } from "./decoders.js";
import { rewriteManifestUrls } from "../region.js";

export async function loadSwell5d(info, state) {
  try {
    const sres = await fetch(info.summary_url, { cache: "no-cache" });
    if (!sres.ok) throw new Error(`swell summary ${info.summary_url} ${sres.status}`);
    // Region rewrite: see sst7d.js.
    const summary = rewriteManifestUrls(await sres.json());
    state.layers.swell5d = {
      summary,
      heightRange: info.height_range_m,
      periodRange: info.period_range_s,
      buckets:     {},
      hourly:      {},
      hourlyLoading: {},
    };
    const tasks = [];
    for (const day of summary.days) {
      for (const b of day.buckets) {
        const key = bucketKey(day.day, b.bucket);
        tasks.push(
          decodeWavePng(b.wave_url, info.height_range_m, info.period_range_s)
            .then((wv) => {
              state.layers.swell5d.buckets[key] = wv;
            })
            .catch((e) => console.warn(`swell5d bucket ${key} failed`, e))
        );
      }
    }
    await Promise.all(tasks);
  } catch (e) {
    console.warn("dataSource: swell5d summary load failed", e);
  }
}
