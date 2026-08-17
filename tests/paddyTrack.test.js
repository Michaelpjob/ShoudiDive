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
  assert.equal(c.LEEWAY_ALPHA, 0.02, "combined wind response (windage + Stokes)");
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

// ---- forcing plumbing ---------------------------------------------------
// These pin the two bugs that silently starved the drift of its ocean
// model: region-prefixed URLs that hit the SPA fallback (200 + HTML, so
// the <img> decode fails without throwing), and a compact-ISO init_cycle
// that Date.parse rejects.

test("region-prefixed data URLs are rewritten to the served path", () => {
  assert.equal(PT.fixUrl("/data/ca/rtofs/uv_d1.png"), "/data/rtofs/uv_d1.png");
  assert.equal(PT.fixUrl("/data/baja/rtofs/uv_d3.png"), "/data/rtofs/uv_d3.png");
  assert.equal(PT.fixUrl("/data/currents/buckets/d1_midday_uv.png"),
    "/data/currents/buckets/d1_midday_uv.png", "already-correct paths untouched");
});

test("compact-ISO init_cycle parses (Date.parse alone returns NaN)", () => {
  assert.ok(Number.isNaN(Date.parse("20260816T00:00:00Z")), "premise: plain parse fails");
  const t = PT.parseCycle("20260816T00:00:00Z");
  assert.ok(Number.isFinite(t), "should parse");
  assert.equal(new Date(t).toISOString(), "2026-08-16T00:00:00.000Z");
  assert.ok(Number.isFinite(PT.parseCycle("2026-08-16T00:00:00Z")), "normal ISO still works");
  assert.ok(Number.isNaN(PT.parseCycle("")), "junk stays NaN");
});

test("drift stops at the end of the current forecast, never on a frozen field", () => {
  // Forcing that only covers 48 h. Asking for a week must truncate rather
  // than clamp to the last field and coast on a stale current.
  const g = { u: new Float32Array(60 * 60).fill(0.4), v: new Float32Array(60 * 60).fill(0), w: 60, h: 60 };
  const F = {
    bbox: BBOX, currentHorizonH: 48,
    rtofs: [{ t: 0, g, k: 1 }, { t: 48, g, k: 1 }],
    surface: [], wind: [],
  };
  const tr = PT.integrate(F, -119.0, 33.0, 168, false, 1);
  const lastT = tr[tr.length - 1].t;
  assert.ok(lastT <= 56, `should stop near the 48 h horizon, ran to ${lastT} h`);
  assert.ok(lastT >= 40, `should still use the forcing it has, only reached ${lastT} h`);
});

test("the lane is only claimed while one actually exists", () => {
  const f = PT.forecast(forcing({ cu: 0.3 }), -119.0, 33.0, 168);
  assert.ok(Number.isFinite(f.laneEndH), "forecast must publish a lane horizon");
  // Every step inside the lane is genuinely narrow...
  for (const s of f.steps.filter((x) => x.t <= f.laneEndH)) {
    assert.ok(s.crossKm <= PT.consts.LANE_MAX_CROSS_KM,
      `t=${s.t}h inside lane but ±${s.crossKm.toFixed(1)} km wide`);
  }
  // ...and the first step past it is not.
  const after = f.steps.find((x) => x.t > f.laneEndH);
  if (after) {
    assert.ok(after.crossKm > PT.consts.LANE_MAX_CROSS_KM,
      "lane horizon should end exactly where the spread outgrows it");
  }
});

test("steps carry the real member positions, not just a summary", () => {
  // Drawing the ensemble instead of a geometric ribbon is what stops the
  // display painting a corridor across land the model never entered.
  const f = PT.forecast(forcing({ cu: 0.3 }), -119.0, 33.0, 72);
  const s = f.steps.find((x) => x.t === 48);
  assert.ok(Array.isArray(s.cloud) && s.cloud.length > 50,
    `expected a member cloud, got ${s.cloud && s.cloud.length}`);
  for (const [lng, lat] of s.cloud) {
    assert.ok(Number.isFinite(lng) && Number.isFinite(lat), "member positions must be real");
  }
  // The cloud must bracket the reported centre.
  const lats = s.cloud.map((c) => c[1]);
  assert.ok(Math.min(...lats) <= s.lat && Math.max(...lats) >= s.lat);
});

test("members that run out of forcing drop out of the cloud", () => {
  const g = { u: new Float32Array(60 * 60).fill(0.5), v: new Float32Array(60 * 60).fill(0), w: 60, h: 60 };
  const F = { bbox: BBOX, currentHorizonH: 48, rtofs: [{ t: 0, g, k: 1 }, { t: 48, g, k: 1 }], surface: [], wind: [] };
  const f = PT.forecast(F, -119.0, 33.0, 168);
  const last = f.steps[f.steps.length - 1];
  assert.ok(last.t <= 56, "forecast should not outlive its forcing");
  assert.ok(last.afloat <= 1 && last.afloat > 0, "afloat fraction should be reported");
});

test("leeway is the COMBINED wind term — no separate Stokes, no double count", () => {
  // Literature splits surface wind response into windage (~1% for
  // macroalgae rafts) and Stokes (~1-1.5% of wind, predominantly
  // downwind). Models use one OR the other, never both. This engine uses
  // the implicit-leeway form: a single 2% coefficient standing for both.
  assert.equal(PT.consts.LEEWAY_ALPHA, 0.02);
  assert.equal(PT.consts.STOKES_COEF, undefined,
    "an explicit Stokes term would double-count the downwind response");
  // 10 m/s of wind, no current -> 0.2 m/s -> 17.28 km/day, nothing more.
  const tr = PT.integrate(forcing({ wu: 10 }), -119.0, 33.0, 24, false, 1);
  const km = PT.haversineKm(33.0, -119.0, tr[tr.length - 1].lat, tr[tr.length - 1].lng);
  assert.ok(Math.abs(km - 17.28) < 0.6, `expected ~17.3 km, got ${km.toFixed(1)}`);
});

test("survival fraction is domain-tracking, not flotation", () => {
  // Sinking is not modelled. A run leaves the ensemble by exiting the
  // grid or hitting land — calling that "afloat" would overclaim.
  const f = PT.forecast(forcing({ cu: 0.3 }), -119.0, 33.0, 72);
  const s = f.steps.find((x) => x.t === 48);
  assert.ok(s.inDomain > 0 && s.inDomain <= 1, "inDomain fraction reported");
});
