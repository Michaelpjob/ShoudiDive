// Contract checks for the Field Reports layer (PR-3). String-based, like
// confidence.test.js — pins the wiring without booting React.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(resolve(repoRoot, rel), "utf8");

test("FieldReportsLayer exports the layer + the recent-obs loader", () => {
  const src = read("src/components/FieldReportsLayer.jsx");
  assert.match(src, /export default function FieldReportsLayer/);
  assert.match(src, /export function loadRecentObservations/);
  assert.match(src, /observations_recent\.json/);
});

test("MapShell mounts the layer + popup, gated on fieldReportsOn", () => {
  const src = read("src/components/MapShell.jsx");
  assert.match(src, /import FieldReportsLayer/);
  assert.match(src, /import FieldReportsPopup/);
  assert.match(src, /<FieldReportsLayer[\s\S]*?active=\{fieldReportsOn\}/);
  assert.match(src, /selectedObservation &&[\s\S]*?<FieldReportsPopup/);
});

test("Field Reports is dark-launched (pref defaults off)", () => {
  const src = read("src/contexts/PrefsContext.jsx");
  assert.match(src, /fieldReportsOn:\s*false/);
});

test("both layouts expose a Field Reports toggle", () => {
  const desk = read("src/components/DesktopLayout.jsx");
  const mob = read("src/components/MobileSheet.jsx");
  assert.match(desk, /updateFieldReportsOn\(!fieldReportsOn\)/);
  assert.match(mob, /setFieldReportsOn\(!fieldReportsOn\)/);
});
