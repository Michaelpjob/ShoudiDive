// S1, NO DISPLAY FABRICATION  (docs/STRICT-SCIENCE.md)
//
// Observed-only layers (chl, viz) must never be interpolated, smoothed,
// gap-filled, or "bloomed" into cells where nothing was measured. Blank
// means no data, not clear water. A blank cell is honest; a painted-in
// guess is the exact failure that put "clean" over Northeast Bank when the
// water was green (a clean reading bloomed 15-30km across a gap).
//
// This is a STATIC tripwire: it reads DataOverlay.jsx as text and asserts
// no interpolation set is keyed to an observed-only layer, and that those
// layers stay on the nearest-neighbour (pixelated, blank-gap) render path.
// It cannot prove the runtime paints correctly, but it makes the specific
// regression (re-adding a chl gradient / dots / smooth-fill) fail CI, which
// is what it exists to prevent.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OVERLAY = resolve(__dirname, "../../src/components/DataOverlay.jsx");
const src = readFileSync(OVERLAY, "utf8");

// The layers whose values are sparse real observations, blank elsewhere.
const OBSERVED_ONLY = ["chl", "viz"];

// Any const holding a Set of layers that triggers interpolation / synthesis.
// If a future change introduces a new one, name it here so the guard covers it.
const INTERPOLATION_SET_RE =
  /(GRADIENT|DOTTED|SMOOTH|BLOOM|INTERP|FILL|IDW|SPREAD)\w*_LAYERS\s*=\s*new Set\(\[([^\]]*)\]\)/g;

function membersOf(listBody) {
  return [...listBody.matchAll(/["'`]([a-z0-9_]+)["'`]/gi)].map((m) => m[1]);
}

test("S1: no observed-only layer is in any interpolation/synthesis set", () => {
  let match;
  const offenders = [];
  while ((match = INTERPOLATION_SET_RE.exec(src)) !== null) {
    const constName = match[1];
    const members = membersOf(match[2]);
    for (const layer of members) {
      if (OBSERVED_ONLY.includes(layer)) {
        offenders.push(`${constName}_LAYERS contains observed-only "${layer}"`);
      }
    }
  }
  assert.equal(
    offenders.length,
    0,
    `Observed-only layers must not be interpolated/bloomed. Offenders: ${offenders.join(
      "; ",
    )}. Blank the gaps instead, see docs/STRICT-SCIENCE.md (S1).`,
  );
});

test("S1: observed-only layers render nearest-neighbour (pixelated, blank gaps)", () => {
  const m = src.match(/PIXELATED_LAYERS\s*=\s*new Set\(\[([^\]]*)\]\)/);
  assert.ok(m, "PIXELATED_LAYERS set not found in DataOverlay.jsx");
  const pixelated = membersOf(m[1]);
  for (const layer of OBSERVED_ONLY) {
    assert.ok(
      pixelated.includes(layer),
      `Observed-only layer "${layer}" must be in PIXELATED_LAYERS so unmeasured ` +
        `cells stay blank rather than being smoothed into neighbours.`,
    );
  }
});
