// Unit tests for the temperature-break line tracer (src/lib/sstBreaks.js).
//
// v2 traces FRONTS, not patches: gradient crest thinning (non-maximum
// suppression), hysteresis continuation, and a minimum end-to-end span.
// These tests pin the product contract from user feedback (2026-08-12):
// a break is a long line that runs for miles — the edge of a warm tongue
// between Catalina and San Clemente reaching to the coast — never a
// local dark spot. And the honesty contract: no-data cells are never
// marked, land-sea edges produce nothing, degraded input is refused.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import {
  computeBreakMask,
  BREAK_THRESHOLD_C_PER_KM,
  BREAK_THRESHOLD_LOW_C_PER_KM,
  BREAK_MIN_SPAN_KM,
  BREAK_STRONG_C_PER_KM,
} from "../src/lib/sstBreaks.js";

// 100x100 grid over a ~1° box near 33N: ~1.1 km/px both axes. Same object
// shape as mapData.js's exported BBOX (the real caller's argument).
const BBOX = { latMin: 32.5, latMax: 33.5, lngMin: -118.0, lngMax: -117.0 };
const W = 100, H = 100;

function grid(fill) {
  const data = new Float32Array(W * H);
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) data[y * W + x] = fill(x, y);
  return { data, width: W, height: H };
}

// Smooth thermal step centred at x=c: tanh ramp `width` px wide, `amp`
// degC of total contrast. Gives a controllable peak gradient without the
// binomial smoothing changing the answer much.
const step = (x, c, width, amp) => amp * 0.5 * (1 + Math.tanh((x - c) / width));

test("uniform field has zero breaks", () => {
  const res = computeBreakMask(grid(() => 18.0), BBOX);
  assert.ok(res, "uniform field should still compute");
  assert.equal(res.breakPx, 0);
  assert.equal(res.fronts.length, 0);
});

test("a long thermal step traces as ONE thin front spanning the grid", () => {
  const res = computeBreakMask(grid((x) => 16 + step(x, 50, 2, 2)), BBOX);
  assert.equal(res.fronts.length, 1, "one physical edge = one front");
  assert.ok(res.fronts[0].spanKm >= 100, `should span the grid, got ${res.fronts[0].spanKm} km`);
  // Thin: crest suppression should keep it ~1-2 px wide, so total pixels
  // stay close to the grid height, nowhere near a band.
  assert.ok(res.breakPx <= 3 * H, `expected a thin line, got ${res.breakPx} px`);
  const cols = new Set();
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++)
      if (res.mask[y * W + x]) cols.add(x);
  for (const c of cols) assert.ok(Math.abs(c - 50) <= 3, `crest at column ${c}, expected near 50`);
});

test("a locally-steep SPOT is rejected — breaks must run for miles", () => {
  // Hot bullseye ~6 px across: its edge ring is steep (>= threshold) but
  // the whole structure spans well under MIN_SPAN_KM. This is exactly the
  // "dark spots" complaint — it must not survive.
  const res = computeBreakMask(
    grid((x, y) => 18 + 2 * Math.exp(-((x - 50) ** 2 + (y - 50) ** 2) / 8)),
    BBOX
  );
  assert.equal(res.breakPx, 0, `local bump leaked ${res.breakPx} px through the span filter`);
});

test("hysteresis carries a front through its faded middle", () => {
  // One edge, strong at the ends, SMOOTHLY fading toward the middle (a
  // sharp amplitude jump would itself be a thermal edge — real fronts
  // weaken gradually). At y=50 the contrast bottoms out at 0.20 degC over
  // ~2 px ≈ 0.045 degC/km: BELOW the 0.06 seed threshold but above the
  // 0.035 continuation floor. A real warm-tongue boundary does exactly
  // this; it must stay ONE line, not two fragments with a gap.
  // sigma 20 keeps the fade gentle enough that the gradient DIRECTION
  // stays across-the-front — a steeper along-front fade rotates the
  // direction and fragments NMS, which is fixture geometry, not physics.
  const amp = (y) => 2.0 - 1.78 * Math.exp(-((y - 50) ** 2) / (2 * 20 ** 2));
  const res = computeBreakMask(grid((x, y) => 16 + step(x, 50, 2, amp(y))), BBOX);
  assert.equal(res.fronts.length, 1, `expected one connected front, got ${res.fronts.length}`);
  const midRows = new Set();
  for (let y = 44; y < 57; y++)
    for (let x = 0; x < W; x++)
      if (res.mask[y * W + x]) midRows.add(y);
  assert.ok(midRows.size >= 10, `faded middle missing: only ${midRows.size}/13 mid rows marked`);
  // And the faded crest really is sub-seed there — otherwise this test
  // wouldn't be exercising hysteresis at all.
  assert.ok(amp(50) / (4 * 1.11) < BREAK_THRESHOLD_C_PER_KM,
    "fixture no longer dips below the seed threshold");
});

test("a weak-everywhere edge never seeds a front", () => {
  // Entire edge sits between the low and high thresholds: hysteresis may
  // not bootstrap a line that nothing strong anchors.
  const res = computeBreakMask(grid((x) => 16 + step(x, 50, 2, 0.2)), BBOX);
  assert.equal(res.breakPx, 0);
});

test("a gentle basin-scale ramp stays silent", () => {
  const res = computeBreakMask(grid((x) => 16 + x / 100), BBOX);
  assert.equal(res.breakPx, 0);
});

test("no-data cells are never marked, even beside a strong front", () => {
  const res = computeBreakMask(
    grid((x, y) => (y < 10 ? NaN : 16 + step(x, 50, 2, 2))),
    BBOX
  );
  for (let y = 0; y < 10; y++)
    for (let x = 0; x < W; x++)
      assert.equal(res.mask[y * W + x], 0, `NaN cell (${x},${y}) marked`);
});

test("land-sea edges do not read as breaks", () => {
  const res = computeBreakMask(grid((x) => (x < 30 ? NaN : 18.0)), BBOX);
  assert.ok(res, "coastal field should compute");
  assert.equal(res.breakPx, 0, "uniform water beside land must have no breaks");
});

test("refuses fallback-source input entirely", () => {
  const res = computeBreakMask(grid((x) => 16 + step(x, 50, 2, 2)), BBOX, {
    sourceFallback: true,
  });
  assert.equal(res, null);
});

test("refuses grids too coarse to mean anything", () => {
  const w = 40, h = 40;
  const data = new Float32Array(w * h).fill(18);
  assert.equal(computeBreakMask({ data, width: w, height: h }, BBOX), null);
});

test("array-style bbox is rejected, not misread", () => {
  // The first live render crashed on exactly this: an array bbox
  // destructured as an object yields undefined bounds. Must return null,
  // never throw or silently compute garbage km-per-pixel.
  const g = grid((x) => 16 + step(x, 50, 2, 2));
  assert.equal(computeBreakMask(g, [-118, 32.5, -117, 33.5]), null);
  assert.equal(computeBreakMask(g, null), null);
});

test("thresholds and span are the exported knobs and stay registered", () => {
  // Recalibrated 2026-08-12 (San Nicolas/Tanner miss): MUR smears real
  // fronts into the 0.05-0.13 band, so 0.1 only caught knife-edges.
  assert.equal(BREAK_THRESHOLD_C_PER_KM, 0.06);
  assert.equal(BREAK_THRESHOLD_LOW_C_PER_KM, 0.035);
  assert.equal(BREAK_MIN_SPAN_KM, 30);
  assert.equal(BREAK_STRONG_C_PER_KM, 0.1);
  // S5: numbers that decide what users see must be in the registry.
  const reg = JSON.parse(
    readFileSync(new URL("../pipeline/validation/knobs_registry.json", import.meta.url), "utf-8")
  );
  const knob = (reg.knobs || []).find((k) => k.name === "sst_breaks.front_tracing");
  assert.ok(knob, "sst_breaks.front_tracing missing from knobs_registry.json");
  assert.ok(["provisional", "fit"].includes(knob.status));
});

test("a broad MUR-smeared front is captured (San Nicolas regression)", () => {
  // The warm-pool west wall, 2026-08-09: ~1.8 degC spread over a ~25 km
  // transition. Peak local gradient ~0.068 degC/km — invisible at the old
  // 0.1 seed, real and obvious to any eye on the map. tanh width 12 px
  // (~13 km half-width) with 1.8 degC amplitude reproduces it.
  const g = grid((x) => 16 + step(x, 50, 12, 1.8));
  const res = computeBreakMask(g, BBOX);
  assert.equal(res.fronts.length, 1, "the smeared wall must trace");
  assert.ok(res.fronts[0].spanKm >= 100, "and span the grid");
  assert.ok(res.fronts[0].maxGradient < BREAK_STRONG_C_PER_KM,
    "fixture should be a soft front (below the strong bar)");
  // And the old calibration really did miss it — the regression guard.
  const old = computeBreakMask(g, BBOX, { threshold: 0.1, thresholdLow: 0.05 });
  assert.equal(old.fronts.length, 0, "old 0.1 seed should miss the smeared wall");
});

test("fronts report their peak gradient for strength grading", () => {
  const res = computeBreakMask(grid((x) => 16 + step(x, 50, 2, 2)), BBOX);
  assert.ok(res.fronts[0].maxGradient >= BREAK_STRONG_C_PER_KM,
    "a 2 degC knife-edge should grade strong");
});

test("input grid is never mutated", () => {
  const g = grid((x) => 16 + step(x, 50, 2, 2));
  const before = Array.from(g.data.slice(0, 200));
  computeBreakMask(g, BBOX);
  assert.deepEqual(Array.from(g.data.slice(0, 200)), before);
});

test("each front carries an ordered main-stem polyline", () => {
  const res = computeBreakMask(grid((x) => 16 + step(x, 50, 2, 2)), BBOX);
  const pts = res.fronts[0].points;
  assert.ok(Array.isArray(pts) && pts.length >= 2, "polyline missing");
  // Ends of the stem sit at the top and bottom of the grid (the front
  // runs the full height), and every point hugs the crest column.
  const ys = pts.map((p) => p[1]);
  assert.ok(Math.min(...ys) <= 3 && Math.max(...ys) >= H - 4,
    `stem should span the grid, got rows ${Math.min(...ys)}..${Math.max(...ys)}`);
  for (const [x] of pts) assert.ok(Math.abs(x - 50) <= 3, `stem point at column ${x}`);
  // Ordered: monotonic along the line, not a scrambled pixel bag.
  const dirs = new Set();
  for (let i = 1; i < ys.length; i++) dirs.add(Math.sign(ys[i] - ys[i - 1]));
  dirs.delete(0);
  assert.equal(dirs.size, 1, "polyline rows should progress in one direction");
  // Simplified: a straight line must not carry hundreds of points.
  assert.ok(pts.length <= 12, `expected a simplified stem, got ${pts.length} points`);
});
