// Static contract checks for src/lib/confidence.js.
//
// Pins the per-region/per-layer matrix (every region/layer pair has a
// defined entry), the documented score floors (Baja current = 2/5, CA
// current = 4/5), and the exported API.
//
// Uses the same source-string pattern as other tests/*.test.js — node's
// native test runner (no jest) on the unmodified source file.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
function read(rel) {
  return readFileSync(resolve(repoRoot, rel), "utf8");
}

test("confidence.js defines static matrix entries for every region", () => {
  const src = read("src/lib/confidence.js");
  for (const region of ["ca", "baja", "pnw", "tropical"]) {
    assert.match(src, new RegExp(`${region}:\\s*\\{`), `missing region ${region}`);
  }
});

test("confidence.js defines every layer for every region", () => {
  const src = read("src/lib/confidence.js");
  // Each region block must have all six layers. Use a wide pattern
  // (region-block + layer name on its own line with a colon) to stay
  // robust to formatting changes.
  for (const region of ["ca", "baja", "pnw", "tropical"]) {
    const block = src.split(`${region}: {`)[1]?.split(/^\s{0,4}\}/m)[0];
    assert.ok(block, `couldn't find ${region} block`);
    for (const layer of ["sst", "chl", "wind", "swell", "current", "viz"]) {
      assert.match(
        block,
        new RegExp(`${layer}:\\s*\\{`),
        `${region} block missing ${layer}`,
      );
    }
  }
});

test("Baja current is documented as 2/5 (no HFRNet)", () => {
  const src = read("src/lib/confidence.js");
  const bajaBlock = src.split("baja: {")[1]?.split(/^\s{0,4}\}/m)[0];
  assert.ok(bajaBlock, "couldn't find baja block");
  // Pin the score+source for the headline Baja gap.
  assert.match(bajaBlock, /current:\s*\{\s*score:\s*2/);
  assert.match(bajaBlock, /Tide \+ Ekman/);
});

test("CA current is documented as 4/5 (HFRNet observed)", () => {
  const src = read("src/lib/confidence.js");
  const caBlock = src.split("ca: {")[1]?.split(/^\s{0,4}\}/m)[0];
  assert.ok(caBlock, "couldn't find ca block");
  assert.match(caBlock, /current:\s*\{\s*score:\s*4/);
  assert.match(caBlock, /HFRNet/);
});

test("confidence.js exports the two public getters", () => {
  const src = read("src/lib/confidence.js");
  assert.match(src, /export function getLayerConfidence/);
  assert.match(src, /export function getRegionConfidence/);
});

test("ConfidenceDot is wired into DesktopLayout for every layer chip", () => {
  const src = read("src/components/DesktopLayout.jsx");
  assert.match(src, /import ConfidenceDot/);
  for (const layer of ["sst", "chl", "wind", "swell", "current", "viz"]) {
    assert.match(
      src,
      new RegExp(`ConfidenceDot[^>]*layer="${layer}"`),
      `DesktopLayout missing ConfidenceDot for layer="${layer}"`,
    );
  }
});

test("ConfidenceDot is wired into MobileSheet chips via L.id", () => {
  const src = read("src/components/MobileSheet.jsx");
  assert.match(src, /import ConfidenceDot/);
  assert.match(src, /<ConfidenceDot layer={L\.id}/);
});

test("TopBar renders the active-layer confidence badge", () => {
  const src = read("src/components/TopBar.jsx");
  // Switched from getRegionConfidence to getLayerConfidence(layer) so
  // the badge updates as the user clicks chips. The chip-strip dots
  // still cover the per-region overview.
  assert.match(src, /getLayerConfidence/);
  assert.match(src, /layer-confidence/);
  assert.match(src, /TopBar\(\{[^}]*layer/);  // accepts `layer` prop
});

test("App.jsx threads the active layer into TopBar", () => {
  const src = read("src/App.jsx");
  assert.match(src, /<TopBar[\s\S]{0,400}layer=\{layer\}/);
});

test("confidence.js exposes horizon-aware decay", () => {
  const src = read("src/lib/confidence.js");
  assert.match(src, /function horizonDecay/);
  // SST forecast persistence drop at +4
  assert.match(src, /sst[\s\S]*?horizonDays >= 4[\s\S]*?delta:\s*-2/);
  // Dynamical layers drop at day 5
  assert.match(src, /wind[\s\S]*?horizonDays >= 5[\s\S]*?delta:\s*-2/);
});

test("App.jsx computes activeHorizonDays + passes to TopBar", () => {
  const src = read("src/App.jsx");
  assert.match(src, /function layerHorizonDays/);
  assert.match(src, /activeHorizonDays\s*=\s*layerHorizonDays/);
  assert.match(src, /<TopBar[\s\S]{0,400}horizonDays=\{activeHorizonDays\}/);
});

test("TopBar passes horizonDays into getLayerConfidence", () => {
  const src = read("src/components/TopBar.jsx");
  assert.match(src, /getLayerConfidence\(layer,\s*\{\s*horizonDays\s*\}\)/);
});

// ---- Staleness awareness (lost-confidence surfacing) --------------------

test("layerDataAgeDays reads observation age from window dates", async () => {
  const { layerDataAgeDays } = await import("../src/lib/confidence.js");
  const mk = (date) => ({ layers: { sst: { windows: { "1d": { dates: [date] } } } } });
  const iso = (dAgo) => new Date(Date.now() - dAgo * 86400000).toISOString().slice(0, 10);
  assert.ok(layerDataAgeDays("sst", mk(iso(1))) < 2.5, "~1-day-old reads < 2.5 d");
  assert.ok(layerDataAgeDays("sst", mk(iso(6))) > 5, "~6-day-old reads > 5 d");
  assert.equal(layerDataAgeDays("sst", null), null, "no manifest → null");
  assert.equal(layerDataAgeDays("sst", { layers: {} }), null, "no layer → null");
});

test("confidence defines per-layer freshness budgets + caps the score when stale", () => {
  const src = read("src/lib/confidence.js");
  assert.match(src, /LAYER_FRESH_DAYS\s*=/, "missing LAYER_FRESH_DAYS budgets");
  assert.match(src, /sst:\s*4/, "sst budget should mirror the pipeline (4 d)");
  assert.match(src, /export function layerDataAgeDays/);
  // Staleness must CAP the ceiling, not just nudge the score by 1.
  assert.match(src, /Math\.min\(score,\s*st\.cap\)/);
});

test("region confidence is staleness-aware (uses live layer scores)", () => {
  const src = read("src/lib/confidence.js");
  const regionFn = src.split("export function getRegionConfidence")[1] || "";
  assert.match(regionFn, /getLayerConfidence\(/, "getRegionConfidence must use live scores");
});

// ---- Source provenance (fallback awareness) -----------------------------

test("confidence drops the score + names the source on a fallback", () => {
  const src = read("src/lib/confidence.js");
  // dynamicModulation must read the pipeline's source_fallback flag, drop the
  // score by 1, and tell the user which backup source they're looking at.
  assert.match(
    src,
    /info\.source_fallback[\s\S]{0,80}delta\s*-=\s*1[\s\S]{0,140}via \$\{info\.source/,
    "source_fallback must drop the score and surface the source name",
  );
});

test("pipeline records source provenance (blender + build_layer)", () => {
  // chl: provenance lives in the BLENDER (build_blended_chl), not build_layer —
  // a fallback added to LAYERS["chl"].fallbacks would be a silent no-op.
  const chlBlend = read("pipeline/chl_blend.py");
  assert.match(chlBlend, /erdVHNchla1day/, "chl needs a no-auth blender fallback source");
  assert.match(chlBlend, /upwell\.pfeg\.noaa\.gov/, "raw-VIIRS chl fallback lives on upwell, not coastwatch.pfeg");
  assert.match(chlBlend, /source_fallback/, "blender must flag a fallback-dominated chl blend");

  // sst/kd490: build_layer records which source served the freshest day.
  const fetchPy = read("pipeline/fetch.py");
  assert.match(fetchPy, /_LAYER_SOURCE/, "build_layer must record the serving source");
  assert.match(fetchPy, /source_fallback/, "build_layer must flag a fallback source");
  // The retired OISST id must not be USED as a dataset (it 404s -> silent
  // no-op fallback); the explanatory comment may still mention it.
  assert.doesNotMatch(
    fetchPy,
    /"dataset":\s*"ncdcOisst21NrtAgg_LonPM180"/,
    "use the live base OISST id + lng_offset_360, not the retired _LonPM180",
  );
  assert.match(fetchPy, /"dataset":\s*"ncdcOisst21NrtAgg"/, "OISST last-resort SST fallback present");
  assert.match(fetchPy, /lng_offset_360/, "base OISST (0..360) needs the longitude offset");
});
