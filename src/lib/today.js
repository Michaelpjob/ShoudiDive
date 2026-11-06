// Copyright (c) 2026 Michael Job. All rights reserved.
// Licensed under the PolyForm Strict License 1.0.0; see LICENSE.md.
// Shared "today" helpers for timeline default-selection logic.
//
// Wind / swell / current / SST summaries each carry a `tz` field
// (America/Los_Angeles for CA + Baja + PNW; UTC for tropical/SST5d).
// Their per-day entries have a `date: "YYYY-MM-DD"` string anchored to
// that tz. To make every timeline default to "today" on app load
// (rather than to `day: 0` which can lag a day when the cycle anchor
// is yesterday-PT), look up today's date in the summary's own tz and
// find the matching day.
//
// `now` is injectable everywhere so the pure helpers are testable with
// a fixed clock; production callers leave it at the default.

const DEFAULT_TZ = "America/Los_Angeles";

function tzOf(summary) {
  return summary?.tz || DEFAULT_TZ;
}

export function summaryTzToday(summary, now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: tzOf(summary), year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now); // "YYYY-MM-DD"
}

// Wall-clock hour (0-23) in the summary's tz. Used to open a bucketed
// timeline on the bucket that is happening right now, not on pre-dawn.
export function summaryTzHour(summary, now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tzOf(summary), hour: "numeric", hourCycle: "h23",
  }).formatToParts(now);
  const h = Number(parts.find((p) => p.type === "hour")?.value);
  return Number.isFinite(h) ? h % 24 : 0;
}

export function findTodayDay(summary, now = new Date()) {
  if (!summary?.days?.length) return null;
  const today = summaryTzToday(summary, now);
  return summary.days.find((d) => d.date === today) || null;
}

// Pick the bucket of `day` that contains the current local hour, using
// the summary's own `buckets_def` ([{name, start_hour_local,
// end_hour_local}]). Before the first window → the first bucket the day
// still has; after the last → the last one; a bucket that has already
// been dropped from the day (past) → the next one that still exists.
// Returns a bucket name or null when the day has no buckets at all.
export function findCurrentBucket(summary, day, now = new Date()) {
  const have = (day?.buckets || []).map((b) => b.bucket).filter(Boolean);
  if (!have.length) return null;
  const defs = Array.isArray(summary?.buckets_def) ? summary.buckets_def : [];
  if (!defs.length) return have[0];
  const hour = summaryTzHour(summary, now);
  // Index of the def window that contains `hour`, else the first window
  // that starts after it (the next thing coming up), else the last.
  let idx = defs.findIndex((d) => hour >= d.start_hour_local && hour < d.end_hour_local);
  if (idx < 0) idx = defs.findIndex((d) => hour < d.start_hour_local);
  if (idx < 0) idx = defs.length - 1;
  // Walk forward from the chosen window to the first bucket the day
  // actually carries (today's elapsed buckets may have been dropped).
  for (let i = idx; i < defs.length; i++) {
    if (have.includes(defs[i].name)) return defs[i].name;
  }
  // Nothing at or after the current window (late evening) — show the
  // latest bucket the day still has rather than jumping to tomorrow.
  for (let i = idx - 1; i >= 0; i--) {
    if (have.includes(defs[i].name)) return defs[i].name;
  }
  return have[0];
}

// Today's {day, bucket} for a bucketed summary (wind / swell / current),
// or null when the summary doesn't cover today. This is what every
// bucketed timeline opens on: the user's current day at the current
// time of day.
export function todaySelection(summary, now = new Date()) {
  const todayDay = findTodayDay(summary, now);
  if (!todayDay?.buckets?.length) return null;
  const bucket = findCurrentBucket(summary, todayDay, now);
  if (!bucket) return null;
  return { day: todayDay.day, bucket };
}
