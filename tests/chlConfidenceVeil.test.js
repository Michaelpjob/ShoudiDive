// Confidence veil for the chl layer: gap-filled/aging cells keep their value
// but paint faded (opacity = confidence) instead of being NaN-blanked into a
// boxy checkerboard. Verifies the per-cell confidence math.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  computeConfidence, GAP_FILL_CONFIDENCE, UNKNOWN_AGE_CONFIDENCE,
} from "../src/lib/loaders/scalarPng.js";

const GAP = new Set([4, 5, 6]);

test("fresh verified observation → full confidence", () => {
  const data = new Float32Array([0.2, 1.5]);
  const source = new Uint8Array([1, 2]);          // AQUA, VIIRS (real)
  const age = new Uint8Array([1, 3]);             // 0d, 2d old (code-1)
  const c = computeConfidence(data, { source, age, gapFillCodes: GAP, freshDays: 3 });
  assert.equal(c[0], 1.0);
  assert.equal(c[1], 1.0);
});

test("gap-filled source → faded (0.40), value NOT blanked", () => {
  const data = new Float32Array([0.13]);
  const source = new Uint8Array([6]);             // GlobColour gap-fill
  const age = new Uint8Array([3]);                // fresh age
  const c = computeConfidence(data, { source, age, gapFillCodes: GAP, freshDays: 3 });
  assert.ok(Math.abs(c[0] - GAP_FILL_CONFIDENCE) < 1e-6);
  assert.ok(Number.isFinite(data[0]), "value is preserved, not NaN'd");
});

test("real but aging observation fades with age, never below the floor", () => {
  const data = new Float32Array([0.2, 0.2, 0.2]);
  const source = new Uint8Array([1, 1, 1]);
  const age = new Uint8Array([5, 8, 30]);         // 4d, 7d, 29d old
  const c = computeConfidence(data, { source, age, gapFillCodes: GAP, freshDays: 3 });
  assert.ok(c[0] < 1.0 && c[0] > c[1], "4d fades below fresh, above 7d");
  assert.ok(c[2] >= 0.30, "very old still >= floor (real obs never vanishes)");
});

test("unknown age (sentinel 0) → low confidence, not blank", () => {
  const data = new Float32Array([0.2]);
  const source = new Uint8Array([1]);
  const age = new Uint8Array([0]);                // no-data sentinel
  const c = computeConfidence(data, { source, age, gapFillCodes: GAP, freshDays: 3 });
  assert.ok(Math.abs(c[0] - UNKNOWN_AGE_CONFIDENCE) < 1e-6);
});

test("genuine no-data (NaN value) → zero confidence (stays transparent)", () => {
  const data = new Float32Array([NaN]);
  const source = new Uint8Array([1]);
  const age = new Uint8Array([1]);
  const c = computeConfidence(data, { source, age, gapFillCodes: GAP, freshDays: 3 });
  assert.equal(c[0], 0);
});
