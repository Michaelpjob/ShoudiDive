// Physics tests for the paddy drift engine (public/paddies/track.js).
//
// The engine advects a user-entered GPS position forward through
// published forecast forcing. These tests drive it with SYNTHETIC
// forcing so the integrator, the windage coefficient, and the
// uncertainty growth are pinned independent of whatever the ocean is
// doing today. The physics constants must stay identical to the
// research prototype (kelp-drift-proto/config.py) or the browser and
// the offline model silently diverge.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

// public/paddies/ ships as a plain <script> bundle (no modules, no build
// step), so we evaluate it exactly as a browser would rather than
// importing it — that also proves the file stays browser-loadable.
const SRC = readFileSync(new URL("../public/paddies/track.js", import.meta.url), "utf8");
const sandbox = { Math, Date, Promise, Float32Array, isFinite, console };
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox);
const PT = sandbox.PT;

const BBOX = { lngMin: -128.5, latMin: 31.8, lngMax: -116.8, latMax: 42.0 };

// A uniform field: every cell carries the same (u, v).
function uniform(u, v, w = 60, h = 60) {
  const U = new Float32Array(w * h).fill(u);
  const V = new Float32Array(w * h).fill(v);
  return { u: U, v: V, w, h };
}
// Forcing with a constant current (m/s) and optional constant wind (m/s).
function forcing({ cu = 0, cv = 0, wu = 0, wv = 0 } = {}) {
  return {
    bbox: BBOX,
    rtofs: [{ t: 0, g: uniform(cu, cv), k: 1 }, { t: 200, g: uniform(cu, cv), k: 1 }],
    surface: [],
    wind: [{ t: 0, g: uniform(wu, wv), k: 1 }, { t: 200, g: uniform(wu, wv), k: 1 }],
  };
}

test("constants match the calibrated prototype", () => {
  const c = PT.consts;
  assert.equal(c.WINDAGE_ALPHA, 0.02, "floating kelp = 2% of wind");
  // Measured flow-frame decomposition of the current-product disagreement.
  assert.equal(c.SIGMA_ALONG_MS, 0.166);
  assert.equal(c.SIGMA_CROSS_MS, 0.039);
  assert.ok(c.SIGMA_ALONG_MS / c.SIGMA_CROSS_MS > 4, "error is along-flow dominated");
  assert.equal(c.DT_HOURS, 1.0);
  assert.equal(c.DIFFUSION_K_M2S, 5.0);
  assert.equal(c.BLEND_RTOFS + c.BLEND_SURFACE, 1.0);
});

test("a constant eastward current advects at the right speed", () => {
  // 0.5 m/s = 1.8 km/h -> 43.2 km in 24 h, due east.
  const tr = PT.integrate(forcing({ cu: 0.5 }), -119.0, 33.0, 24, false, 1);
  const end = tr[tr.length - 1];
  assert.equal(tr.length, 25, "hourly steps for 24 h + origin");
  const km = PT.haversineKm(33.0, -119.0, end.lat, end.lng);
  assert.ok(Math.abs(km - 43.2) < 1.0, `expected ~43.2 km, got ${km.toFixed(1)}`);
  assert.ok(end.lng > -119.0, "should move east");
  assert.ok(Math.abs(end.lat - 33.0) < 0.02, "should not drift north/south");
});

test("windage contributes exactly 2% of wind speed", () => {
  // 10 m/s of wind, no current -> 0.2 m/s of drift = 17.28 km/day.
  const tr = PT.integrate(forcing({ wu: 10 }), -119.0, 33.0, 24, false, 1);
  const end = tr[tr.length - 1];
  const km = PT.haversineKm(33.0, -119.0, end.lat, end.lng);
  assert.ok(Math.abs(km - 17.28) < 0.6, `expected ~17.3 km from windage, got ${km.toFixed(1)}`);
});

test("current and windage add", () => {
  const cur = PT.integrate(forcing({ cu: 0.5 }), -119, 33, 24, false, 1);
  const both = PT.integrate(forcing({ cu: 0.5, wu: 10 }), -119, 33, 24, false, 1);
  const dCur = PT.haversineKm(33, -119, cur[cur.length - 1].lat, cur[cur.length - 1].lng);
  const dBoth = PT.haversineKm(33, -119, both[both.length - 1].lat, both[both.length - 1].lng);
  assert.ok(dBoth > dCur + 15, `windage should add ~17 km, got +${(dBoth - dCur).toFixed(1)}`);
});

test("uncertainty grows with lead time and is reported per hour", () => {
  const f = PT.forecast(forcing({ cu: 0.3 }), -119.0, 33.0, 168);
  const at = (h) => f.steps.find((s) => s.t === h);
  assert.ok(f.steps.length > 100, "should produce hourly steps across the week");
  assert.equal(at(0).alongKm, 0, "no spread at the observed position");
  const d1 = at(24).alongKm, d3 = at(72).alongKm, d7 = at(168).alongKm;
  assert.ok(d1 > 3, `day-1 along spread should be real, got ${d1.toFixed(1)} km`);
  assert.ok(d3 > d1 && d7 > d3, `spread must grow: ${d1.toFixed(1)} < ${d3.toFixed(1)} < ${d7.toFixed(1)}`);
  assert.ok(d7 > 20, `day-7 along spread should be tens of km, got ${d7.toFixed(1)}`);
});

test("the spread is a CORRIDOR: along-track error dwarfs cross-track", () => {
  // A paddy runs down the flow; it does not wander back upstream. So the
  // uncertainty is "how far along", not "which way" — the shape must be a
  // lane, never a disc. (User feedback 2026-08-16, backed by the measured
  // 4.3x along/cross anisotropy.)
  const f = PT.forecast(forcing({ cu: 0.3 }), -119.0, 33.0, 168);
  for (const h of [24, 72, 168]) {
    const s = f.steps.find((x) => x.t === h);
    assert.ok(s.alongKm > 2.5 * s.crossKm,
      `day ${h / 24}: along ${s.alongKm.toFixed(1)} should dwarf cross ${s.crossKm.toFixed(1)}`);
  }
  const d7 = f.steps.find((x) => x.t === 168);
  // The lane must stay narrow enough to actually run down.
  assert.ok(d7.crossKm < 15, `day-7 lane should stay runnable, got ±${d7.crossKm.toFixed(1)} km`);
  // And a corridor must be far smaller than the circle it replaces.
  const ell = Math.PI * d7.alongKm * d7.crossKm;
  const circ = Math.PI * Math.pow(Math.max(d7.alongKm, d7.crossKm), 2);
  assert.ok(circ / ell > 4, `corridor should shrink the search a lot, got ${(circ / ell).toFixed(1)}x`);
});

test("every step carries a track bearing for orienting the corridor", () => {
  const f = PT.forecast(forcing({ cu: 0.3 }), -119.0, 33.0, 48);
  const s = f.steps.find((x) => x.t === 24);
  assert.ok(Number.isFinite(s.bearing) && s.bearing >= 0 && s.bearing < 360);
  // Pure eastward flow -> heading ~090.
  assert.ok(Math.abs(s.bearing - 90) < 25, `expected ~090 bearing, got ${s.bearing.toFixed(0)}`);
});

test("tiers grade the corridor WIDTH — what a boat actually sweeps", () => {
  assert.equal(PT.tierFor(3).key, "search");
  assert.equal(PT.tierFor(9).key, "wide");
  assert.equal(PT.tierFor(20).key, "region");
  assert.match(PT.tierFor(3).note, /[Rr]un the line/);
});

test("a particle that leaves the data truncates instead of inventing drift", () => {
  // Strong westward current runs it off the western edge of the bbox.
  const tr = PT.integrate(forcing({ cu: -2.0 }), -127.8, 33.0, 168, false, 1);
  assert.ok(tr.length < 169, "track should end when forcing runs out");
  const f = PT.forecast(forcing({ cu: -2.0 }), -127.8, 33.0, 168);
  assert.ok(f.truncated, "forecast must report truncation, not pad the week");
});

test("land / no-data cells stop the track rather than sampling through them", () => {
  const f = forcing({ cu: 0.5 });
  // Punch a NaN hole across the whole grid east of the start.
  const g = f.rtofs[0].g;
  for (let i = 0; i < g.u.length; i++) {
    if ((i % g.w) > g.w / 2) { g.u[i] = NaN; g.v[i] = NaN; }
  }
  f.rtofs[1] = f.rtofs[0];
  const tr = PT.integrate(f, -119.0, 33.0, 168, false, 1);
  assert.ok(tr.length < 169, "should stop at the no-data boundary");
});

// ---- coordinate parsing -------------------------------------------------
// Skippers type whatever their plotter shows. All of these are the same
// point off San Diego and all must land within a few metres of each other.
const near = (a, b, tol = 0.0005) => Math.abs(a - b) < tol;

test("reads split degrees/minutes/decimal-minutes: '32 56 0000 117 52 000'", () => {
  const p = PT.parseLatLng("32 56 0000 117 52 000");
  assert.ok(p, "should parse");
  assert.ok(near(p.lat, 32 + 56 / 60), `lat ${p.lat}`);
  assert.ok(near(p.lng, -(117 + 52 / 60)), `lng ${p.lng}`);
  assert.match(p.how, /decimal minutes/);
});

test("split minutes keep their fraction: '32 56 5000' = 32°56.5'", () => {
  const p = PT.parseLatLng("32 56 5000 117 52 2500");
  assert.ok(near(p.lat, 32 + 56.5 / 60), `lat ${p.lat}`);
  assert.ok(near(p.lng, -(117 + 52.25 / 60)), `lng ${p.lng}`);
});

test("the same point in every other plotter format agrees", () => {
  const ref = PT.parseLatLng("32 56 0000 117 52 000");
  for (const s of [
    "32 56.000 117 52.000",
    "32°56.000'N 117°52.000'W",
    "3256.000N 11752.000W".replace("3256.000", "32 56.000").replace("11752.000", "117 52.000"),
    "32.93333 -117.86667",
  ]) {
    const p = PT.parseLatLng(s);
    assert.ok(p, `failed to parse: ${s}`);
    assert.ok(near(p.lat, ref.lat, 0.001) && near(p.lng, ref.lng, 0.001),
      `${s} -> ${p.lat.toFixed(5)},${p.lng.toFixed(5)} != ${ref.lat.toFixed(5)},${ref.lng.toFixed(5)}`);
  }
});

test("2-digit third group is SECONDS, not decimal minutes", () => {
  // 32 56 30 -> 32°56'30" = 32.94166, NOT 32°56.30' = 32.93833
  const p = PT.parseLatLng("32 56 30 117 52 15");
  assert.ok(near(p.lat, 32 + 56 / 60 + 30 / 3600), `lat ${p.lat}`);
  assert.match(p.how, /sec/);
});

test("hemisphere honoured, and western assumed when unmarked", () => {
  assert.ok(PT.parseLatLng("32 56.000 117 52.000").lng < 0, "unmarked lng -> west");
  assert.ok(PT.parseLatLng("32 56.000 S 117 52.000 E").lat < 0, "S -> negative lat");
  assert.ok(PT.parseLatLng("32 56.000 S 117 52.000 E").lng > 0, "E -> positive lng");
  assert.ok(PT.parseLatLng("32.9333, -117.8667").lng < 0, "explicit minus preserved");
});

test("junk and impossible coordinates are refused, not guessed", () => {
  assert.equal(PT.parseLatLng(""), null);
  assert.equal(PT.parseLatLng("no numbers here"), null);
  assert.equal(PT.parseLatLng("32"), null, "one number is not a position");
  assert.equal(PT.parseLatLng("95 00.000 117 52.000"), null, "latitude past the pole");
});
