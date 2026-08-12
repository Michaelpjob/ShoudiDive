// Unit tests for the temperature-break mask (src/lib/sstBreaks.js).
//
// The break outline derives client-side from the SST grid the map already
// shows. These tests pin the honesty contract: breaks appear exactly where
// a real gradient crosses the threshold, never on no-data cells, and the
// whole computation refuses degraded input (fallback source, tiny grids)
// rather than drawing confident lines from mush.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { computeBreakMask, BREAK_THRESHOLD_C_PER_KM } from "../src/lib/sstBreaks.js";

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

test("uniform field has zero breaks", () => {
  const res = computeBreakMask(grid(() => 18.0), BBOX);
  assert.ok(res, "uniform field should still compute");
  assert.equal(res.breakPx, 0);
});

test("a sharp thermal step is outlined along the step, nowhere else", () => {
  // 2 degC step at x=50 → gradient ~2/(2*1.1) ≈ 0.9 degC/km at the edge,
  // far above threshold; far field is flat.
  const res = computeBreakMask(grid((x) => (x < 50 ? 16 : 18)), BBOX);
  assert.ok(res.breakPx > 0, "step should produce break pixels");
  const cols = new Set();
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++)
      if (res.mask[y * W + x]) cols.add(x);
  for (const c of cols) {
    assert.ok(Math.abs(c - 50) <= 3, `break at column ${c}, expected near 50`);
  }
});

test("a gentle basin-scale ramp stays below threshold", () => {
  // 1 degC across the full 100 px (~110 km) = 0.009 degC/km — an order of
  // magnitude under the threshold. Normal SST structure must NOT light up.
  const res = computeBreakMask(grid((x) => 16 + x / 100), BBOX);
  assert.equal(res.breakPx, 0);
});

test("no-data cells are never marked, even beside a strong front", () => {
  const res = computeBreakMask(
    grid((x, y) => (y < 10 ? NaN : x < 50 ? 16 : 18)),
    BBOX
  );
  for (let y = 0; y < 10; y++)
    for (let x = 0; x < W; x++)
      assert.equal(res.mask[y * W + x], 0, `NaN cell (${x},${y}) marked`);
});

test("land-sea edges do not read as breaks", () => {
  // Coast: NaN land against 18 degC water, uniform temperature. The
  // NaN-aware smoothing must not fabricate a gradient at the coastline —
  // that would outline every beach as a permanent 'break'.
  const res = computeBreakMask(
    grid((x) => (x < 30 ? NaN : 18.0)),
    BBOX
  );
  assert.ok(res, "coastal field should compute");
  assert.equal(res.breakPx, 0, "uniform water beside land must have no breaks");
});

test("refuses fallback-source input entirely", () => {
  const res = computeBreakMask(grid((x) => (x < 50 ? 16 : 18)), BBOX, {
    sourceFallback: true,
  });
  assert.equal(res, null);
});

test("refuses grids too coarse to mean anything", () => {
  const w = 40, h = 40;
  const data = new Float32Array(w * h).fill(18);
  assert.equal(computeBreakMask({ data, width: w, height: h }, BBOX), null);
});

test("threshold is the exported knob and stays registered", () => {
  assert.equal(BREAK_THRESHOLD_C_PER_KM, 0.1);
  // S5: the number that decides what users see must be in the registry.
  const reg = JSON.parse(
    readFileSync(new URL("../pipeline/validation/knobs_registry.json", import.meta.url), "utf-8")
  );
  const knob = (reg.knobs || []).find((k) => k.name === "sst_breaks.threshold");
  assert.ok(knob, "sst_breaks.threshold missing from knobs_registry.json");
  assert.ok(["provisional", "fit"].includes(knob.status));
});

test("array-style bbox is rejected, not misread", () => {
  // The first live render crashed on exactly this: an array bbox
  // destructured as an object yields undefined bounds. Must return null,
  // never throw or silently compute garbage km-per-pixel.
  const g = grid((x) => (x < 50 ? 16 : 18));
  assert.equal(computeBreakMask(g, [-118, 32.5, -117, 33.5]), null);
  assert.equal(computeBreakMask(g, null), null);
});

test("input grid is never mutated", () => {
  const g = grid((x) => (x < 50 ? 16 : 18));
  const before = Array.from(g.data.slice(0, 200));
  computeBreakMask(g, BBOX);
  assert.deepEqual(Array.from(g.data.slice(0, 200)), before);
});
