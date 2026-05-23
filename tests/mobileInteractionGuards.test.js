import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  isMapGestureChildTarget,
  layerHasTopTimeline,
  shouldPinMapTap,
} from "../src/lib/mapInteractionGuards.js";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function targetInside(selector) {
  return {
    closest(query) {
      return query.includes(selector) ? { selector } : null;
    },
  };
}

const freshTap = (overrides = {}) => ({
  startX: 120,
  startY: 90,
  startTime: 1_000,
  moved: false,
  ...overrides,
});

test("map gesture guard recognizes all mobile timeline and control surfaces", () => {
  for (const selector of [
    ".wind-timeline",
    ".sst-timeline",
    ".swell-timeline",
    ".current-timeline",
    ".mobile-shell",
    ".panel",
    ".moon-widget",
    ".zoom-ctl",
  ]) {
    assert.equal(isMapGestureChildTarget(targetInside(selector)), true, selector);
  }
  assert.equal(isMapGestureChildTarget(targetInside(".map-svg")), false);
  assert.equal(isMapGestureChildTarget(null), false);
});

test("top-slider layers suppress tap-to-pin inside the mobile guard band", () => {
  for (const [layer, hasSstHistory] of [
    ["sst", true],
    ["wind", false],
    ["swell", false],
    ["current", false],
  ]) {
    assert.equal(
      shouldPinMapTap({
        tap: freshTap({ startY: 80 }),
        layer,
        hasSstHistory,
        now: 1_100,
      }),
      false,
      layer
    );
    assert.equal(layerHasTopTimeline(layer, hasSstHistory), true, layer);
  }
});

test("map taps still pin below the guard band or on layers without top sliders", () => {
  assert.equal(
    shouldPinMapTap({ tap: freshTap({ startY: 180 }), layer: "wind", now: 1_100 }),
    true
  );
  assert.equal(
    shouldPinMapTap({ tap: freshTap({ startY: 80 }), layer: "chl", now: 1_100 }),
    true
  );
  assert.equal(
    shouldPinMapTap({
      tap: freshTap({ startY: 80 }),
      layer: "sst",
      hasSstHistory: false,
      now: 1_100,
    }),
    true
  );
});

test("moved, stale, and control-surface taps never create map pins", () => {
  assert.equal(
    shouldPinMapTap({ tap: freshTap({ moved: true }), layer: "chl", now: 1_100 }),
    false
  );
  assert.equal(
    shouldPinMapTap({ tap: freshTap(), layer: "chl", now: 1_400 }),
    false
  );
  assert.equal(
    shouldPinMapTap({
      tap: freshTap({ startY: 180 }),
      layer: "chl",
      now: 1_100,
      target: targetInside(".mobile-shell"),
    }),
    false
  );
});

test("mobile timeline regression wiring stays in place", () => {
  // 2026-05-23 Stage 4: DesktopView extracted from App.jsx into
  // src/components/MapShell.jsx — the gesture guards + hover overlay
  // gating live with the map JSX in MapShell now.
  const mapShell = readFileSync(resolve(repoRoot, "src/components/MapShell.jsx"), "utf8");
  const timelineHook = readFileSync(resolve(repoRoot, "src/components/useTimelineDrag.js"), "utf8");
  const currentTimeline = readFileSync(resolve(repoRoot, "src/components/CurrentTimeline.jsx"), "utf8");

  assert.match(mapShell, /!\s*isMobile\s*&&\s*hover\s*&&\s*\(/);
  assert.match(mapShell, /isMapGestureChildTarget\(e\.target\)/);
  assert.match(mapShell, /shouldPinMapTap\(\{\s*tap,\s*layer,/);
  assert.match(timelineHook, /onPointerDown[\s\S]*?e\.stopPropagation\(\)/);
  assert.match(timelineHook, /onPointerMove[\s\S]*?e\.stopPropagation\(\)/);
  assert.match(timelineHook, /onPointerUp[\s\S]*?e\.stopPropagation\(\)/);
  assert.match(currentTimeline, /onPointerDown[\s\S]*?e\.stopPropagation\(\)/);
  assert.match(currentTimeline, /onPointerMove[\s\S]*?e\.stopPropagation\(\)/);
  assert.match(currentTimeline, /onPointerUp[\s\S]*?e\.stopPropagation\(\)/);
});
