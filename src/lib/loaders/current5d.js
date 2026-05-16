// 5-day × 5-bucket surface-current forecast loader. Decodes the
// uv_url for each bucket, then applies the cached land-mask to drop
// land-cell samples (HFR + the inland-extension blend can paint into
// land otherwise — visually fine for the streamlines, wrong for the
// scalar speed sample under the cursor).
//
// Carved out of dataSource.js loadManifest's if/else if chain on
// 2026-05-09 (Tier-1 follow-up).

import {
  decodeUVPng,
  computeSpeedKt,
  bucketKey,
  currentSampleMask,
  landMaskedCurrentSample,
} from "./decoders.js";
import { rewriteManifestUrls } from "../region.js";

export async function loadCurrent5d(info, state) {
  try {
    const sres = await fetch(info.summary_url, { cache: "no-cache" });
    if (!sres.ok) throw new Error(`currents summary ${info.summary_url} ${sres.status}`);
    // Region rewrite: see sst7d.js.
    const summary = rewriteManifestUrls(await sres.json());
    state.layers.current5d = {
      summary,
      uvRange: info.uv_range,
      speedRange: info.speed_range,
      buckets: {},
    };
    const tasks = [];
    let failed = 0;
    let loaded = 0;
    for (const day of summary.days || []) {
      for (const b of day.buckets || []) {
        const key = bucketKey(day.day, b.bucket);
        tasks.push(
          decodeUVPng(b.uv_url, info.uv_range)
            .then((uv) => {
              const maskPromise = currentSampleMask(uv.width, uv.height);
              return maskPromise.then((landMask) => ({ uv, landMask }));
            })
            .then(({ uv, landMask }) => {
              const speed = computeSpeedKt(uv);
              const sample = landMaskedCurrentSample(uv, landMask);
              const sampleSpeed = computeSpeedKt(sample);
              state.layers.current5d.buckets[key] = {
                uvU: sample.u, uvV: sample.v,
                visualUvU: uv.u, visualUvV: uv.v,
                width: uv.width, height: uv.height,
                data: speed, speedKt: speed,
                sampleData: sampleSpeed, sampleSpeedKt: sampleSpeed,
              };
              loaded++;
            })
            .catch((e) => {
              failed++;
              console.warn(`current5d bucket ${key} failed`, e);
            })
        );
      }
    }
    await Promise.all(tasks);
    if (failed) {
      console.warn(`current5d: ${loaded} buckets loaded, ${failed} failed`);
    }
  } catch (e) {
    console.warn("dataSource: current5d summary load failed", e);
  }
}
