// Reef / bank positions must agree across every surface that names them.
//
// The same offshore feature is placed in three files that feed different
// parts of the product:
//   public/data/bathy-features.geojson          main map labels + popups,
//                                               Fleet Tracks, Pelagic Lab
//   paddies-model/features.py                   paddy finder "work X" phrasing
//                                               and its structure bonus
//   pipeline/validation/ingest/_spot_lookup.json geolocates field reports
//
// A 2026-10-07 audit against GMRT bathymetry, USGS GNIS and the NOAA Coast
// Pilot found the three disagreeing by up to 90 km, with most offshore pins
// in 600-1,800 m of water: Potato Bank sat in the basin between San Nicolas
// and Catalina, the Footprint sat north of Anacapa instead of inside its own
// reserve, and the numbered San Diego spots were read as FEET instead of the
// chart soundings in FATHOMS they are named for. These tests pin what was
// verified so the surfaces cannot quietly drift apart again.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(resolve(root, p), "utf8");

const bathy = JSON.parse(read("public/data/bathy-features.geojson")).features;
const byId = Object.fromEntries(bathy.map((f) => [f.properties.id, f]));
const lookup = JSON.parse(read("pipeline/validation/ingest/_spot_lookup.json"));
const paddyFeatures = {};
for (const m of read("paddies-model/features.py").matchAll(
  /\("([^"]+)",\s*(-?[\d.]+),\s*(-?[\d.]+),\s*"(\w+)"\)/g,
)) {
  paddyFeatures[m[1]] = { lng: +m[2], lat: +m[3], type: m[4] };
}
const mpas = JSON.parse(read("public/data/mpa-boundaries.geojson")).features;

function km(a, b) {
  const R = 6371, rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
const pt = (f) => ({ lng: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] });

function inRing(p, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > p.lat) !== (yj > p.lat) &&
        p.lng < ((xj - xi) * (p.lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function inFeature(p, f) {
  const g = f.geometry;
  const polys = g.type === "MultiPolygon" ? g.coordinates : [g.coordinates];
  return polys.some((poly) => inRing(p, poly[0]) && !poly.slice(1).some((h) => inRing(p, h)));
}

// One feature, every name it goes by in each file.
const SAME_FEATURE = [
  { bathy: "cortes-bank", paddy: "Cortes Bank", lookup: ["Cortes Bank", "Cortez Bank"] },
  { bathy: "tanner-bank", paddy: "Tanner Bank", lookup: ["Tanner Bank"] },
  { bathy: "nine-mile-bank", paddy: "the 9-Mile Bank", lookup: ["9-Mile Bank", "Nine Mile Bank"] },
  { bathy: "spot-43", paddy: "the 43-Fathom Spot", lookup: ["43 Fathom"] },
  { bathy: "fourteen-mile-bank", paddy: "the 14-Mile Bank" },
  { bathy: "spot-302", paddy: "the 302 Spot" },
  { bathy: "spot-277", paddy: "the 277" },
  { bathy: "sixty-mile-bank", lookup: ["60 Mile Bank", "60-Mile Bank", "Sixty Mile Bank"] },
  { bathy: "pioneer-seamount", lookup: ["Pioneer Seamount"] },
  { bathy: "davidson-seamount", lookup: ["Davidson Seamount"] },
];
const MAX_DISAGREE_KM = 3;

for (const g of SAME_FEATURE) {
  test(`${g.bathy}: every surface puts it in the same place`, () => {
    const f = byId[g.bathy];
    assert.ok(f, `bathy-features is missing ${g.bathy}`);
    const here = pt(f);
    if (g.paddy) {
      const p = paddyFeatures[g.paddy];
      assert.ok(p, `paddies-model/features.py is missing "${g.paddy}"`);
      const d = km(here, p);
      assert.ok(d <= MAX_DISAGREE_KM,
        `"${g.paddy}" in features.py is ${d.toFixed(1)} km from the map's ${g.bathy}`);
    }
    for (const name of g.lookup || []) {
      const v = lookup[name];
      assert.ok(v, `_spot_lookup.json is missing "${name}"`);
      const d = km(here, { lat: v[0], lng: v[1] });
      assert.ok(d <= MAX_DISAGREE_KM,
        `"${name}" in _spot_lookup.json is ${d.toFixed(1)} km from the map's ${g.bathy}`);
    }
  });
}

test("the Footprint sits inside the Footprint marine reserve", () => {
  const f = byId["footprint-reef"];
  const reserve = mpas.filter((m) => /footprint/.test(m.properties.id));
  assert.ok(reserve.length, "Footprint reserve missing from the MPA layer");
  assert.ok(reserve.some((m) => inFeature(pt(f), m)),
    `Footprint at ${JSON.stringify(pt(f))} is outside its own reserve`);
  // It is a no-take reserve, so the map must not pitch it as a fishing spot.
  assert.equal((f.properties.commonSpecies || []).length, 0);
  assert.match(f.properties.description, /no take/i);
});

test("Potato Bank is west of San Nicolas Island, not in the Catalina basin", () => {
  const p = pt(byId["potato-bank"]);
  assert.ok(p.lng < -119.7 && p.lat > 33.15 && p.lat < 33.35,
    `Potato Bank at ${JSON.stringify(p)}`);
});

test("numbered spots carry their name as a depth in FATHOMS, not feet", () => {
  for (const f of bathy) {
    const m = /^spot-(\d+)$/.exec(f.properties.id);
    if (!m || f.properties.minDepthM == null) continue;
    const fathomsM = +m[1] * 1.8288;
    assert.ok(Math.abs(f.properties.minDepthM - fathomsM) / fathomsM < 0.06,
      `${f.properties.id}: minDepthM ${f.properties.minDepthM} != ${m[1]} fathoms (${fathomsM.toFixed(0)} m)`);
  }
});

test("a description only cites a marine reserve that exists in the MPA layer", () => {
  const names = mpas.map((m) => `${m.properties.id} ${m.properties.name}`.toLowerCase());
  for (const f of bathy) {
    const words = (f.properties.description || "").split(/\s+/);
    words.forEach((w, i) => {
      if (!/^(SMR|SMCAs?)[.,;:)]?$/.test(w)) return;
      const place = [];
      for (let k = i - 1; k >= 0 && /^[A-Z]/.test(words[k]); k--) place.unshift(words[k]);
      const key = place.filter((x) => !/^(Inside|Within|Partly|The)$/.test(x)).join(" ").toLowerCase();
      assert.ok(key, `${f.properties.id}: can't read the reserve name in "${f.properties.description}"`);
      assert.ok(names.some((n) => n.includes(key)),
        `${f.properties.id} claims "${key} ${w}", which is not in mpa-boundaries.geojson`);
    });
  }
});
