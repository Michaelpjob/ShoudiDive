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

test("TopBar renders the region confidence badge", () => {
  const src = read("src/components/TopBar.jsx");
  assert.match(src, /getRegionConfidence/);
  assert.match(src, /region-confidence/);
});
