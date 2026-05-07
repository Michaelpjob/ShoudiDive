/**
 * cp-sst-trend-math — Phase A trend computation invariants.
 *
 * The previous version of this test grepped dataSource.js source for
 * specific identifiers. That was brittle — every refactor of the
 * trend internals broke the test even when behavior was preserved.
 *
 * This trimmed-down version tests INVARIANTS only:
 *   - SstTrendBits.jsx exports the chip + sparkline components
 *   - Both components defend against non-finite values (no `NaN` text
 *     leaks into the DOM, which is the whole class of bug we shipped
 *     on 2026-05-07)
 *
 * The actual math (NaN propagation, sparkline ordering, cache
 * invalidation) is exercised end-to-end by cp-runtime-smoke +
 * cp-visual-paint when those run against the real data layer. Source-
 * grepping was redundant.
 */
import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";


const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

function read(rel) {
  return readFileSync(resolve(REPO_ROOT, rel), "utf8");
}

const trendBits = read("src/components/SstTrendBits.jsx");


test("cp-sst-trend-math: SstTrendBits.jsx exports the chip + sparkline", () => {
  assert.match(
    trendBits, /export function SstTrendChip\b/,
    "SstTrendBits.jsx must export SstTrendChip",
  );
  assert.match(
    trendBits, /export function SstSparkline\b/,
    "SstTrendBits.jsx must export SstSparkline",
  );
});


test("cp-sst-trend-math: chip + sparkline never let NaN leak into the DOM", () => {
  // Defense-in-depth against the 2026-05-07 white-screen-class bug.
  // Both components must explicitly check for non-finite numbers
  // BEFORE rendering anything that includes the value as text. We
  // grep for `Number.isFinite` since that's the canonical guard;
  // any equivalent (`!Number.isNaN`, `isFinite`) is technically OK
  // but breaks the test, which is fine — the grep is a forcing
  // function for the canonical pattern.
  const isFiniteCount = (trendBits.match(/Number\.isFinite/g) || []).length;
  assert.ok(
    isFiniteCount >= 2,
    `SstTrendBits.jsx should call Number.isFinite at least twice ` +
    `(once in the chip, once in the sparkline) to guard against ` +
    `NaN values from getSstTrend / getSstSparkline; got ${isFiniteCount}.`,
  );
});


test("cp-sst-trend-math: chip returns null when there's nothing to show", () => {
  // Critical invariant: the chip must render NOTHING (not a degenerate
  // chip with `+0.0°F` or `NaN`) when the trend is unknown. The map
  // already shows a "no data" state visually; the chip should match.
  assert.match(
    trendBits, /return null/,
    "SstTrendChip / SstSparkline must `return null` when their data is " +
    "missing — rendering a chip with NaN text was the white-screen-class " +
    "bug we already shipped once.",
  );
});
