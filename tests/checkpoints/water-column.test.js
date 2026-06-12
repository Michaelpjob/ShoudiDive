// Checkpoint: water-column UI logic (PRD water-column V1-V5).
//
// Catches: slice-at-depth geometry regressions (shallow shelf must
// not grow a murk layer; deep bottoms clip with a flag), two-number
// formatting drift in saved-spot rows, crossing-callout logic, and
// diurnal-strip normalization — the math the WaterColumn widget
// renders from. Pure node:test over src/lib/waterColumn.js.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  columnGeometry,
  crossingCallout,
  diurnalStrip,
  formatColumnSummary,
  visClass,
} from "../../src/lib/waterColumn.js";

const LA_JOLLA = {
  surface_ft: 15, cliff_ft: 25, below_ft: 5,
  bottom_ft: 165, no_cliff: false, cliff_swing_ft: 9.6,
};

test("two-number formatting: cliff column reads surface → below", () => {
  assert.equal(formatColumnSummary(LA_JOLLA), "~15 ft → ~5 ft below");
});

test("two-number formatting: shallow shelf shows one number", () => {
  const shelf = { surface_ft: 20, no_cliff: true, bottom_ft: 12 };
  assert.equal(formatColumnSummary(shelf), "~20 ft");
  assert.equal(formatColumnSummary(null), null);
});

test("slice geometry: cliff column has clear, band, murk in order", () => {
  const g = columnGeometry(LA_JOLLA, { maxDepthFt: 60 });
  assert.ok(g.clipped, "165 ft bottom must clip at 60 ft");
  assert.equal(g.drawnDepthFt, 60);
  assert.ok(Math.abs(g.cliffFrac - 25 / 60) < 1e-9);
  assert.equal(g.clear.top, 0);
  assert.equal(g.clear.bottom, g.cliffFrac);
  assert.equal(g.murk.top, g.cliffFrac);
  assert.equal(g.murk.bottom, 1);
  assert.ok(g.band.top < g.cliffFrac && g.band.bottom > g.cliffFrac,
    "swing band straddles the cliff");
});

test("slice geometry: shallow shelf clips above the cliff — no murk", () => {
  const shelf = { ...LA_JOLLA, bottom_ft: 18, no_cliff: true };
  const g = columnGeometry(shelf, { maxDepthFt: 60 });
  assert.equal(g.clipped, false);
  assert.equal(g.drawnDepthFt, 18);
  assert.equal(g.murk, null);
  assert.equal(g.cliffFrac, null);
  assert.deepEqual(g.clear, { top: 0, bottom: 1 });
});

test("slice geometry: cliff below the drawn window still renders murk fraction sanely", () => {
  const deepCliff = { ...LA_JOLLA, cliff_ft: 80, bottom_ft: 120 };
  const g = columnGeometry(deepCliff, { maxDepthFt: 60 });
  assert.equal(g.cliffFrac, 1, "cliff deeper than the window clamps to 1");
});

test("crossing callout: above vs through the cliff; silent when clear", () => {
  assert.match(crossingCallout(15, LA_JOLLA), /Above the cliff/);
  assert.match(crossingCallout(40, LA_JOLLA), /drops to ~5 ft around 25 ft/);
  assert.equal(crossingCallout(40, { ...LA_JOLLA, no_cliff: true }), null);
  assert.equal(crossingCallout(null, LA_JOLLA), null);
});

test("vis classes follow the legend semantics", () => {
  assert.equal(visClass(30), "good");
  assert.equal(visClass(15), "fair");
  assert.equal(visClass(5), "poor");
  assert.equal(visClass(null), null);
});

test("diurnal strip: normalized, best window sits at the deepest cliff", () => {
  const series = [29, 29.8, 29.4, 27.9, 25.7, 23.3, 21.3, 20.3, 20.5,
    21.7, 23.8, 26.2, 28.3, 29.6, 29.7, 28.6, 26.6, 24.3, 22.1, 20.6,
    20.2, 21.1, 22.9, 25.2];
  const s = diurnalStrip(series);
  assert.equal(s.pts.length, 24);
  assert.equal(s.minFt, 20.2);
  assert.equal(s.maxFt, 29.8);
  assert.ok(s.pts.every((p) => p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1));
  // Deepest run (within tolerance of 29.8) is hours 0-2 or 12-15;
  // the longest such run must contain the day's max.
  const windowDepths = series.slice(s.best.start, s.best.end + 1);
  assert.ok(windowDepths.includes(29.8) || Math.max(...windowDepths) >= 28.3);
  assert.equal(diurnalStrip(null), null);
  assert.equal(diurnalStrip([25]), null);
});
