// The Ocean Report's break lines come from a copy of the main map's tracer
// (public/reports/breaks.js, made by scripts/vendor-report-breaks.py). This
// pins the copy to src/lib/sstBreaks.js + breaksGps.js: same constants, same
// fronts on the same grid. If it fails, re-run the vendor script.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import * as src from "../src/lib/sstBreaks.js";
import * as gps from "../src/lib/breaksGps.js";

const code = readFileSync(new URL("../public/reports/breaks.js", import.meta.url), "utf8");
const ctx = { window: {} };
vm.runInNewContext(code, ctx);
const V = ctx.window.SDBreaks;

function grid() {
  // 120 x 100 cells over a SoCal-sized box: a warm tongue with a sharp west wall.
  const w = 120, h = 100, data = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const wall = 60 + 10 * Math.sin(y / 12);
    data[y * w + x] = x > wall ? 22 : 18 + 4 * Math.exp(-((wall - x) ** 2) / 8);
    if (x < 3 && y < 3) data[y * w + x] = NaN;
  }
  return { data, width: w, height: h };
}
const BBOX = { latMin: 31.8, latMax: 34.0, lngMin: -120.0, lngMax: -117.0 };

test("the report's break tracer is the main map's", () => {
  assert.equal(V.BREAK_STRONG_C_PER_KM, src.BREAK_STRONG_C_PER_KM);
  const a = src.computeBreakMask(grid(), BBOX), b = V.computeBreakMask(grid(), BBOX);
  assert.ok(a.fronts.length > 0);
  assert.deepEqual(JSON.parse(JSON.stringify(b.fronts)), JSON.parse(JSON.stringify(a.fronts)));
  const plain = (v) => JSON.parse(JSON.stringify(v));   // objects from the vm context have its own prototypes
  assert.deepEqual(plain(V.formatWaypoint([-117.5, 33.25])), plain(gps.formatWaypoint([-117.5, 33.25])));
  const pts = [[0, 0], [1, 1], [2, 2], [3, 3], [4, 4], [5, 5], [6, 6]];
  assert.deepEqual(plain(V.pickWaypoints(pts)), plain(gps.pickWaypoints(pts)));
});

test("the copy refuses the coarse fallback grid like the original", () => {
  assert.equal(V.computeBreakMask(grid(), BBOX, { sourceFallback: true }), null);
});
