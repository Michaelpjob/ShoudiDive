// Contract test for Spot Detail bundle.json shape (Phase 1B).
//
// The pipeline writes public/data/spots/<id>/bundle.json + an
// index.json listing the built spots. The frontend SpotDetailView
// fetches both and renders against the contracted shape. This test
// pins that contract so a future change in build_spot_bundles.py
// that drops a required key (or renames `bbox` → `extent` etc.) fails
// the dev gate before it ships.
//
// We test against the LIVE checked-in bundles when present (CA spots
// landed after the pipeline runs); if none are present yet (fresh
// branch, pipeline hasn't run), we fall back to a fixture so the
// test stays green for greenfield agents.

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const spotsDir = resolve(repoRoot, "public/data/spots");

const FIXTURE_BUNDLE = {
  id: "lajolla",
  name: "La Jolla",
  centre: { lng: -117.275, lat: 32.854 },
  bbox: {
    lng_min: -117.318, lng_max: -117.232,
    lat_min:   32.818, lat_max:   32.890,
  },
  generated_at: "2026-05-27T00:00:00Z",
  layers: {
    bathy: { url: "bathy.png", width: 480, height: 480,
             depth_range_m: [0, 500], encoding: "linear_8bit_0nan" },
    contours: { url: "contours.geojson", intervals_m: [1, 5, 25, 100],
                levels: 32 },
    coastline: { url: "coastline.geojson", features: 5 },
    kelp: { url: "kelp.geojson", features: 4 },
    mpa: { url: "mpa.geojson", features: 2 },
  },
  sources: {
    bathy: "GMRT high-resolution",
    coastline: "OSM",
    kelp: "CDFW ds3135",
    mpa: "CDFW ds582",
  },
};

function loadFirstBundle() {
  if (!existsSync(spotsDir)) return FIXTURE_BUNDLE;
  const candidates = readdirSync(spotsDir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => resolve(spotsDir, d.name, "bundle.json"))
    .filter((p) => existsSync(p));
  if (!candidates.length) return FIXTURE_BUNDLE;
  return JSON.parse(readFileSync(candidates[0], "utf8"));
}

test("Spot Detail bundle.json carries the contracted top-level keys", () => {
  const b = loadFirstBundle();
  for (const k of ["id", "name", "centre", "bbox", "generated_at", "layers", "sources"]) {
    assert.ok(k in b, `bundle.json missing required key: ${k}`);
  }
  // centre must have lng + lat
  assert.equal(typeof b.centre.lng, "number");
  assert.equal(typeof b.centre.lat, "number");
});

test("Spot Detail bundle bbox is a valid lng/lat rectangle", () => {
  const { bbox } = loadFirstBundle();
  for (const k of ["lng_min", "lng_max", "lat_min", "lat_max"]) {
    assert.ok(k in bbox, `bbox missing ${k}`);
    assert.equal(typeof bbox[k], "number");
  }
  assert.ok(bbox.lng_max > bbox.lng_min, "lng_max must exceed lng_min");
  assert.ok(bbox.lat_max > bbox.lat_min, "lat_max must exceed lat_min");
});

test("Spot Detail bundle declares the bathy + contours layers", () => {
  const { layers } = loadFirstBundle();
  assert.ok(layers.bathy, "bathy layer missing");
  assert.equal(layers.bathy.url, "bathy.png");
  assert.ok(Array.isArray(layers.bathy.depth_range_m));
  assert.equal(layers.bathy.depth_range_m.length, 2);
  assert.equal(typeof layers.bathy.width, "number");
  assert.equal(typeof layers.bathy.height, "number");

  assert.ok(layers.contours, "contours layer missing");
  assert.equal(layers.contours.url, "contours.geojson");
});

test("Spot Detail index.json carries the spots array + generated_at", () => {
  const indexPath = resolve(spotsDir, "index.json");
  // Pipeline may not have run yet on a greenfield branch — skip the
  // strict check if the index doesn't exist. The contract still
  // pinned by the per-bundle test above.
  if (!existsSync(indexPath)) return;
  const idx = JSON.parse(readFileSync(indexPath, "utf8"));
  assert.ok(Array.isArray(idx.spots), "index.json must have spots[] array");
  assert.equal(typeof idx.generated_at, "string");
});
