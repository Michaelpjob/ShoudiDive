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

test("chl loader reads source + age sidecars and gates on both", () => {
  const src = read("src/lib/loaders/scalarPng.js");
  assert.match(src, /decodeRawPng/, "must decode sidecars via decodeRawPng");
  assert.match(src, /source_url/, "must read the manifest source_url");
  assert.match(src, /age_days_url/, "must read the manifest age_days_url (freshness gate)");
  // Gap-fill priorities = DINEOF (4/5) + Copernicus GlobColour (6); NASA
  // direct (1-3) and raw VIIRS (7) are real retrievals, kept.
  assert.match(src, /GAP_FILL_SOURCE_CODES\s*=\s*new Set\(\[4,\s*5,\s*6\]\)/);
  assert.match(src, /OBSERVED_FRESH_DAYS\s*=\s*\{\s*chl:\s*3\s*\}/, "chl freshness budget = 3 days");
});

test("blankUnverifiedCells keeps only real + fresh cells, blanks the rest", async () => {
  const { blankUnverifiedCells } = await import("../src/lib/loaders/scalarPng.js");
  const GAP = new Set([4, 5, 6]);
  // 6 cells. source: 2=VIIRS(real) 6=GlobColour(gap) 1=MODIS(real) 7=rawVIIRS(real) 3=OLCI(real) 2=VIIRS(real)
  // age codes (0=no-data, code-1 = days): 3→2d, 3→2d, 13→12d(STALE), 2→1d, 0→unknown, 4→3d
  const data = new Float32Array([0.3, 0.4, 0.5, 0.6, 0.7, 0.8]);
  const source = new Uint8Array([2, 6, 1, 7, 3, 2]);
  const age = new Uint8Array([3, 3, 13, 2, 0, 4]);
  const res = blankUnverifiedCells(data, { source, age, gapFillCodes: GAP, freshDays: 3 });
  // cell0: real + 2d fresh → KEEP
  // cell1: gap-fill → BLANK (gap)
  // cell2: real but 12d → BLANK (stale)
  // cell3: real + 1d → KEEP
  // cell4: real but age unknown (code 0) → BLANK (can't verify freshness)
  // cell5: real + 3d (== budget) → KEEP
  assert.equal(res.blankedGapFill, 1);
  assert.equal(res.blankedStale, 2);
  assert.ok(Number.isFinite(data[0]) && Number.isFinite(data[3]) && Number.isFinite(data[5]), "real+fresh kept");
  assert.ok(Number.isNaN(data[1]) && Number.isNaN(data[2]) && Number.isNaN(data[4]), "gap/stale/unknown blanked");
});

test("blankUnverifiedCells skips the age gate when no age sidecar or budget", async () => {
  const { blankUnverifiedCells } = await import("../src/lib/loaders/scalarPng.js");
  const data = new Float32Array([0.3, 0.4]);
  const source = new Uint8Array([2, 6]);
  // No age array → only the gap-fill gate runs; the real cell survives.
  const res = blankUnverifiedCells(data, { source, gapFillCodes: new Set([4, 5, 6]) });
  assert.equal(res.blankedStale, 0);
  assert.ok(Number.isFinite(data[0]) && Number.isNaN(data[1]));
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
