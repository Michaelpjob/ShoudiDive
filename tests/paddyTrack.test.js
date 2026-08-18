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

/* ---- corridor geometry ---------------------------------------------
   The drawn band and the number in the panel have to be the same claim.
   They were not: the selected-hour band reused the 7-day lane's
   per-step width, so on live forcing a band labelled "+-2.49 km either
   side" was drawn ramping +-1.02 -> +-3.68 km, i.e. 2.0 km wide at the
   near end and 7.4 km at the far end. Visually it read as a lopsided
   orange wedge spilling off one side of the track. */

// Half-width of a ribbon at vertex k, recovered from the closed polygon.
// corridor() returns left[0..n-1] ++ reverse(right[0..n-1]), so vertex k
// pairs with poly[len-1-k].
function halfWidthAt(poly, k) {
  const a = poly[k], b = poly[poly.length - 1 - k];
  const kLat = 111.132, kLng = 111.32 * Math.cos((a[0] * Math.PI) / 180);
  return Math.hypot((b[1] - a[1]) * kLng, (b[0] - a[0]) * kLat) / 2;
}

// A curving track with a genuinely growing spread, which is the case
// that exposed the bug.
function curvedForecast() {
  const F = {
    bbox: BBOX,
    rtofs: [{ t: 0, g: uniform(0.25, -0.18), k: 1 }, { t: 200, g: uniform(0.25, -0.18), k: 1 }],
    surface: [],
    wind: [{ t: 0, g: uniform(4, -3), k: 1 }, { t: 200, g: uniform(4, -3), k: 1 }],
  };
  return PT.forecast(F, -119.0, 33.5, 168);
}

test("selected-hour band is ONE width — the spread at that hour", () => {
  const f = curvedForecast();
  const i = f.steps.findIndex((s) => s.t === 24);
  const [lo, hi] = PT.alongSpan(f.steps, i);
  const want = f.steps[i].crossKm;
  const poly = PT.corridor(f.steps, lo, hi, () => want);
  const n = poly.length / 2;
  // 0.5% covers the spherical round-trip in halfWidthAt (it recovers the
  // scale at the offset point, not the centre). The regression it guards
  // against was a 3.6x ramp, so this is nowhere near too loose.
  for (let k = 0; k < n; k++) {
    const got = halfWidthAt(poly, k);
    assert.ok(Math.abs(got - want) / want < 0.005,
      `vertex ${k}: half-width ${got.toFixed(4)} km, expected a constant ${want.toFixed(4)} km`);
  }
});

test("the 7-day lane still widens — growth is real, not a bug to flatten", () => {
  const f = curvedForecast();
  let laneEnd = 0;
  f.steps.forEach((s, i) => { if (s.t <= f.laneEndH) laneEnd = i; });
  const poly = PT.corridor(f.steps, 0, laneEnd, (i) => f.steps[i].crossKm);
  const n = poly.length / 2;
  const first = halfWidthAt(poly, 0), last = halfWidthAt(poly, n - 1);
  assert.ok(last > first, `lane should widen: ${first.toFixed(2)} -> ${last.toFixed(2)} km`);
  // crossKm is built as a non-decreasing envelope, so the drawn lane
  // must never narrow either.
  for (let k = 1; k < n; k++) {
    assert.ok(halfWidthAt(poly, k) >= halfWidthAt(poly, k - 1) - 1e-9,
      `lane narrows at vertex ${k} — uncertainty does not shrink with time`);
  }
});

test("a corridor contains the stretch of track it is drawn around", () => {
  const f = curvedForecast();
  const i = f.steps.findIndex((s) => s.t === 24);
  const [lo, hi] = PT.alongSpan(f.steps, i);
  const poly = PT.corridor(f.steps, lo, hi, () => f.steps[i].crossKm);
  const inside = (pt) => {
    let c = false;
    for (let a = 0, b = poly.length - 1; a < poly.length; b = a++) {
      const yi = poly[a][0], xi = poly[a][1], yj = poly[b][0], xj = poly[b][1];
      if ((yi > pt[0]) !== (yj > pt[0]) &&
          pt[1] < ((xj - xi) * (pt[0] - yi)) / (yj - yi) + xi) c = !c;
    }
    return c;
  };
  for (let k = lo + 1; k < hi; k++) {
    assert.ok(inside([f.steps[k].lat, f.steps[k].lng]),
      `hour ${f.steps[k].t} falls outside its own corridor`);
  }
});

test("corridor geometry is pure — no DOM, no Leaflet", () => {
  // It used to live in trackui.js, which cannot be loaded headlessly.
  ["corridor", "alongSpan", "dirAt", "offsetKm"].forEach((fn) =>
    assert.equal(typeof PT[fn], "function", `PT.${fn} should be exported`));
});

/* ---- corridor geometry across many drift regimes --------------------
   The single-scenario tests above missed two defects that only appear
   when the track turns. This drives the engine with forcing that turns,
   reverses, shears and stalls, and checks every drawn band in every
   run. Both defects were pre-existing and both made the band cover far
   more water than its own label claimed:

     * alongSpan measured reach as straight-line distance from the
       selected step. A track curving back toward its own position stays
       close as the crow flies however far it has drifted, so the span
       ran away - 135 hours selected for a +-23.5 km reach.
     * even bounded by arc length, a span may wrap a turn. On live
       forcing hours 60-84 swung ~147 deg, and the ribbon wrapped its
       own tail into a lobed blob. */

const SCENARIOS = {
  "straight east": { cu: 0.25, cv: 0, wu: 3, wv: 0 },
  "straight south": { cu: 0, cv: -0.25, wu: 0, wv: -4 },
  "hard reversal": { turn: (t) => [t < 72 ? 0.3 : -0.3, -0.1], wu: 2, wv: -2 },
  "steady turn": {
    turn: (t) => [0.22 * Math.cos((t / 168) * Math.PI), 0.22 * Math.sin((t / 168) * Math.PI) - 0.12],
    wu: 4, wv: -3,
  },
  "S-curve": { turn: (t) => [0.2 * Math.sin((t / 84) * Math.PI * 2), -0.18], wu: 0, wv: -3 },
  "near stall": { cu: 0.02, cv: -0.01, wu: 0.5, wv: 0 },
  "fast offshore": { cu: -0.45, cv: -0.2, wu: -8, wv: -4 },
  "wind only": { cu: 0, cv: 0, wu: 9, wv: -5 },
  "current against wind": { cu: 0.3, cv: 0, wu: -9, wv: 0 },
};
function scenarioForecast(spec) {
  const hrs = [0, 24, 48, 72, 96, 120, 144, 168, 200];
  const cur = (t) => (spec.turn ? spec.turn(t) : [spec.cu, spec.cv]);
  return PT.forecast({
    bbox: BBOX,
    rtofs: hrs.map((t) => ({ t, g: uniform(...cur(t)), k: 1 })),
    surface: [],
    wind: hrs.map((t) => ({ t, g: uniform(spec.wu, spec.wv), k: 1 })),
  }, -119.2, 33.4, 168);
}
// Recovering the width round-trips through a spherical approximation, so
// compare relatively (0.5%) rather than exactly.
function widthAt(poly, k) {
  const a = poly[k], b = poly[poly.length - 1 - k];
  const kLat = 111.132, kLng = 111.32 * Math.cos((a[0] * Math.PI) / 180);
  return Math.hypot((b[1] - a[1]) * kLng, (b[0] - a[0]) * kLat) / 2;
}
function pointInPoly(poly, pt) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const yi = poly[i][0], xi = poly[i][1], yj = poly[j][0], xj = poly[j][1];
    if ((yi > pt[0]) !== (yj > pt[0]) &&
        pt[1] < ((xj - xi) * (pt[0] - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

for (const [name, spec] of Object.entries(SCENARIOS)) {
  test(`corridor holds its contract — ${name}`, () => {
    const fc = scenarioForecast(spec);
    const steps = fc.steps;
    assert.ok(steps.length > 24, "scenario should produce a usable run");
    let bandsChecked = 0;

    for (const s of steps) {
      if (s.t === 0 || s.t % 6 !== 0 || s.t > fc.laneEndH) continue;
      const i = steps.indexOf(s);
      const [lo, hi] = PT.alongSpan(steps, i);
      if (hi - lo < 2) continue;
      bandsChecked++;

      const band = PT.corridor(steps, lo, hi, () => s.crossKm);
      for (let k = 0; k < band.length / 2; k++) {
        const got = widthAt(band, k);
        assert.ok(Math.abs(got - s.crossKm) / s.crossKm < 0.005,
          `${name} h${s.t}: drew ±${got.toFixed(2)} km where the label says ±${s.crossKm.toFixed(2)} km`);
      }
      for (let k = lo + 1; k < hi; k++) {
        assert.ok(pointInPoly(band, [steps[k].lat, steps[k].lng]),
          `${name} h${s.t}: hour ${steps[k].t} falls outside its own corridor`);
      }
    }
    assert.ok(bandsChecked > 0, `${name} drew no bands to check`);
  });
}

test("alongSpan measures along the track, not as the crow flies", () => {
  // A track that curves back stays close in straight-line terms however
  // far it has drifted, which is how the span used to run away.
  const fc = scenarioForecast(SCENARIOS["steady turn"]);
  const steps = fc.steps;
  const i = steps.findIndex((s) => s.t === 48);
  const [lo, hi] = PT.alongSpan(steps, i);
  let arc = 0;
  for (let k = lo; k < hi; k++)
    arc += PT.haversineKm(steps[k].lat, steps[k].lng, steps[k + 1].lat, steps[k + 1].lng);
  assert.ok(arc <= 2 * steps[i].alongKm * 1.05 + 1e-6,
    `span arc ${arc.toFixed(1)} km exceeds the ±${steps[i].alongKm.toFixed(1)} km it claims`);
});

test("a band never wraps a turn into a blob", () => {
  // Past ~90 deg of swing the track doubles back and a left/right ribbon
  // wraps its own tail. Measured on live forcing, the bands that
  // rendered as lobed blobs swung ~147 deg.
  for (const [name, spec] of Object.entries(SCENARIOS)) {
    const fc = scenarioForecast(spec);
    const steps = fc.steps;
    const heading = (k) => {
      const d = PT.dirAt(steps, k);
      return (Math.atan2(d[0], d[1]) * 180) / Math.PI;
    };
    for (const s of steps) {
      if (s.t === 0 || s.t % 12 !== 0 || s.t > fc.laneEndH) continue;
      const i = steps.indexOf(s);
      const [lo, hi] = PT.alongSpan(steps, i);
      for (const [a, b] of [[lo, i], [i, hi]]) {
        let sw = 0;
        for (let k = a; k < b; k++) sw += ((heading(k + 1) - heading(k) + 540) % 360) - 180;
        assert.ok(Math.abs(sw) <= 91,
          `${name} h${s.t}: band wraps ${sw.toFixed(0)}° of turn`);
      }
    }
  }
});

/* ---- origin & age ---------------------------------------------------
   The tool used to treat a sighted paddy as day zero of its life. It is
   not: it broke off a bed days ago and has been fouling since. These pin
   the estimate that replaces that assumption. Lifespan anchors are the
   deep-research-verified literature values (Hobday 2000; Graiff /
   Rothausler temperature work) — a test failing here means someone
   changed a cited number, which needs a citation, not a tweak. */

test("lifespan anchors match the literature", () => {
  assert.equal(PT.LIFE.COOL_D, 41, "cool-water median (Graiff)");
  assert.equal(PT.LIFE.WARM_D, 22, "warm-water median (Graiff)");
  // element-wise: the array comes from the vm sandbox realm, so
  // deepStrictEqual would fail on the prototype, not the values
  assert.equal(PT.LIFE.MAX_OBSERVED_D[0], 63, "SCB survivors lo (Hobday)");
  assert.equal(PT.LIFE.MAX_OBSERVED_D[1], 109, "SCB survivors hi (Hobday)");
  assert.equal(PT.lifespanDays(14).typical, 41);
  assert.equal(PT.lifespanDays(20).typical, 22);
  assert.equal(PT.lifespanDays(24.5).band, "hot", ">24 C is the hard-sink regime");
  // interpolation between the anchors is monotone in temperature
  let prev = Infinity;
  for (const t of [12, 16, 17, 18, 19, 21, 24, 26]) {
    const d = PT.lifespanDays(t).typical;
    assert.ok(d <= prev, `lifespan should not lengthen as water warms (${t}C)`);
    prev = d;
  }
  // no temperature -> a band, not a fabricated point estimate
  const u = PT.lifespanDays(null);
  assert.equal(u.typical, null);
  assert.ok(u.lo === 22 && u.hi === 41);
});

function bedsFC(points) {
  return { type: "FeatureCollection", features: points.map(([lng, lat, detach, island]) => ({
    type: "Feature", properties: { bed: "t", island: !!island, detach_now: detach ?? 1 },
    geometry: { type: "Point", coordinates: [lng, lat] },
  })) };
}

test("source candidates sit up-current, not just nearby", () => {
  // Flow is due EAST at the sighting, so the paddy came FROM the west.
  // One bed west (upstream), one equally near bed east (downstream).
  const beds = bedsFC([[-119.5, 33.0], [-118.5, 33.0]]);
  const sight = { lng: -119.0, lat: 33.0 };
  const c = PT.sourceCandidates(sight.lng, sight.lat, beds, 90, 20, 24, null, 22);
  assert.equal(c.length, 1, "only the upstream bed qualifies");
  assert.ok(c[0].lng === -119.5, "the WEST bed is the source candidate");
  // ~46.7 km at 20 km/day, regime 0.6-1.8x -> ~1.3 to ~3.9 days
  assert.ok(c[0].transitLo > 1 && c[0].transitLo < 2, `lo ${c[0].transitLo}`);
  assert.ok(c[0].transitHi > 3 && c[0].transitHi < 5, `hi ${c[0].transitHi}`);
});

test("unreachably distant beds are excluded by the shed window", () => {
  // ~514 km upstream at 20 km/day: the fast end of the regime (1.8x)
  // makes that ~14 days, so it IS reachable inside a 24-day window and
  // must be excluded only when the window is shorter than that.
  const beds = bedsFC([[-124.5, 33.0]]);
  const wide = PT.sourceCandidates(-119.0, 33.0, beds, 90, 20, 24, null, 22);
  assert.equal(wide.length, 1, "reachable inside 24 days at the fast end");
  const tight = PT.sourceCandidates(-119.0, 33.0, beds, 90, 20, 10, null, 22);
  assert.equal(tight.length, 0, "beyond a 10-day shed window");
});

test("shedding history weights the implied shed date", () => {
  // Two beds, same bearing, different distances -> different implied shed
  // dates. A timeline with all the shedding 5 days ago should favour the
  // bed whose transit time is ~5 days over the one at ~1 day.
  const beds = bedsFC([[-119.22, 33.0], [-120.08, 33.0]]);   // ~20 km and ~100 km west
  const tl = [];
  for (let d = 0; d <= 20; d++) tl.push({ days_ago: d, shed: d === 5 ? 2.0 : 0.1 });
  const c = PT.sourceCandidates(-119.0, 33.0, beds, 90, 20, 24, tl, 22);
  assert.equal(c.length, 2);
  assert.ok(Math.abs(c[0].transitMid - 5) < 1.2,
    `the ~5-day bed should rank first (got transitMid ${c[0].transitMid.toFixed(1)})`);
});

test("outlook subtracts age from lifespan and clamps at zero", () => {
  const life = PT.lifespanDays(20);            // warm: typical 22
  const young = PT.paddyOutlook([{ transitLo: 2, transitHi: 6 }], life);
  assert.equal(young.leftLo, 16, "22 - 6");
  assert.equal(young.leftHi, 20, "22 - 2");
  const old = PT.paddyOutlook([{ transitLo: 20, transitHi: 30 }], life);
  assert.equal(old.leftLo, 0, "never negative");
  assert.equal(PT.paddyOutlook([], life), null, "no candidates, no claim");
});

test("landmarks name the neighbourhood, and stay silent far offshore", () => {
  assert.equal(PT.nearestLandmark(-118.45, 33.35), "Catalina");
  assert.equal(PT.nearestLandmark(-117.27, 32.70), "Point Loma");
  assert.equal(PT.nearestLandmark(-125.0, 35.0), null, "no name 500 km out");
});
