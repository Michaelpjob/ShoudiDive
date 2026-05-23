// Pick which SST track (history vs forecast) the UI should actively
// render given the requested mode and the two summaries' availability.
//
// Used by both:
//   * App.jsx's selToDate computation (so the moon-phase widget tracks
//     the right summary's date range)
//   * MapShell.jsx's render path (so the timeline + current-value
//     readout pull from the right per-day stats)
//
// Extracted to src/lib/ on 2026-05-23 (Stage 4 refactor) so the two
// callers don't have to live in the same file just to share this
// six-line decision tree.
export function resolveSstMode(requested, historySummary, forecastSummary) {
  if (requested === "forecast" && forecastSummary?.days?.length) return "forecast";
  if (historySummary?.days?.length) return "history";
  if (forecastSummary?.days?.length) return "forecast";
  return "history";
}
