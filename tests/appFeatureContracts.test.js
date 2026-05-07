import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function read(rel) {
  return readFileSync(resolve(repoRoot, rel), "utf8");
}

test("Temp keeps historical and beta forecast SST timelines instead of reverting to legacy composites", () => {
  const app = read("src/App.jsx");
  const mobileSheet = read("src/components/MobileSheet.jsx");
  const dataSource = read("src/lib/dataSource.js");

  assert.match(app, /import SstTimeline,\s*\{[\s\S]*SstCurrentCard[\s\S]*sstSelToSlotKey/);
  assert.match(app, /SstModeToggle/);
  assert.match(app, /getSstForecastSummary/);
  assert.match(app, /const \[sstMode, setSstMode\] = useState\("history"\)/);
  assert.match(app, /const \[sstForecastSel, setSstForecastSel\] = useState\(\{ slot: "f0" \}\)/);
  assert.match(app, /layer === "sst"\s*\?\s*\(sstTimelineSummary \? sstSelToSlotKey\(sstActiveSel, sstTimelineSummary\) : composite\)/);
  assert.match(app, /\{layer === "sst" && hasSstTimeline && \(\s*<SstTimeline sel=\{sstActiveSel\} setSel=\{setSstActiveSel\} units=\{units\} mode=\{activeSstMode\} \/>/);
  assert.match(app, /layer === "sst" && hasSstTimeline \?\s*\(\s*<div className="composite wind-grid-host">[\s\S]*Sea temp forecast[\s\S]*<SstModeToggle[\s\S]*<SstCurrentCard sel=\{sstActiveSel\} units=\{units\} mode=\{activeSstMode\} \/>/);
  assert.match(mobileSheet, /layer === "sst" && hasSstTimeline \? `Sea temp/);
  assert.match(mobileSheet, /layer === "sst" && hasSstTimeline \?\s*\(\s*<>[\s\S]*<SstModeToggle[\s\S]*<SstCurrentCard sel=\{activeSstSel\} units=\{units\} mode=\{activeSstMode\} \/>/);
  assert.match(dataSource, /if \(layer === "sst7d"\)/);
  assert.match(dataSource, /else if \(layer === "sst5d"\)/);
  assert.match(dataSource, /state\.layers\.sst7d = \{ summary \}/);
  assert.match(dataSource, /state\.layers\.sst5d = \{ summary \}/);
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
