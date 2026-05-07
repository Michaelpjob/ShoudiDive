/**
 * cp-data-shape — manifest schema checkpoint.
 *
 * Reads the actual published `public/data/manifest.json` and asserts
 * the contract React + RN clients depend on. Strictly tests the data
 * SHAPE, not the source-code path that produces it — implementation
 * details (which loader branch handles which layer) are caught by
 * `cp-static-lint`'s `no-dupe-else-if` rule and the runtime smoke,
 * not by source-grepping here.
 *
 * Catches:
 *   - manifest.json gone / unreadable
 *   - generated_at missing or malformed
 *   - a required layer dropped from the manifest
 *
 * Non-scope:
 *   - "is the manifest fresh?" — that's live-cp-manifest
 *   - "does dataSource.js have a branch for this layer?" — covered
 *     organically by cp-runtime-smoke + cp-visual-paint actually
 *     loading every layer; source-grepping was too brittle.
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


// Layers the React + RN clients hard-code. If one disappears from
// manifest, those clients render an empty layer and the user sees
// blank tiles.
const REQUIRED_LAYERS = [
  "sst", "sst7d", "sst5d", "chl", "kd490",
  "wind", "wind5d", "swell5d", "viz", "wave", "precip",
];


// ---------------------------------------------------------------------
// File presence + top-level shape
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


// ---------------------------------------------------------------------
// Per-layer minimal contract: each layer entry has SOMETHING the
// client can actually use — either a `summary_url` (5d/7d feeds)
// OR a `windows.<key>.url` (the standard PNG-window pattern).
//
// We deliberately don't list per-layer required fields beyond that,
// because the actual fields vary (wind has speed_url+uv_url; wave
// has wave_url; precip has just url). Over-specifying caused this
// test to flake on legitimate data shapes.
// ---------------------------------------------------------------------

test("cp-data-shape: every layer has either a summary_url or a windows entry", () => {
  const m = readJson("public/data/manifest.json");
  const orphans = [];
  for (const id of REQUIRED_LAYERS) {
    const layer = m.layers[id];
    if (!layer) continue; // covered by REQUIRED_LAYERS test above
    const hasSummary = typeof layer.summary_url === "string";
    const windows = layer.windows || {};
    const hasWindowedUrl = Object.values(windows).some(
      (w) => w && (typeof w.url === "string" ||
                   typeof w.speed_url === "string" ||
                   typeof w.uv_url === "string" ||
                   typeof w.wave_url === "string"),
    );
    if (!hasSummary && !hasWindowedUrl) {
      orphans.push(id);
    }
  }
  assert.deepEqual(
    orphans, [],
    `Layers with neither summary_url nor any windowed url: ${orphans.join(", ")}\n` +
    `These layers cannot be loaded by any current client decoder branch.`,
  );
});


test("cp-data-shape: 5d/7d summary layers carry summary_url that points to /data/", () => {
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
