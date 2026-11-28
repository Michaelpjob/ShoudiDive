// "Open on today" contract for the timeline default selections
// (src/lib/today.js). The app must open every timeline on the user's
// current day — and, for bucketed timelines, on the bucket that contains
// the current local hour — never on the oldest day of the window.
//
// All helpers take an injectable `now` so these run on a fixed clock.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  findCurrentBucket,
  findTodayDay,
  summaryTzHour,
  summaryTzToday,
  todaySelection,
} from "../src/lib/today.js";

const here = dirname(fileURLToPath(import.meta.url));
const read = (p) => readFileSync(resolve(here, "..", p), "utf8");

// Live wind/summary.json shape (2026-10-30), trimmed.
const BUCKETS_DEF = [
  { name: "predawn", start_hour_local: 4, end_hour_local: 6 },
  { name: "morning", start_hour_local: 6, end_hour_local: 10 },
  { name: "midday", start_hour_local: 10, end_hour_local: 14 },
  { name: "afternoon", start_hour_local: 14, end_hour_local: 19 },
  { name: "evening", start_hour_local: 19, end_hour_local: 21 },
];
const ALL = ["predawn", "morning", "midday", "afternoon", "evening"];
const day = (i, date, buckets = ALL) => ({
  day: i, date, buckets: buckets.map((b) => ({ bucket: b })),
});
const windLike = (days, tz = "America/Los_Angeles") => ({ tz, buckets_def: BUCKETS_DEF, days });

// 2026-10-30 14:19 PDT == 21:19Z (Friday afternoon in California).
const FRI_AFTERNOON = new Date("2026-10-30T21:19:00Z");

test("summaryTzToday resolves the date in the summary's own tz", () => {
  const la = { tz: "America/Los_Angeles" };
  // 02:00Z on Oct 31 is still Oct 30 in California.
  assert.equal(summaryTzToday(la, new Date("2026-10-31T02:00:00Z")), "2026-10-30");
  assert.equal(summaryTzToday({ tz: "UTC" }, new Date("2026-10-31T02:00:00Z")), "2026-10-31");
  // Missing tz defaults to Pacific.
  assert.equal(summaryTzToday({}, new Date("2026-10-31T02:00:00Z")), "2026-10-30");
});

test("summaryTzHour is the local wall-clock hour, 0-23", () => {
  assert.equal(summaryTzHour({ tz: "America/Los_Angeles" }, FRI_AFTERNOON), 14);
  assert.equal(summaryTzHour({ tz: "UTC" }, FRI_AFTERNOON), 21);
  // Midnight must come back as 0, not 24.
  assert.equal(summaryTzHour({ tz: "UTC" }, new Date("2026-10-30T00:30:00Z")), 0);
});

test("findTodayDay matches the day whose date is today in summary tz", () => {
  const s = { tz: "UTC", days: [{ date: "2026-10-27", slot: "f0" }, { date: "2026-10-30", slot: "f3" }] };
  assert.equal(findTodayDay(s, FRI_AFTERNOON)?.slot, "f3");
  assert.equal(findTodayDay({ tz: "UTC", days: [{ date: "2026-10-27" }] }, FRI_AFTERNOON), null);
  assert.equal(findTodayDay(null, FRI_AFTERNOON), null);
});

test("findCurrentBucket picks the bucket containing the current hour", () => {
  const s = windLike([day(0, "2026-10-30")]);
  assert.equal(findCurrentBucket(s, s.days[0], FRI_AFTERNOON), "afternoon");
  assert.equal(findCurrentBucket(s, s.days[0], new Date("2026-10-30T15:00:00Z")), "morning"); // 08:00 PDT
  assert.equal(findCurrentBucket(s, s.days[0], new Date("2026-10-30T19:30:00Z")), "midday");  // 12:30 PDT
});

test("findCurrentBucket before the first window takes the first bucket, after the last takes the last", () => {
  const s = windLike([day(0, "2026-10-30")]);
  assert.equal(findCurrentBucket(s, s.days[0], new Date("2026-10-30T09:00:00Z")), "predawn"); // 02:00 PDT
  assert.equal(findCurrentBucket(s, s.days[0], new Date("2026-10-31T06:30:00Z")), "evening"); // 23:30 PDT
});

test("findCurrentBucket walks to the next bucket the day still carries", () => {
  // Today's elapsed buckets dropped by the pipeline: only midday+ left.
  const s = windLike([day(0, "2026-10-30", ["midday", "afternoon", "evening"])]);
  assert.equal(findCurrentBucket(s, s.days[0], new Date("2026-10-30T15:00:00Z")), "midday"); // 08:00 PDT
  // Nothing at or after the current window: latest the day has.
  const late = windLike([day(0, "2026-10-30", ["predawn", "morning"])]);
  assert.equal(findCurrentBucket(late, late.days[0], FRI_AFTERNOON), "morning");
});

test("findCurrentBucket without buckets_def falls back to the first bucket; empty day → null", () => {
  const s = { tz: "America/Los_Angeles", days: [day(0, "2026-10-30", ["midday", "evening"])] };
  assert.equal(findCurrentBucket(s, s.days[0], FRI_AFTERNOON), "midday");
  assert.equal(findCurrentBucket(s, { buckets: [] }, FRI_AFTERNOON), null);
  assert.equal(findCurrentBucket(s, null, FRI_AFTERNOON), null);
});

test("todaySelection opens on today at the current time of day", () => {
  // Window that starts BEFORE today (hindcast days first), like sst5d's
  // -3..+3 layout — must land on today's index, not day 0.
  const s = windLike([
    day(-2, "2026-10-28"), day(-1, "2026-10-29"), day(0, "2026-10-30"), day(1, "2026-10-31"),
  ]);
  assert.deepEqual(todaySelection(s, FRI_AFTERNOON), { day: 0, bucket: "afternoon" });
  // Summary whose window no longer reaches today → null (caller falls
  // back to best_window), never a silently-wrong day.
  const stale = windLike([day(0, "2026-10-27"), day(1, "2026-10-28")]);
  assert.equal(todaySelection(stale, FRI_AFTERNOON), null);
});

test("useTimelineSelections applies the default selection on each summary's first load", () => {
  // The reconcile pass only replaced selections that had NO data, and the
  // placeholder f0 / day-0 selections always have data, so without a
  // first-load pass the app opened on the oldest day of every window.
  const hook = read("src/hooks/useTimelineSelections.js");
  assert.match(hook, /const openedRef = useRef\(\{\}\)/);
  for (const key of ["sstHistory", "sstForecast", "wind", "swell", "current"]) {
    assert.match(hook, new RegExp(`firstLoad\\("${key}", \\w+\\) \\|\\|`), `first-load gate for ${key}`);
  }
  // SST timeline marks the current day so the window reads as hindcast
  // / today / forecast rather than an anonymous run of dates.
  const sst = read("src/components/SstTimeline.jsx");
  assert.match(sst, /isToday \? "Today" : fmtWeekday\(d\.date\)/);
});
