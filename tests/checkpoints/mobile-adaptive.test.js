/**
 * cp-mobile-adaptive — mobile/desktop responsive contracts.
 *
 * Catches the regression class of "this only fails on a phone":
 *   - mobile breakpoint media-query in the CSS
 *   - gesture-isolation selector list complete
 *   - safe-area + dvh used SOMEWHERE so the iPhone notch + Safari
 *     toolbar don't eat UI
 *
 * Source-grepping for specific implementation details was too
 * brittle on the previous iteration. This file now tests the
 * INVARIANTS that don't churn — a missing dvh OR a missing gesture
 * selector are bugs regardless of the surrounding code structure.
 *
 * Non-scope:
 *   - Native iOS Safari runtime quirks (need real device; live-cp-render
 *     gives partial coverage via headless Chrome mobile emulation)
 *   - Pixel-perfect mobile layout (no visual regression baseline yet)
 *   - useTimelineDrag internals (dropped — overlapped with the visible
 *     drag behavior cp-visual-paint already exercises end-to-end)
 */
import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";


const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

function read(rel) {
  return readFileSync(resolve(REPO_ROOT, rel), "utf8");
}

const css    = read("src/styles/app.css");
const guards = read("src/lib/mapInteractionGuards.js");


// ---------------------------------------------------------------------
// Mobile breakpoint must be present somewhere in the CSS.
// ---------------------------------------------------------------------

test("cp-mobile-adaptive: CSS uses 1024px as the mobile breakpoint", () => {
  // Anywhere in the file. The exact `@media` form varies; what
  // matters is the 1024px width threshold being present.
  assert.match(
    css, /1024px/,
    "CSS should use 1024px as the mobile breakpoint to align with " +
    "useIsMobile. Without it, iPad-class touch devices in landscape " +
    "fall onto desktop UI and break.",
  );
});


test("cp-mobile-adaptive: CSS uses pointer:coarse media hint", () => {
  assert.match(
    css, /pointer:\s*coarse/,
    "CSS should pair the width breakpoint with `(pointer: coarse)` " +
    "so wide-but-touch devices (like an iPad in landscape) still get " +
    "mobile UI.",
  );
});


// ---------------------------------------------------------------------
// Gesture isolation — the SELECTOR LIST in
// src/lib/mapInteractionGuards.js. If a new panel ships without
// being added here, dragging it pans the map underneath.
// ---------------------------------------------------------------------

test("cp-mobile-adaptive: gesture-isolation selector list lives in mapInteractionGuards.js", () => {
  // Source of truth. If this file is removed or gutted, gesture
  // isolation is broken — the rule of thumb is "the constant
  // MAP_GESTURE_CHILD_SELECTOR must exist."
  assert.match(
    guards, /MAP_GESTURE_CHILD_SELECTOR/,
    "src/lib/mapInteractionGuards.js must export MAP_GESTURE_CHILD_SELECTOR",
  );
});


test("cp-mobile-adaptive: gesture-isolation list includes every interactive panel/scrubber", () => {
  // Must include each known scrubber + panel. Not a complete
  // exhaustive list — just the ones that broke in the past when
  // they were missing.
  const required = [
    ".wind-timeline",
    ".swell-timeline",
    ".sst-timeline",
    ".panel",
    ".moon-widget",
    ".zoom-ctl",
  ];
  for (const sel of required) {
    assert.ok(
      guards.includes(`"${sel}"`),
      `mapInteractionGuards.js must include "${sel}" in MAP_GESTURE_CHILD_SELECTOR. ` +
      `Touch events inside that element will pan the map underneath if missing.`,
    );
  }
});


// ---------------------------------------------------------------------
// Safe-area inset + dynamic viewport height
// ---------------------------------------------------------------------

test("cp-mobile-adaptive: app.css uses env(safe-area-inset-top) somewhere", () => {
  assert.match(
    css, /env\(safe-area-inset-top\)/,
    "app.css must use env(safe-area-inset-top) on the topbar / map " +
    "stage — without it, UI lands underneath the iPhone notch / " +
    "Dynamic Island.",
  );
});


test("cp-mobile-adaptive: app.css uses 100dvh for viewport-tracking layout", () => {
  // 100vh is the static viewport height. iOS Safari's collapsing
  // toolbar makes 100vh land BEHIND the toolbar, eating bottom UI.
  // 100dvh tracks the dynamic viewport.
  assert.match(
    css, /100dvh/,
    "app.css must use 100dvh somewhere (typically on .app or body) " +
    "or iOS Safari's collapsing toolbar eats bottom UI. Keep " +
    "`height: 100vh; height: 100dvh;` as a graceful fallback for " +
    "ancient browsers.",
  );
});
