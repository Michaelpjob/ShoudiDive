// Contract checks for the OBSERVED-ONLY clarity view — every clarity cell is
// a direct retrieval at that cell, or it is blank. Nothing is filled in from
// neighbours (no gap-fill product, no fillNearest smear, no offshore reach).
//
// The pipeline emits per-cell provenance (chl_1d_source.png source ids,
// viz_quality.png tiers) and declares them in the manifest. These tests pin
// the chain that turns that provenance into honest blanks:
//   chl_1d_source.png → blank gap-fill cells (source 4/5/6)
//   viz_quality.png   → blank estimate cells (tier 0 or >=3)
//   + no fillNearest smear, + no offshore shell-reach in the spot sampler,
//   + discrete pixelated cells.
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

test("viz loader BLANKS estimate cells and drops the fillNearest smear", () => {
  const src = read("src/lib/loaders/viz.js");
  assert.match(src, /decodeRawPng/, "viz loader must decode viz_quality via decodeRawPng");
  assert.match(src, /quality_url/, "viz loader must read the manifest quality_url");
  // Keep OBSERVED_1D(1)/OBSERVED_3D(2); blank no-data(0) + INTERPOLATED/
  // PREDICTED/CLIMATOLOGY (>=3) to NaN.
  assert.match(src, /c === 0 \|\| c >= 3/, "blank rule covers code 0 and >=3");
  assert.match(src, /decoded\.data\[i\]\s*=\s*NaN/, "estimate cells must be NaN'd, not kept");
  // The smear that manufactured a colored map out of blind cells must be gone
  // — not imported, not called (a mention in a comment is fine).
  assert.doesNotMatch(src, /import\s*\{[^}]*fillNearestInPlace/, "must not import fillNearestInPlace");
  assert.doesNotMatch(src, /fillNearestInPlace\s*\(/, "must not call fillNearestInPlace");
});

test("chl loader BLANKS gap-fill cells (never backfills from neighbours)", () => {
  const src = read("src/lib/loaders/scalarPng.js");
  assert.match(src, /decodeRawPng/, "must decode chl_1d_source via decodeRawPng");
  assert.match(src, /source_url/, "must read the manifest source_url (chl ships one)");
  // Gap-fill priorities = DINEOF (4/5) + Copernicus GlobColour (6); NASA
  // direct (1-3) and raw VIIRS (7) are real retrievals, kept.
  assert.match(src, /GAP_FILL_SOURCE_CODES\s*=\s*new Set\(\[4,\s*5,\s*6\]\)/);
  assert.match(src, /decoded\.data\[i\]\s*=\s*NaN/, "gap-fill cells must be NaN'd (blank)");
});

test("DataOverlay paints observed cells opaque, blanks transparent, cells discrete", () => {
  const src = read("src/components/DataOverlay.jsx");
  // No veil/fade any more — a cell is a real value (opaque) or blank (NaN).
  assert.doesNotMatch(src, /VEIL_ALPHA/, "veil alpha removed — cells blank, not faded");
  assert.match(src, /img\.data\[i \* 4 \+ 3\]\s*=\s*255/, "finite cells fully opaque");
  // Clarity layers render as discrete cells (nearest-neighbour), not smoothed.
  assert.match(src, /PIXELATED_LAYERS\s*=\s*new Set\(\["chl",\s*"viz"\]\)/);
  assert.match(src, /imageRendering:\s*PIXELATED_LAYERS\.has\(layer\)\s*\?\s*"pixelated"/);
});

test("spot sampler does not reach offshore for blank clarity cells", () => {
  const src = read("src/lib/dataSource.js");
  // bilinear takes a maxReach; <=0 means "no neighbour shell — return NaN".
  assert.match(src, /function bilinear\(layer, lng, lat, maxReach = 6\)/);
  assert.match(src, /if \(maxReach <= 0\) return NaN/);
  // chl + viz spot accessors pass 0 (observed-only, no reach).
  assert.match(src, /state\.layers\.chl[\s\S]{0,60}lng, lat, 0\)/, "getChl must pass maxReach=0");
  assert.match(src, /state\.layers\.viz[\s\S]{0,60}lng, lat, 0\)/, "getVizFt must pass maxReach=0");
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
  // The final override: any interpolated cell → INTERPOLATED, so viz_quality
  // marks gap-fills as estimates (which the frontend then blanks).
  assert.match(
    model,
    /out\[interpolated_mask\.astype\(bool\)\]\s*=\s*"INTERPOLATED"/,
    "interpolated_mask must override OBSERVED/PREDICTED labels",
  );
});
