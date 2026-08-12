// Unit tests for the GPS formatting of traced temperature breaks
// (src/lib/breaksGps.js). The copy block is what a user pastes into a
// chartplotter — formats here are a user-facing contract.

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  gridToLngLat,
  toDDM,
  formatWaypoint,
  pickWaypoints,
  buildGpsText,
} from "../src/lib/breaksGps.js";

const BBOX = { latMin: 31.8, latMax: 42.0, lngMin: -128.5, lngMax: -116.8 };

test("grid corners map to bbox corners (center-of-pixel)", () => {
  const [lng0, lat0] = gridToLngLat(0, 0, 586, 511, BBOX);
  assert.ok(Math.abs(lng0 - (-128.49)) < 0.02 && Math.abs(lat0 - 41.99) < 0.02,
    `top-left ~ (-128.5, 42.0), got (${lng0.toFixed(3)}, ${lat0.toFixed(3)})`);
  const [lng1, lat1] = gridToLngLat(585, 510, 586, 511, BBOX);
  assert.ok(Math.abs(lng1 - (-116.81)) < 0.02 && Math.abs(lat1 - 31.81) < 0.02,
    `bottom-right ~ (-116.8, 31.8), got (${lng1.toFixed(3)}, ${lat1.toFixed(3)})`);
});

test("DDM formatting matches chartplotter convention", () => {
  assert.equal(toDDM(33.4212, true), "33°25.272'N");
  assert.equal(toDDM(-118.0145, false), "118°0.870'W");
  assert.equal(toDDM(-33.5, true), "33°30.000'S");
});

test("formatWaypoint carries both DDM and decimal", () => {
  const wp = formatWaypoint([-118.0145, 33.4212]);
  assert.equal(wp.ddm, "33°25.272'N, 118°0.870'W");
  assert.equal(wp.dec, "33.4212, -118.0145");
});

test("pickWaypoints keeps endpoints and caps the count", () => {
  const pts = Array.from({ length: 40 }, (_, i) => [i, i]);
  const wps = pickWaypoints(pts, 5);
  assert.equal(wps.length, 5);
  assert.deepEqual(wps[0], [0, 0]);
  assert.deepEqual(wps[4], [39, 39]);
  assert.deepEqual(pickWaypoints([[1, 2], [3, 4]], 5), [[1, 2], [3, 4]]);
});

test("the copy block reads Start..End with span, date, and drift caveat", () => {
  const front = { spanKm: 85, points: Array.from({ length: 30 }, (_, i) => [100 + i * 5, 200 + i * 3]) };
  const text = buildGpsText(front, { width: 586, height: 511 }, BBOX, "2026-08-09");
  assert.match(text, /^Temperature break — ~85 km \(satellite data 2026-08-09\)/);
  assert.match(text, /Start\s+\d+°[\d.]+'N, \d+°[\d.]+'W\s+\(\d+\.\d{4}, -\d+\.\d{4}\)/);
  assert.match(text, /Mid 1/);
  assert.match(text, /End\s+\d+°/);
  assert.match(text, /search line, not a pin/);
  const lines = text.split("\n");
  assert.equal(lines.length, 7, `header + 5 waypoints + caveat, got ${lines.length}`);
});

test("no date -> no date clause, still valid", () => {
  const text = buildGpsText({ spanKm: 40, points: [[0, 0], [50, 50]] }, { width: 586, height: 511 }, BBOX, null);
  assert.match(text, /^Temperature break — ~40 km\n/);
  assert.match(text, /Start/);
  assert.match(text, /End/);
});
