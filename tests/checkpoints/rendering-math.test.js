/**
 * cp-rendering-math — pure functions in the render pipeline.
 *
 * Catches regressions in:
 *   - colormap stops (missing entries, wrong order)
 *   - sstTrendColor (Phase A diverging palette) — saturation +
 *     near-zero handling
 *   - project()/unproject() round-trip on the bbox
 *
 * Non-scope:
 *   - Whether the canvas actually paints (cp-runtime-smoke,
 *     cp-visual-paint).
 *   - Whether the colormap LOOKS right (subjective).
 */
import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

// Dynamically import mapData.js so we test the actual exported helpers.
// Vite + ES modules — no transpilation needed at the node:test level.
const mapDataPath = resolve(REPO_ROOT, "src/lib/mapData.js");
const mapData = await import(mapDataPath);
const {
  BBOX,
  SST_RANGE,
  SST_STOPS,
  SST_TREND_RANGE_C,
  SST_TREND_STOPS,
  sstTrendColor,
  project,
  unproject,
} = mapData;


// ---------------------------------------------------------------------
// Bbox + projection
// ---------------------------------------------------------------------

test("cp-rendering-math: BBOX is sane (lat/lng ordered, CA coast)", () => {
  assert.equal(BBOX.latMin < BBOX.latMax, true,
    `BBOX latMin must be < latMax (got ${BBOX.latMin} >= ${BBOX.latMax})`);
  assert.equal(BBOX.lngMin < BBOX.lngMax, true,
    `BBOX lngMin must be < lngMax (got ${BBOX.lngMin} >= ${BBOX.lngMax})`);
  // Sanity: should cover CA coast roughly 31.8..37.6 N, -124..-116.8 W.
  // Anything wildly outside this range = the bbox got corrupted during
  // editing.
  assert.equal(
    BBOX.latMin > 30 && BBOX.latMax < 40,
    true, `BBOX latitude range looks wrong: ${BBOX.latMin}..${BBOX.latMax}`,
  );
  assert.equal(
    BBOX.lngMin > -130 && BBOX.lngMax < -110,
    true, `BBOX longitude range looks wrong: ${BBOX.lngMin}..${BBOX.lngMax}`,
  );
});


test("cp-rendering-math: project + unproject round-trip", () => {
  // Build a fake 1000x600 viewport. project() should land each input
  // pair back at itself within float-precision tolerance.
  const W = 1000, H = 600;
  const samples = [
    { lng: -120, lat: 35 },           // bbox center
    { lng: BBOX.lngMin, lat: BBOX.latMax },  // top-left corner
    { lng: BBOX.lngMax, lat: BBOX.latMin },  // bottom-right corner
    { lng: -118.5, lat: 33.4 },       // Catalina Island
    { lng: -117.28, lat: 32.85 },     // La Jolla
  ];
  for (const { lng, lat } of samples) {
    const [x, y] = project(lng, lat, W, H);
    const [lng2, lat2] = unproject(x, y, W, H);
    assert.ok(
      Math.abs(lng - lng2) < 1e-9,
      `lng round-trip diverged for (${lng}, ${lat}): got ${lng2}`,
    );
    assert.ok(
      Math.abs(lat - lat2) < 1e-9,
      `lat round-trip diverged for (${lng}, ${lat}): got ${lat2}`,
    );
  }
});


// ---------------------------------------------------------------------
// SST primary palette — must keep the exact stops the React + RN
// clients depend on. Adding a new stop is fine; reordering or
// removing one will break the color ramp.
// ---------------------------------------------------------------------

test("cp-rendering-math: SST primary palette is consistent (range + stops)", () => {
  assert.equal(Array.isArray(SST_RANGE) && SST_RANGE.length === 2, true,
    "SST_RANGE must be [min, max]");
  assert.equal(SST_RANGE[0] < SST_RANGE[1], true,
    `SST_RANGE must have min < max; got ${SST_RANGE.join("..")}`);
  assert.equal(SST_STOPS.length >= 2, true,
    "SST_STOPS must have at least 2 stops to make a gradient");
  // Stops must be in ascending t order (the colormap interpolator
  // assumes this).
  for (let i = 1; i < SST_STOPS.length; i++) {
    assert.equal(
      SST_STOPS[i].t > SST_STOPS[i - 1].t, true,
      `SST_STOPS not in ascending t order at index ${i}: ` +
      `${SST_STOPS[i - 1].t} >= ${SST_STOPS[i].t}`,
    );
  }
  // Must span 0..1 inclusive — anything else and the SST gradient
  // truncates at the endpoints.
  assert.equal(SST_STOPS[0].t, 0,
    "SST_STOPS first t must be 0");
  assert.equal(SST_STOPS[SST_STOPS.length - 1].t, 1,
    "SST_STOPS last t must be 1");
});


// ---------------------------------------------------------------------
// SST trend (Phase A) — diverging palette, the new one we just shipped.
// ---------------------------------------------------------------------

test("cp-rendering-math: sstTrendColor saturation + neutral handling", () => {
  // Below the noise floor (±0.2 °C in the palette), the color should
  // be visually near-grey. We don't test exact values; we test that
  // the R+G+B sum at +0 is roughly equal in all three channels (= grey).
  const neutral = sstTrendColor(0);
  const m = neutral.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
  assert.ok(m, `sstTrendColor(0) should return rgb(...); got ${neutral}`);
  const [r, g, b] = m.slice(1, 4).map(Number);
  assert.ok(Math.abs(r - g) < 5 && Math.abs(g - b) < 5,
    `sstTrendColor(0) should be near-grey; got rgb(${r},${g},${b})`);

  // Saturated cooling (-2 °C) should be deeply blue: B > R.
  const cold = sstTrendColor(-2);
  const cm = cold.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
  assert.ok(cm, `sstTrendColor(-2) should return rgb(...); got ${cold}`);
  const [cr, , cb] = cm.slice(1, 4).map(Number);
  assert.ok(cb > cr + 50,
    `cooling should be blue (B >> R); got rgb(${cm[1]},${cm[2]},${cm[3]})`);

  // Saturated warming (+2 °C) should be deeply red: R > B.
  const warm = sstTrendColor(2);
  const wm = warm.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
  assert.ok(wm, `sstTrendColor(2) should return rgb(...); got ${warm}`);
  const [wr, , wb] = wm.slice(1, 4).map(Number);
  assert.ok(wr > wb + 50,
    `warming should be red (R >> B); got rgb(${wm[1]},${wm[2]},${wm[3]})`);
});


test("cp-rendering-math: sstTrendColor handles non-finite gracefully", () => {
  for (const v of [NaN, Infinity, -Infinity, undefined]) {
    const out = sstTrendColor(v);
    // Either returns transparent OR a valid rgb; never throws or returns "NaN".
    assert.equal(typeof out, "string", `sstTrendColor(${v}) must return string`);
    assert.equal(
      out.includes("NaN"), false,
      `sstTrendColor(${v}) must not produce a literal "NaN" string`,
    );
  }
});


test("cp-rendering-math: sstTrendColor saturates at the range endpoints (no overshoot)", () => {
  // Beyond ±SST_TREND_RANGE_C the palette should clamp, not extrapolate.
  const minClamp = sstTrendColor(SST_TREND_RANGE_C[0]);
  const farCold  = sstTrendColor(SST_TREND_RANGE_C[0] - 5);
  assert.equal(minClamp, farCold,
    "sstTrendColor must clamp at the cold endpoint, not extrapolate");
  const maxClamp = sstTrendColor(SST_TREND_RANGE_C[1]);
  const farWarm  = sstTrendColor(SST_TREND_RANGE_C[1] + 5);
  assert.equal(maxClamp, farWarm,
    "sstTrendColor must clamp at the warm endpoint, not extrapolate");
});


test("cp-rendering-math: SST_TREND_STOPS in ascending d order", () => {
  for (let i = 1; i < SST_TREND_STOPS.length; i++) {
    assert.equal(
      SST_TREND_STOPS[i].d > SST_TREND_STOPS[i - 1].d, true,
      `SST_TREND_STOPS not in ascending d order at index ${i}: ` +
      `${SST_TREND_STOPS[i - 1].d} >= ${SST_TREND_STOPS[i].d}`,
    );
  }
  // Centered at 0 — there must be a stop AT 0 (or symmetric endpoints).
  const ds = SST_TREND_STOPS.map((s) => s.d);
  assert.ok(
    ds.includes(0) || (ds[0] === -ds[ds.length - 1]),
    "SST_TREND_STOPS should be centered at 0 (either explicit 0 stop or symmetric endpoints)",
  );
});
