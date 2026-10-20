// Navy closures freshness guard (src/lib/closures.js). A stale schedule must
// never present a past date as "Today" or report a zone as clear.

import assert from "node:assert/strict";
import { test } from "node:test";
import { normalizeClosures } from "../src/lib/closures.js";

const week = (start) => {
  const out = [];
  const d = new Date(start + "T12:00:00Z");
  for (let i = 0; i < 7; i++) {
    out.push(new Date(d.getTime() + i * 86400000).toISOString().slice(0, 10));
  }
  return out;
};
const fc = (start) => ({ tz: "America/Los_Angeles", dates: week(start), features: [] });

test("a current file keeps all 7 days and is not stale", () => {
  const n = normalizeClosures(fc("2026-10-19"), "2026-10-19");
  assert.equal(n.stale, false);
  assert.equal(n.dates.length, 7);
  assert.equal(n.dates[0], "2026-10-19");
});

test("a file from yesterday drops the past day and starts at today", () => {
  const n = normalizeClosures(fc("2026-10-18"), "2026-10-19");
  assert.equal(n.stale, false);
  assert.equal(n.dates[0], "2026-10-19");
  assert.equal(n.dates.length, 6);
});

test("a file that no longer reaches today is stale with no days", () => {
  const n = normalizeClosures(fc("2026-06-29"), "2026-10-19");
  assert.equal(n.stale, true);
  assert.deepEqual(n.dates, []);
});

test("missing file stays null", () => {
  assert.equal(normalizeClosures(null, "2026-10-19"), null);
});
