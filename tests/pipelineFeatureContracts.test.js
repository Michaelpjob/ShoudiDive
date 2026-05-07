import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function read(rel) {
  return readFileSync(resolve(repoRoot, rel), "utf8");
}

test("SST history fallbacks keep a single grid instead of truncating the slider", () => {
  const fetchPipeline = read("pipeline/fetch.py");

  assert.match(fetchPipeline, /def fetch_day\([\s\S]*expected_shape: tuple\[int, int\] \| None = None/);
  assert.match(fetchPipeline, /expected_shape is not None and arr\.shape != expected_shape/);
  assert.match(fetchPipeline, /trying next source/);
  assert.match(fetchPipeline, /expected_shape = stack_rev\[0\]\.shape if stack_rev else None/);
  assert.match(fetchPipeline, /fetch_day\(layer, cfg, d, cfg\["stride"\], expected_shape=expected_shape\)/);
  assert.match(fetchPipeline, /candidate_configs\(cfg\)/);
});
