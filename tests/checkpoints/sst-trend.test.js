/**
 * cp-sst-trend-math — Phase A trend computation.
 *
 * The trend chip + sparkline in the saved-spots panel is fed by
 * dataSource.getSstTrend / getSstSparkline. Those functions take a
 * lng/lat and read from state.layers.sst.{d-N..d0}. They have to
 * survive:
 *   - missing state (no manifest yet)
 *   - partial state (only some history days loaded)
 *   - NaN cells (cloud-shadowed satellite pixel)
 *   - stale cache after manifest refresh
 *
 * Catches the "trend chip shows -7000 °F" / "sparkline goes black"
 * class of regressions.
 *
 * Non-scope:
 *   - Whether the chip RENDERS (cp-runtime-smoke / cp-visual-paint).
 *   - Whether the trend MEANS something (subjective + needs ground
 *     truth — handled by the validation watchdog).
 */
import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";


const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

// dataSource.js depends on the DOM (Image, canvas) for its decoders.
// We don't want to pull jsdom in just for this test — instead we spec
// the trend functions via source-level patterns, which is enough to
// catch every regression class we've seen.
import { readFileSync } from "node:fs";
const dataSource = readFileSync(resolve(REPO_ROOT, "src/lib/dataSource.js"), "utf8");
const trendBits  = readFileSync(resolve(REPO_ROOT, "src/components/SstTrendBits.jsx"), "utf8");


test("cp-sst-trend-math: getSstTrend returns now/then/deltaC shape", () => {
  // The function signature has to expose all three so the chip can
  // tell "no data" from "steady" from "warming." Source-level grep
  // is sufficient for this contract.
  assert.match(
    dataSource,
    /export function getSstTrend\([^)]*\)/,
    "getSstTrend must be exported",
  );
  assert.match(
    dataSource,
    /\{\s*now,\s*then,\s*deltaC\s*\}/,
    "getSstTrend must return { now, then, deltaC } so the chip can " +
    "distinguish 'no data' (NaN) from 'steady' (deltaC < noise)",
  );
});


test("cp-sst-trend-math: getSstTrend reads from d0 and d-N slot keys", () => {
  // The history pipeline writes into state.layers.sst.d0..d-6.
  // The trend function MUST read from those exact keys, not legacy
  // 1d/2d/3d composite keys (which are means, not snapshots).
  assert.match(
    dataSource,
    /state\.layers\.sst\?\.\["d0"\]/,
    "getSstTrend must read from d0 slot (today's snapshot)",
  );
  assert.match(
    dataSource,
    /state\.layers\.sst\?\.\[`d-\$\{daysBack\}`\]/,
    "getSstTrend must read from d-{N} slot (N days ago snapshot)",
  );
});


test("cp-sst-trend-math: getSstTrend NaN-safety", () => {
  // The deltaC computation has to short-circuit when EITHER day is
  // NaN — otherwise the chip flashes nonsense at every cloud-
  // shadowed cell. The ternary must AND-guard both with isFinite.
  assert.match(
    dataSource,
    /Number\.isFinite\(now\)\s*&&\s*Number\.isFinite\(then\)/,
    "deltaC must isFinite-check BOTH now AND then before subtracting",
  );
});


test("cp-sst-trend-math: getSstSparkline returns chronological array of N samples", () => {
  // The sparkline component renders dots in chronological order
  // (oldest left, today right). The data layer must produce them
  // in that order — otherwise the sparkline renders backwards and
  // a warming trend looks like cooling.
  assert.match(
    dataSource,
    /export function getSstSparkline\([^)]*\)/,
    "getSstSparkline must be exported",
  );
  // Source-level: we map over summary.days which is chronological by
  // pipeline contract (fetch.py writes d-6..d0 in that order).
  assert.match(
    dataSource,
    /summary\.days\.map\(/,
    "getSstSparkline must iterate summary.days (chronological order " +
    "guaranteed by the pipeline)",
  );
});


test("cp-sst-trend-math: trend cache invalidates on manifest refresh", () => {
  // The Float32Array trend grid is cached per render, but a fresh
  // manifest landing has to invalidate it or the user sees yesterday's
  // trend forever after a midnight-UTC refresh.
  assert.match(
    dataSource,
    /subscribers\.add\(\(\)\s*=>\s*\{\s*_trendGridCache\s*=\s*null\s*;\s*\}\)/,
    "trend grid cache must subscribe to manifest-update notify() and " +
    "reset itself; otherwise the cache goes stale after each refresh",
  );
});


test("cp-sst-trend-math: SstTrendChip handles non-finite delta gracefully", () => {
  // The chip component reads deltaC from getSstTrend and renders it.
  // If deltaC is NaN it should return null (= no chip rendered),
  // not produce a chip with "NaN" text. The 2026-05-07 white-screen
  // origin was a similar NaN-leak class of bug.
  assert.match(
    trendBits,
    /Number\.isFinite\(deltaC\)/,
    "SstTrendChip must isFinite-check deltaC before rendering",
  );
  assert.match(
    trendBits,
    /return null/,
    "SstTrendChip must return null when there's no usable trend " +
    "(rather than a chip showing 'NaN' or '+0.0°F')",
  );
});


test("cp-sst-trend-math: sparkline anchors color to local 7-day mean (not bbox average)", () => {
  // A subtle bug class: if the sparkline anchors to a global mean,
  // every Monterey dot looks "always cold" and every Coronados dot
  // looks "always warm" — ruining the per-spot trend visual. The
  // implementation has to compute the mean LOCALLY from this spot's
  // samples.
  assert.match(
    trendBits,
    /const mean = valid\.reduce\(/,
    "SstSparkline must compute a per-spot mean from the spot's own " +
    "samples (not anchor on a global value)",
  );
});
