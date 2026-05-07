import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dataRoot = resolve(repoRoot, "public/data");
const manifestPath = resolve(dataRoot, "manifest.json");

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function localDataPath(url) {
  assert.equal(typeof url, "string", `expected data URL string, got ${typeof url}`);
  assert.equal(url.startsWith("/data/"), true, `expected /data URL, got ${url}`);
  return resolve(dataRoot, url.slice("/data/".length));
}

assert.equal(existsSync(manifestPath), true, "public/data/manifest.json must exist");
const manifest = readJson(manifestPath);
const layers = manifest.layers || {};

for (const layer of ["sst", "sst7d", "wind5d", "swell5d", "current5d", "viz"]) {
  assert.ok(layers[layer], `manifest must include ${layer}`);
}

const sst7d = layers.sst7d;
assert.equal(typeof sst7d.summary_url, "string", "sst7d must expose summary_url");
assert.deepEqual(sst7d.range, layers.sst.range, "sst7d range must match sst");
assert.deepEqual(sst7d.grid, layers.sst.grid, "sst7d grid must match sst");

const sstSummaryPath = localDataPath(sst7d.summary_url);
assert.equal(existsSync(sstSummaryPath), true, `sst7d summary missing: ${sst7d.summary_url}`);
const sstSummary = readJson(sstSummaryPath);
assert.equal(Array.isArray(sstSummary.days), true, "sst7d summary must have days[]");
assert.ok(sstSummary.days.length >= 3, `sst7d must retain at least 3 days, got ${sstSummary.days.length}`);
assert.equal(typeof sstSummary.latest_slot, "string", "sst7d summary must include latest_slot");

for (const day of sstSummary.days) {
  assert.equal(typeof day.slot, "string", "sst7d day must include slot");
  assert.equal(typeof day.date, "string", `sst7d ${day.slot} must include date`);
  assert.equal(typeof day.url, "string", `sst7d ${day.slot} must include url`);
  assert.equal(existsSync(localDataPath(day.url)), true, `sst7d PNG missing: ${day.url}`);
}

for (const layer of ["wind5d", "swell5d", "current5d"]) {
  const summaryPath = localDataPath(layers[layer].summary_url);
  assert.equal(existsSync(summaryPath), true, `${layer} summary missing: ${layers[layer].summary_url}`);
  const summary = readJson(summaryPath);
  assert.equal(Array.isArray(summary.days), true, `${layer} summary must include days[]`);
  assert.ok(summary.days.length >= 5, `${layer} must retain at least 5 days`);
}

console.log("data feature contracts passed");
