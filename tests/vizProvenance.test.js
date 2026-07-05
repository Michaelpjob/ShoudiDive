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

test("loadViz decodes the quality sidecar and keeps it RAW (not smeared)", () => {
  const src = read("src/lib/loaders/viz.js");
  assert.match(src, /decodeRawPng/, "viz loader must decode viz_quality via decodeRawPng");
  assert.match(src, /quality_url/, "viz loader must read the manifest quality_url");
  assert.match(src, /quality/, "decoded quality must be attached to the slot");
  // fillNearestInPlace runs on the VALUE grid only; quality must not be smeared.
  const fillIdx = src.indexOf("fillNearestInPlace");
  const qualIdx = src.indexOf("decodeRawPng");
  assert.ok(fillIdx >= 0 && qualIdx > fillIdx, "quality decode happens after value fill, separately");
});

test("DataOverlay veils viz cells whose quality is not a direct observation", () => {
  const src = read("src/components/DataOverlay.jsx");
  assert.match(src, /VIZ_VEIL_ALPHA/, "must define a veil alpha");
  assert.match(src, /grid\.quality/, "must read the per-cell quality grid");
  // Trust only OBSERVED_1D(1)/OBSERVED_3D(2); veil no-data(0) + INTERPOLATED/
  // PREDICTED/CLIMATOLOGY (>=3).
  assert.match(
    src,
    /q === 0 \|\| q >= 3/,
    "veil rule must fade code 0 (no-data) and >=3 (interpolated/predicted/climo)",
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
