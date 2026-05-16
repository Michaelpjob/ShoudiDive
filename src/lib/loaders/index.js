// Per-layer loader registry. Replaces the giant if/else if chain that
// used to live inside dataSource.js loadManifest.
//
// Adding a new layer in the future is now a 2-step diff:
//   1. Create `src/lib/loaders/<layerName>.js` exporting
//      `export async function load<LayerName>(info, state)`.
//   2. Register it below.
//
// No more 50-line copy-paste of "fetch summary, decode PNGs, write
// state.layers[name]". No more risk of duplicate `else if` branches
// (which is the exact failure mode that ESLint's `no-dupe-else-if`
// rule had to be load-bearing for after the 2026-05-07 incident).
//
// Layers absent from this map are silently skipped. wave + precip
// fall in that bucket on purpose — they're pipeline inputs, not
// frontend-rendered layers.

import { loadSst7d } from "./sst7d.js";
import { loadSst5d } from "./sst5d.js";
import { loadSwell5d } from "./swell5d.js";
import { loadWind5d } from "./wind5d.js";
import { loadCurrent5d } from "./current5d.js";
import { loadRtofs5d } from "./rtofs5d.js";
import { loadWind } from "./wind.js";
import { loadViz } from "./viz.js";
import { loadScalarPng } from "./scalarPng.js";

// Each entry: layer name → (info, state) => Promise<void>.
// scalarPng layers route through a single helper that takes the
// layer name as the first arg, since they share identical logic.
export const LAYER_LOADERS = {
  sst7d:    (info, state) => loadSst7d(info, state),
  sst5d:    (info, state) => loadSst5d(info, state),
  swell5d:  (info, state) => loadSwell5d(info, state),
  wind5d:   (info, state) => loadWind5d(info, state),
  current5d:(info, state) => loadCurrent5d(info, state),
  // rtofs5d is a parallel ocean-model forecast track to sst5d.
  // Loader plumbs the data into state; UI exposure (toggle / compare
  // view / difference map) is a deferred product decision — see
  // src/lib/loaders/rtofs5d.js docstring.
  rtofs5d:  (info, state) => loadRtofs5d(info, state),
  wind:     (info, state) => loadWind(info, state),
  viz:      (info, state) => loadViz(info, state),
  sst:      (info, state) => loadScalarPng("sst", info, state),
  chl:      (info, state) => loadScalarPng("chl", info, state),
  // kd490 has the same shape as chl. Wire it in once the frontend
  // actually renders a kd490 overlay (today it's manifest-only).
  // kd490: (info, state) => loadScalarPng("kd490", info, state),
};

// Re-export the helpers so callers that need them outside loadManifest
// (loadSwell5dHourly, loadWind5dHourly inside dataSource.js, plus
// CurrentTimeline / WindDayGrid via dataSource.js's re-export chain)
// can import from one place.
export {
  decodePng,
  decodeUVPng,
  decodeWavePng,
  fillNearestInPlace,
  computeSpeedKt,
  currentSampleMask,
  landMaskedCurrentSample,
  bucketKey,
  hourKey,
} from "./decoders.js";
