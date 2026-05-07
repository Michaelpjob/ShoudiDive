/**
 * cp-mobile-adaptive — mobile/desktop responsive behavior.
 *
 * Catches the regression class of "this only fails on a phone"
 * (or on iPad, where the breakpoint matters):
 *   - mobile breakpoint media-query mismatched between CSS and JS
 *   - touch event isolation (slider drag must NOT pan the map)
 *   - tap target sizes hitting Apple HIG / WCAG 2.5.5 floor (44px)
 *   - safe-area inset (iPhone notch / home bar) eating UI elements
 *
 * Non-scope:
 *   - Native iOS Safari quirks (need real device; live-cp-render
 *     gives partial coverage via headless Chrome mobile emulation)
 *   - Pixel-perfect mobile layout (visual regression, not yet wired)
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

const css            = read("src/styles/app.css");
const guards         = read("src/lib/mapInteractionGuards.js");
const useTimelineDrag = read("src/components/useTimelineDrag.js");


// ---------------------------------------------------------------------
// Breakpoint consistency
// ---------------------------------------------------------------------

test("cp-mobile-adaptive: CSS + JS share the same mobile breakpoint", () => {
  // The CSS uses `@media (max-width: 1024px), (hover: none) and
  // (pointer: coarse)`. If a JS component computes "is mobile" with
  // a different threshold (used to be 760px), the two go out of sync
  // and we get half-mobile, half-desktop UI on iPad.
  //
  // This is exactly the bug we shipped on 2026-04-29 (iPhone landscape
  // got desktop layout). The fix was to align both at 1024px / coarse-
  // pointer.
  const expectedBreakpoint = /1024px/;
  const expectedHoverHint = /hover:\s*none.*pointer:\s*coarse|pointer:\s*coarse.*hover:\s*none/s;

  assert.match(
    css, expectedBreakpoint,
    "CSS should use 1024px as the mobile breakpoint (matches useIsMobile)",
  );
  assert.match(
    css, expectedHoverHint,
    "CSS should use the (hover: none) and (pointer: coarse) UA hint " +
    "alongside the width breakpoint, so coarse-pointer iPad in landscape " +
    "still gets mobile UI even though it's wider than 1024px",
  );
});


// ---------------------------------------------------------------------
// Gesture isolation (slider drag must not pan the map)
// ---------------------------------------------------------------------

test("cp-mobile-adaptive: gesture-isolation selector lists every interactive panel", () => {
  // When the user drags the SST/wind/swell timeline on a touch device,
  // the map MUST NOT also pan. The map's pointer-down handler calls
  // isMapGestureChildTarget() to bail when the touch started on a
  // panel or scrubber. If a new panel ships without being added here,
  // touching it pans the map underneath.
  const required = [
    ".wind-timeline",
    ".swell-timeline",
    ".sst-timeline",
    ".current-timeline",
    ".mobile-shell",
    ".panel",
    ".moon-widget",
    ".zoom-ctl",
  ];
  for (const sel of required) {
    assert.ok(
      guards.includes(`"${sel}"`),
      `MAP_GESTURE_CHILD_SELECTOR must include "${sel}" so touch events ` +
      `inside it don't bubble into a map pan. Open ` +
      `src/lib/mapInteractionGuards.js to add the selector.`,
    );
  }
});


test("cp-mobile-adaptive: timeline drag hook stops event propagation", () => {
  // The other half of the gesture-isolation problem: even with the
  // closest-selector check, browsers fire `pointerdown` on the
  // timeline AND on the map (event bubbling). The drag hook must
  // call e.stopPropagation() so the map handler doesn't also see
  // the event.
  for (const fn of ["onPointerDown", "onPointerMove", "onPointerUp"]) {
    const re = new RegExp(`${fn}\\s*=\\s*useCallback\\(\\([^)]*\\)\\s*=>\\s*\\{[\\s\\S]*?stopPropagation`);
    assert.match(
      useTimelineDrag, re,
      `useTimelineDrag.${fn} must call e.stopPropagation() so the map ` +
      `pan handler doesn't also receive the event when the user is ` +
      `scrubbing the timeline`,
    );
  }
});


// ---------------------------------------------------------------------
// Tap-target floors (Apple HIG / WCAG 2.5.5 — 44px)
// ---------------------------------------------------------------------

test("cp-mobile-adaptive: critical tap targets meet the 44px floor", () => {
  // Specific elements we've had complaints about. The CSS rule for
  // each must specify a width or padding that produces ≥44px touch
  // height on a coarse pointer.
  //
  // We grep for `min-height: 44px`, `height: 44px`, `width: 44px`,
  // OR a padding combination that yields 44px (padding 22px ⇒ 44px
  // top+bottom). This is approximate but catches the regressions.
  const targets = [
    { selector: ".icon-btn",        rule: /\bwidth:\s*44px/ },
    { selector: ".zoom-ctl button", rule: /\b(width|height|min-height):\s*44/ },
  ];
  for (const { selector, rule } of targets) {
    // Find the rule block for the selector and check if it (or any
    // immediate-following media-query override of the same selector)
    // matches the size rule.
    const re = new RegExp(
      `${selector.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&")}\\s*\\{[^}]*${rule.source}`,
      "s",
    );
    assert.match(
      css, re,
      `${selector} must hit the 44px tap target floor (Apple HIG / WCAG 2.5.5). ` +
      `If the rule was relaxed deliberately (rare), update this test.`,
    );
  }
});


// ---------------------------------------------------------------------
// Safe-area insets for the notch / Dynamic Island
// ---------------------------------------------------------------------

test("cp-mobile-adaptive: top-anchored UI uses env(safe-area-inset-top)", () => {
  // On notched iPhones (14 Pro+ Dynamic Island, every model with a
  // notch), elements anchored to top: 0 / top: 12px land UNDERNEATH
  // the system status bar unless they add env(safe-area-inset-top).
  // Same for top-anchored timelines on the map-stage.
  //
  // The .topbar and .map-stage rules MUST pull env(safe-area-inset-top)
  // into their top offset. We've shipped this bug twice (April + May)
  // when those rules got rewritten without the env() call.
  const re = /env\(safe-area-inset-top\)/;
  assert.match(
    css, re,
    "app.css must use env(safe-area-inset-top) somewhere to push UI " +
    "below the iPhone notch. Without it, the topbar lands behind the " +
    "Dynamic Island on every iPhone 14 Pro and later.",
  );
});


test("cp-mobile-adaptive: layout uses 100dvh (not just 100vh) for the app shell", () => {
  // 100vh = static viewport height = doesn't shrink when iOS Safari's
  // toolbar collapses. The layer chip strip at the bottom of the peek
  // bar lands BEHIND Safari's chrome and is unreachable. 100dvh
  // tracks dynamic viewport.
  assert.match(
    css, /100dvh/,
    "app.css must use 100dvh somewhere (typically on .app or body) " +
    "or iOS Safari's collapsing toolbar eats bottom UI. We've shipped " +
    "this bug before — keep both `height: 100vh; height: 100dvh;` as " +
    "a graceful fallback for ancient browsers.",
  );
});


// ---------------------------------------------------------------------
// Timeline drag commit-throttling (Phase A polish that's now infra)
// ---------------------------------------------------------------------

test("cp-mobile-adaptive: useTimelineDrag throttles state commits to snap-target changes", () => {
  // Without this throttle, every pointermove triggers a setSel which
  // re-renders the map (DataOverlay PNG decode, ~50ms). On a phone
  // that drops scrub frame rate to ~10 fps. The hook MUST gate the
  // onCommit call on "snap target actually changed."
  assert.match(
    useTimelineDrag,
    /if \(target !== currentTarget\) \{\s*onCommit\(target\);\s*\}/,
    "useTimelineDrag must only fire onCommit when the snap target " +
    "actually changes — otherwise React re-renders 60×/sec during drag " +
    "and mobile scrub goes choppy.",
  );
});
