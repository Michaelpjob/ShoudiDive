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

// chl source-priority codes that mean "gap-filled / spatially-interpolated
// product" (chl_1d_source.png stores the winning source priority per cell):
// 4/5 = NOAA DINEOF, 6 = Copernicus GlobColour "gap-free". Those values are
// derived from NEIGHBOURING cells, not a retrieval at the cell itself.
// Priorities 1-3 (NASA MODIS/VIIRS/OLCI direct) and 7 (raw VIIRS) ARE direct
// retrievals.
const GAP_FILL_SOURCE_CODES = new Set([4, 5, 6]);

// Observed-only is also FRESH-only: a real retrieval that's more than a few
// days old is stale — blooms and clarity shift within days. chl NRT publishes
// ~1-3 days behind, so a cell whose freshest real look is > this many days old
// means the satellite hasn't had a clear view here recently. Blank it. Matches
// the viz model's OBSERVED_3D freshness line. Per-layer; layers absent from
// this map get no age gate.
export const OBSERVED_FRESH_DAYS = { chl: 3 };

// Blank (NaN) every cell that isn't a VERIFIED, FRESH observation. Mutates
// `data` in place and returns counts. Pure array logic — no DOM — so it's
// unit-tested directly.
//   * gap-fill source (spatially interpolated from neighbours) → blank
//   * age > freshDays, OR age unknown (sidecar says no-data) → blank
// `source` / `age` are the raw per-cell code arrays from decodeRawPng, or
// null when that sidecar is absent (then that gate is skipped). Age codes:
// 0 = no-data sentinel; code-1 = age in whole days.
// Balanced confidence veil (chosen 2026-07-06): rather than NaN-blank every
// unverified cell (which turned the chl map into a boxy checkerboard of holes),
// keep the value and encode confidence as per-cell OPACITY. Fresh verified
// satellite obs paint solid; gap-filled / aging cells paint FADED; only genuine
// no-data (already-NaN) stays transparent. Uncertainty becomes translucency —
// nothing is hidden or fabricated. See DataOverlay (per-cell alpha) + the
// "How to read this" copy.
export const GAP_FILL_CONFIDENCE = 0.40;    // DINEOF / GlobColour spatial fill
export const UNKNOWN_AGE_CONFIDENCE = 0.30; // age sidecar = no-data sentinel
const AGE_DECAY_DAYS = 7;                    // past the fresh window, fade over ~7d
const AGE_CONFIDENCE_FLOOR = 0.30;          // a real (if old) obs never fully vanishes

// Per-cell confidence in [0, 1] from the provenance sidecars. 1 = fresh verified
// observation; lower = gap-filled and/or aging; 0 = genuine no-data. Pure +
// allocation-light so it runs in the per-layer-change budget.
export function computeConfidence(data, { source, age, gapFillCodes, freshDays } = {}) {
  const conf = new Float32Array(data.length);
  const ageGate = age && Number.isFinite(freshDays);
  for (let i = 0; i < data.length; i++) {
    if (!Number.isFinite(data[i])) { conf[i] = 0; continue; } // truly no data
    let c = 1.0;
    if (source && gapFillCodes && gapFillCodes.has(source[i])) c = GAP_FILL_CONFIDENCE;
    if (ageGate) {
      const code = age[i];
      if (code === 0) {
        c = Math.min(c, UNKNOWN_AGE_CONFIDENCE);
      } else {
        const days = code - 1;
        if (days > freshDays) {
          const decay = Math.min(1, Math.max(
            AGE_CONFIDENCE_FLOOR, 1 - (days - freshDays) / AGE_DECAY_DAYS));
          c *= decay;
        }
      }
    }
    conf[i] = c;
  }
  return conf;
}

export function blankUnverifiedCells(data, { source, age, gapFillCodes, freshDays } = {}) {
  let blankedGapFill = 0;
  let blankedStale = 0;
  const ageGate = age && Number.isFinite(freshDays);
  for (let i = 0; i < data.length; i++) {
    if (!Number.isFinite(data[i])) continue; // already blank
    if (source && gapFillCodes && gapFillCodes.has(source[i])) {
      data[i] = NaN;
      blankedGapFill++;
      continue;
    }
    if (ageGate) {
      const code = age[i];
      if (code === 0 || code - 1 > freshDays) {
        data[i] = NaN;
        blankedStale++;
      }
    }
  }
  return { blankedGapFill, blankedStale };
}

export async function loadScalarPng(layer, info, state) {
  state.layers[layer] = state.layers[layer] || {};
  const scale = info.scale || "linear";
  const range = info.range;
  if (!range) {
    console.warn(`dataSource: ${layer} has no range, skipping`);
    return;
  }
  const freshDays = OBSERVED_FRESH_DAYS[layer];
  for (const [win, w] of Object.entries(info.windows || {})) {
    try {
      const decoded = await decodePng(w.url, scale, range);
      // Observed-only + fresh-only blanking from the per-cell provenance
      // sidecars (chl's 1d window ships source_url + age_days_url). Opt-in:
      // windows without the sidecars are left as decoded (SST legacy, chl
      // 2d/3d). Categorical/age rasters decode RAW, never smeared.
      const source = await loadSidecarCodes(w.source_url, decoded);
      const age = await loadSidecarCodes(w.age_days_url, decoded);
      if (source || (age && Number.isFinite(freshDays))) {
        // Confidence veil (not blanking): keep values, encode trust as opacity.
        decoded.confidence = computeConfidence(decoded.data, {
          source, age, gapFillCodes: GAP_FILL_SOURCE_CODES, freshDays,
        });
        let faded = 0, solid = 0;
        for (let i = 0; i < decoded.confidence.length; i++) {
          const c = decoded.confidence[i];
          if (c > 0 && c < 1) faded++; else if (c >= 1) solid++;
        }
        console.info(
          `dataSource: ${layer}/${win} confidence veil — ${solid} solid + ${faded} faded ` +
          `(gap-fill/aging) cells`,
        );
      }
      state.layers[layer][win] = { ...decoded, dates: w.dates || [] };
    } catch (e) {
      console.warn(`dataSource: ${layer}/${win} decode failed`, e);
    }
  }
}

// Decode a categorical/age sidecar to its raw per-cell code array, but only if
// it exists AND its grid matches the value grid. Returns null otherwise (that
// gate is then skipped rather than mis-aligning cells).
async function loadSidecarCodes(url, decoded) {
  if (!url) return null;
  try {
    const s = await decodeRawPng(url);
    if (s.width === decoded.width && s.height === decoded.height) return s.codes;
  } catch {
    /* fall through */
  }
  return null;
}
