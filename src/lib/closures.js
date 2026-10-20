// Copyright (c) 2026 Michael Job. All rights reserved.
// Licensed under the PolyForm Strict License 1.0.0; see LICENSE.md.
// Navy closures freshness guard, shared by ClosuresLayer + ClosuresTimeline.
//
// Drops days before today (in the file's own tz) so an old file can never
// label a past date "Today". If the file doesn't cover today at all it is
// marked stale: the day-strip then says the schedule is unavailable and every
// zone draws as "unknown" rather than open or closed. (2026-10: the scraper
// read a frozen source for 3.5 months and the strip kept showing Jun 29 as
// "Today · clear".)

import { summaryTzToday } from "./today.js";

export function normalizeClosures(fc, today = summaryTzToday(fc)) {
  if (!fc) return null;
  const all = fc.dates || [];
  const dates = all.filter((d) => d >= today);
  return { ...fc, dates, today, stale: dates[0] !== today };
}
