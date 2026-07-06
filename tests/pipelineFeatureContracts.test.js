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
  // Shape consistency is enforced POST-HOC in build_layer (the parallel
  // day-walk can't thread expected_shape through each fetch_day call). The
  // stack grid is chosen by _choose_stack_shape, which PREFERS the primary
  // source's grid over a coarser fallback — otherwise MUR L4's ~2-day
  // publication lag let the 5 km blended fallback hijack the grid and every
  // 1 km MUR day got dropped as "shape differs". Same contract (one grid,
  // never truncate/mix), stronger mechanism (prefer the high-res primary).
  assert.match(fetchPipeline, /def _choose_stack_shape\(/);
  assert.match(fetchPipeline, /a\.shape != target_shape/);
  assert.match(fetchPipeline, /preferring primary/);
  assert.match(fetchPipeline, /ThreadPoolExecutor/);
  assert.match(fetchPipeline, /candidate_configs\(cfg\)/);
});

test("SST beta forecast is generated and guarded as a first-class data product", () => {
  const fetchPipeline = read("pipeline/fetch.py");
  const freshness = read("pipeline/check_manifest_freshness.py");
  const published = read("pipeline/check_published.py");
  const workflow = read(".github/workflows/refresh-ca-data.yml");

  assert.match(fetchPipeline, /def build_sst_forecast\(/);
  assert.match(fetchPipeline, /"forecast_summary_url":?|\["forecast_summary_url"\]/);
  assert.match(fetchPipeline, /manifest\["layers"\]\["sst5d"\]/);
  assert.match(fetchPipeline, /"beta": True/);
  assert.match(fetchPipeline, /f\{lead\}_sst\.png/);
  assert.match(freshness, /"sst5d": 96/);
  assert.match(freshness, /"sst5d": 5/);
  assert.match(published, /"sst", "sst7d", "sst5d"/);
  assert.match(workflow, /--layers sst,sst7d,sst5d,/);
});
