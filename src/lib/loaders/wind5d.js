// 5-day × 5-bucket wind forecast loader. Pulls summary.json first —
// keep the summary even if individual bucket PNGs fail to decode so
// the day grid still renders with whatever data we have.
//
// Carved out of dataSource.js loadManifest's if/else if chain on
// 2026-05-09 (Tier-1 follow-up).

import { decodeUVPng, computeSpeedKt, bucketKey } from "./decoders.js";

export async function loadWind5d(info, state) {
  try {
    const sres = await fetch(info.summary_url, { cache: "no-cache" });
    if (!sres.ok) throw new Error(`summary ${info.summary_url} ${sres.status}`);
    const summary = await sres.json();
    state.layers.wind5d = {
      summary,
      uvRange:    info.uv_range,
      speedRange: info.speed_range,
      buckets:    {},
      hourly:     {},
      hourlyLoading: {},
    };
    // Load every bucket UV in parallel. CRUCIAL: each task swallows
    // its own error so one bad PNG can't take down the whole forecast.
    const tasks = [];
    let failed = 0;
    let loaded = 0;
    for (const day of summary.days) {
      for (const b of day.buckets) {
        const key = bucketKey(day.day, b.bucket);
        tasks.push(
          decodeUVPng(b.uv_url, info.uv_range)
            .then((uv) => {
              const speed = computeSpeedKt(uv);
              state.layers.wind5d.buckets[key] = {
                uvU: uv.u, uvV: uv.v,
                width: uv.width, height: uv.height,
                data: speed, speedKt: speed,
              };
              loaded++;
            })
            .catch((e) => {
              failed++;
              console.warn(`wind5d bucket ${key} failed`, e);
            })
        );
      }
    }
    await Promise.all(tasks);
    if (failed) {
      console.warn(`wind5d: ${loaded} buckets loaded, ${failed} failed`);
    }
  } catch (e) {
    console.warn("dataSource: wind5d summary load failed", e);
    // Don't null out — leave whatever summary did parse so the UI
    // can show the day labels even if the heatmap is missing.
  }
}
