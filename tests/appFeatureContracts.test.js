import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function read(rel) {
  return readFileSync(resolve(repoRoot, rel), "utf8");
}

test("Temp stays on the historical SST timeline instead of the legacy composite UI", () => {
  const app = read("src/App.jsx");
  const mobileSheet = read("src/components/MobileSheet.jsx");
  const dataSource = read("src/lib/dataSource.js");

  assert.match(app, /import SstTimeline,\s*\{[\s\S]*SstCurrentCard[\s\S]*sstSelToSlotKey/);
  assert.match(app, /layer === "sst"\s*\?\s*\(sstHistorySummary \? sstSelToSlotKey\(sstSel, sstHistorySummary\) : composite\)/);
  assert.match(app, /\{layer === "sst" && sstHistorySummary && \(\s*<SstTimeline sel=\{sstSel\} setSel=\{setSstSel\} units=\{units\} \/>/);
  assert.match(app, /layer === "sst" && sstHistorySummary \?\s*\(\s*<div className="composite wind-grid-host">[\s\S]*Sea temp trend[\s\S]*<SstCurrentCard sel=\{sstSel\} units=\{units\} \/>/);
  assert.match(mobileSheet, /layer === "sst" && hasSstHistory \? "Sea temp/);
  assert.match(mobileSheet, /layer === "sst" && hasSstHistory \?\s*\(\s*<SstCurrentCard sel=\{sstSel\} setSel=\{setSstSel\} units=\{units\} \/>/);
  assert.match(dataSource, /if \(layer === "sst7d"\)/);
  assert.match(dataSource, /state\.layers\.sst7d = \{ summary \}/);
});

test("Current, swell, wind, and mobile overlay features remain wired", () => {
  const app = read("src/App.jsx");
  const mobileSheet = read("src/components/MobileSheet.jsx");
  const styles = read("src/styles/app.css");

  assert.match(app, /<CurrentTimeline sel=\{currentSel\} setSel=\{setCurrentSel\} \/>/);
  assert.match(app, /<CurrentCurrentCard sel=\{currentSel\} \/>/);
  assert.match(app, /<SwellTimeline sel=\{swellSel\} setSel=\{setSwellSel\} \/>/);
  assert.match(app, /<WindTimeline sel=\{windSel\} setSel=\{setWindSel\} \/>/);
  assert.match(mobileSheet, /className="ms-overlay-quick"/);
  assert.match(mobileSheet, /aria-pressed=\{mpaOn\}/);
  assert.match(mobileSheet, /aria-pressed=\{bathyOn\}/);
  assert.match(styles, /\.ms-overlay-quick/);
  assert.match(styles, /\.mpa-popup-close/);
  assert.match(styles, /\.mpa-popup-done/);
});

test("CI runs frontend and data feature contracts before publishing", () => {
  const pkg = JSON.parse(read("package.json"));
  const deployWorkflow = read(".github/workflows/refresh-data.yml");
  const frontendWorkflow = read(".github/workflows/frontend-tests.yml");

  assert.equal(pkg.scripts.test, "node --test tests/*.test.js");
  assert.equal(pkg.scripts["test:data-contracts"], "node tests/dataFeatureContracts.mjs");
  assert.match(frontendWorkflow, /Run frontend regression tests[\s\S]*npm test[\s\S]*Build[\s\S]*npm run build/);
  assert.match(deployWorkflow, /Run frontend regression tests[\s\S]*npm test[\s\S]*Run data feature contracts[\s\S]*npm run test:data-contracts[\s\S]*Build[\s\S]*npm run build/);
});
