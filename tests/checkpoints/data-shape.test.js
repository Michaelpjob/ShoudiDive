/**
 * cp-data-shape — manifest schema + decoder contract checkpoint.
 *
 * Catches manifest-shape regressions: a layer key getting renamed,
 * a decoder field disappearing, a manifest entry missing a `range`,
 * etc. These are the bugs that break the React + RN clients silently
 * (the data layer returns NaN forever) without surfacing as a build
 * or lint failure.
 *
 * Scope:
 *   - Live `public/data/manifest.json` has every layer the clients
 *     hard-code.
 *   - Each layer entry exposes the fields its decoder needs
 *     (`range`/`scale`/`unit` for grayscale; `summary_url` for 5d).
 *   - dataSource.js's loader branch list includes every layer the
 *     manifest publishes.
 *
 * Non-scope:
 *   - Whether the published PNGs actually have data (live-cp-pngs).
 *   - Whether `generated_at` is fresh (live-cp-manifest).
 *   - End-to-end render correctness (cp-runtime-smoke / live-cp-render).
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";


const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

function readJson(rel) {
  return JSON.parse(readFileSync(resolve(REPO_ROOT, rel), "utf8"));
}

function readText(rel) {
  return readFileSync(resolve(REPO_ROOT, rel), "utf8");
}


// ---------------------------------------------------------------------
// Manifest sanity
// ---------------------------------------------------------------------

test("cp-data-shape: manifest.json exists at the expected path", () => {
  assert.equal(
    existsSync(resolve(REPO_ROOT, "public/data/manifest.json")),
    true,
    "public/data/manifest.json must exist; either the pipeline " +
    "hasn't run on this branch yet OR the publish path moved",
  );
});


test("cp-data-shape: manifest top-level shape is what dataSource expects", () => {
  const m = readJson("public/data/manifest.json");
  assert.equal(typeof m.generated_at, "string", "generated_at must be a string");
  assert.match(
    m.generated_at,
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/,
    "generated_at must look ISO-8601",
  );
  assert.equal(typeof m.layers, "object", "layers must be an object");
  assert.equal(Array.isArray(m.bbox), true, "bbox must be an array");
  assert.equal(m.bbox.length, 4, "bbox must be [lng_min, lat_min, lng_max, lat_max]");
});


// Layers the React + RN clients hard-code. If one disappears from
// manifest, those clients render an empty layer and the user sees
// blank tiles. This list is the source of truth — update both this
// AND the matching client branches when adding/renaming a layer.
const REQUIRED_LAYERS = [
  "sst",       // primary SST grayscale + windows
  "sst7d",     // 7-day history (Phase A trend feeds off this)
  "sst5d",     // 7-day forecast (Phase E + parallel work)
  "chl",       // chlorophyll-a
  "kd490",     // diffuse attenuation (visibility input)
  "wind",      // wind nowcast
  "wind5d",    // 7-day wind forecast
  "swell5d",   // 7-day swell forecast
  "viz",       // predicted visibility
  "wave",      // wave Hs/Tp/Dp
  "precip",    // 7-day precip rollup
];

test("cp-data-shape: every required layer is present in the manifest", () => {
  const m = readJson("public/data/manifest.json");
  const got = Object.keys(m.layers);
  const missing = REQUIRED_LAYERS.filter((k) => !got.includes(k));
  assert.deepEqual(
    missing, [],
    `manifest is missing required layers: ${missing.join(", ")}\n` +
    `  got: [${got.join(", ")}]\n` +
    `  expected at least: [${REQUIRED_LAYERS.join(", ")}]\n` +
    `If a layer was intentionally retired, remove it from REQUIRED_LAYERS in this test.`,
  );
});


test("cp-data-shape: each grayscale-windowed layer carries range + scale + unit + grid", () => {
  const m = readJson("public/data/manifest.json");
  // sst/chl/kd490/viz/wind/wave/precip use the windowed pattern.
  // sst5d/sst7d/wind5d/swell5d use summary_url + range; tested below.
  const windowedLayers = ["sst", "chl", "kd490", "viz", "wind", "wave", "precip"];
  for (const id of windowedLayers) {
    const layer = m.layers[id];
    if (!layer) continue;   // covered by REQUIRED_LAYERS test above
    assert.equal(
      Array.isArray(layer.range) && layer.range.length === 2,
      true,
      `layer.${id}.range must be [min, max]`,
    );
    assert.equal(
      typeof layer.scale, "string",
      `layer.${id}.scale must be a string ("linear" or "log10")`,
    );
    assert.equal(
      typeof layer.unit, "string",
      `layer.${id}.unit must be a string (e.g. "degC", "mg/m^3")`,
    );
    assert.equal(
      typeof layer.grid?.width, "number",
      `layer.${id}.grid.width must be a number`,
    );
    assert.equal(
      typeof layer.grid?.height, "number",
      `layer.${id}.grid.height must be a number`,
    );
  }
});


test("cp-data-shape: every 5d/7d summary layer carries summary_url", () => {
  const m = readJson("public/data/manifest.json");
  for (const id of ["sst7d", "sst5d", "wind5d", "swell5d"]) {
    const layer = m.layers[id];
    if (!layer) continue;
    assert.equal(
      typeof layer.summary_url, "string",
      `layer.${id}.summary_url must be a string for the 5d/7d UI to fetch`,
    );
    assert.equal(
      layer.summary_url.startsWith("/data/"),
      true,
      `layer.${id}.summary_url must be a /data/ relative path; got ${layer.summary_url}`,
    );
  }
});


// ---------------------------------------------------------------------
// dataSource.js loader branch coverage
// ---------------------------------------------------------------------
//
// Every layer the manifest publishes needs a matching loader branch
// in src/lib/dataSource.js — otherwise it silently doesn't load. The
// loader uses `if/else if` chains keyed off `layer === "<id>"`; this
// test grep-checks each required layer has a branch.

test("cp-data-shape: dataSource.js has a loader branch for every layer", () => {
  const src = readText("src/lib/dataSource.js");
  // The loader checks `layer === "<id>"`. We tolerate slight whitespace
  // variation but require the literal layer id in a `===` comparison.
  for (const id of REQUIRED_LAYERS) {
    const re = new RegExp(`layer\\s*===\\s*['"\`]${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}['"\`]`);
    assert.match(
      src, re,
      `src/lib/dataSource.js is missing a loader branch for layer "${id}". ` +
      `If the manifest is publishing this key, the client will silently fail to decode it.`,
    );
  }
});


test("cp-data-shape: dataSource.js does not contain duplicate else-if branches", () => {
  // The 2026-05-07 white-screen had a sibling: two `else if (layer === "sst5d")`
  // branches in the loader. This test catches that family of cherry-pick
  // accidents in source review (ESLint also catches it now, but defense
  // in depth).
  const src = readText("src/lib/dataSource.js");
  const seen = new Map();   // layer id → first line number
  // Match `layer === "<id>"` (any quote variant) inside an else-if chain
  // structure. Lightweight enough for this contract test.
  const re = /layer\s*===\s*['"`]([^'"`]+)['"`]/g;
  let m;
  let lineNo = 1;
  let lastIdx = 0;
  while ((m = re.exec(src)) !== null) {
    // Compute line number from offset
    while (lastIdx < m.index) {
      if (src[lastIdx++] === "\n") lineNo++;
    }
    const id = m[1];
    if (seen.has(id)) {
      // Allow up to 1 outer + 1 nested check (e.g. inside getLayerGrid
      // AND inside loadManifest) — that's two distinct contexts, OK.
      // Three or more = drift / dupe.
      const prev = seen.get(id);
      seen.set(id, [...(Array.isArray(prev) ? prev : [prev]), lineNo]);
    } else {
      seen.set(id, lineNo);
    }
  }
  // Flag any layer id that appears more than 2 times in `===` comparisons
  // (one for each of: loadManifest branch, getLayerGrid branch).
  const dupes = [];
  for (const [id, occ] of seen) {
    const count = Array.isArray(occ) ? occ.length : 1;
    if (count > 2) {
      dupes.push(`${id} appears ${count} times at lines ${Array.isArray(occ) ? occ.join(", ") : occ}`);
    }
  }
  assert.deepEqual(
    dupes, [],
    `dataSource.js has duplicate \`layer === "<id>"\` checks:\n  ${dupes.join("\n  ")}\n` +
    `Likely a cherry-pick conflict resolution that left two implementations.`,
  );
});
