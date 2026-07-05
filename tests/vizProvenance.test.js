// Contract checks for the viz "confidence veil" — the end-to-end wiring that
// stops gap-filled clarity from painting as a confident observation.
//
// The pipeline already emits a per-cell quality raster (viz_quality.png) and
// declares it in the manifest, but the frontend used to ignore it and every
// cell rendered opaque. These tests pin the full chain:
//   chl_1d_source.png → interpolated_mask → assign_quality(INTERPOLATED)
//   → viz_quality.png → loadViz(quality) → DataOverlay veil.
//
// Source-string style (node's test runner, no jest) like the other
// tests/*.test.js — asserts the wiring exists without booting a canvas.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(resolve(repoRoot, rel), "utf8");

test("decoders.js exposes a raw (categorical) PNG decoder", () => {
  const src = read("src/lib/loaders/decoders.js");
  assert.match(src, /export async function decodeRawPng/);
  // Must pass codes through unchanged (no range mapping / NaN) — it's a class id.
  assert.match(src, /codes\[i\]\s*=\s*id\.data\[i \* 4\]/);
});

test("loadViz builds a veil mask from the quality sidecar (raw, not smeared)", () => {
  const src = read("src/lib/loaders/viz.js");
  assert.match(src, /decodeRawPng/, "viz loader must decode viz_quality via decodeRawPng");
  assert.match(src, /quality_url/, "viz loader must read the manifest quality_url");
  assert.match(src, /\bveil\b/, "must attach a veil mask to the slot");
  // Trust only OBSERVED_1D(1)/OBSERVED_3D(2); veil no-data(0) + INTERPOLATED/
  // PREDICTED/CLIMATOLOGY (>=3).
  assert.match(src, /c === 0 \|\| c >= 3/, "veil rule fades code 0 and >=3");
  // fillNearestInPlace runs on the VALUE grid only; quality must not be smeared.
  const fillIdx = src.indexOf("fillNearestInPlace");
  const qualIdx = src.indexOf("decodeRawPng");
  assert.ok(fillIdx >= 0 && qualIdx > fillIdx, "quality decode happens after value fill, separately");
});

test("chl loader builds a veil from the gap-fill source sidecar", () => {
  const src = read("src/lib/loaders/scalarPng.js");
  assert.match(src, /decodeRawPng/, "must decode chl_1d_source via decodeRawPng");
  assert.match(src, /source_url/, "must read the manifest source_url (chl ships one)");
  // Gap-fill priorities = DINEOF (4/5) + Copernicus GlobColour (6); NASA
  // direct (1-3) and raw VIIRS (7) are real retrievals, not veiled.
  assert.match(src, /GAP_FILL_SOURCE_CODES\s*=\s*new Set\(\[4,\s*5,\s*6\]\)/);
  assert.match(src, /\bveil\b/, "must attach a veil mask to the slot");
});

test("DataOverlay dims estimate cells via the generic per-cell veil mask", () => {
  const src = read("src/components/DataOverlay.jsx");
  assert.match(src, /VEIL_ALPHA/, "must define a veil alpha");
  assert.match(src, /grid\.veil/, "must read the per-cell veil mask (layer-agnostic)");
  assert.match(
    src,
    /veilMask && veilMask\[i\]\)\s*\?\s*VEIL_ALPHA\s*:\s*255/,
    "veiled cells paint at VEIL_ALPHA, others opaque",
  );
});

test("pipeline builds interpolated_mask from the chl source sidecar and passes it", () => {
  const viz = read("pipeline/fetch_visibility.py");
  assert.match(viz, /chl_1d_source\.png/, "must read the per-cell chl source sidecar");
  // Priorities 4/5/6 = DINEOF + Copernicus GlobColour gap-fill products.
  assert.match(viz, /GAP_FILL_PRIORITIES\s*=\s*\(4,\s*5,\s*6\)/);
  assert.match(viz, /interpolated_mask=interpolated_mask/, "must pass the mask into predict_all");
});

test("assign_quality treats a gap-fill as INTERPOLATED regardless of age", () => {
  const model = read("pipeline/viz_predict/model.py");
  // The final override: any interpolated cell → INTERPOLATED, overriding
  // OBSERVED_* / PREDICTED_*.
  assert.match(
    model,
    /out\[interpolated_mask\.astype\(bool\)\]\s*=\s*"INTERPOLATED"/,
    "interpolated_mask must override OBSERVED/PREDICTED labels",
  );
});
