// Shared "today" helpers for timeline default-selection logic.
//
// Wind / swell / current / SST summaries each carry a `tz` field
// (America/Los_Angeles for CA + Baja + PNW; UTC for tropical/SST5d).
// Their per-day entries have a `date: "YYYY-MM-DD"` string anchored to
// that tz. To make every timeline default to "today" on app load
// (rather than to `day: 0` which can lag a day when the cycle anchor
// is yesterday-PT), look up today's date in the summary's own tz and
// find the matching day.

export function summaryTzToday(summary) {
  const tz = summary?.tz || "America/Los_Angeles";
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date()); // "YYYY-MM-DD"
}

export function findTodayDay(summary) {
  if (!summary?.days?.length) return null;
  const today = summaryTzToday(summary);
  return summary.days.find((d) => d.date === today) || null;
}
