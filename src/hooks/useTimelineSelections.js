// Timeline selection state for all data layers — SST (history + forecast),
// wind, swell, current. Plus the SST-mode auto-pick logic that fires on
// data load when MUR's lag means wall-clock today only lives in the
// forecast track.
//
// Extracted from App.jsx (2026-05-23) as part of the Stage 3 refactor.
// Pure mechanical extraction — same logic, new home — so the reconcile
// + auto-pick effect runs on the SAME dependencies (dataState.ready +
// dataState.manifest.generated_at) and the same edge cases hold.
//
// API:
//   const t = useTimelineSelections(dataState);
//   t.sstMode, t.setSstMode             — "history" | "forecast" (raw user intent)
//   t.sstSel, t.setSstSel               — {slot} pointing at a history day
//   t.sstForecastSel, t.setSstForecastSel
//   t.windSel, t.setWindSel             — {day, bucket, hour} for the wind 5-day grid
//   t.swellSel, t.setSwellSel           — same shape as windSel
//   t.currentSel, t.setCurrentSel       — {day, bucket} (no hour — currents are bucket-level)
//
//   Derived SST mode/sel (Stage 5b, 2026-05-23) — single source of truth for
//   "which SST track is active right now and what's the current selection in
//   it." Computed via resolveSstMode against the live summaries each render.
//   MapShell + MobileSheet used to recompute these locally with subtly
//   different logic; harmonised into the hook so there's one definition.
//   t.activeSstMode                     — "history" | "forecast" (resolved against summaries)
//   t.sstActiveSel                      — {slot} for whichever track is active
//   t.setSstActiveSel                   — corresponding setter
//   t.sstTimelineSummary                — the summary object for the active mode
//   t.hasSstTimeline                    — Boolean(sstTimelineSummary)
//
// Notes:
//   * The PUBLIC setSstMode flips `userToggledRef.current = true` so the
//     auto-pick effect respects user intent on subsequent refreshes.
//   * The auto-pick effect uses the RAW setter internally so its own
//     writes don't flip the ref.
//   * `dataState` is passed in (not destructured) so the effect's
//     dependency array matches the original ?. chains.

import { useCallback, useEffect, useRef, useState } from "react";

// Summary getters live in dataSource.js; selection-validity + default-
// picker helpers live in each layer's component module (because they
// know that layer's slot-key shape). Import-source matches App.jsx
// before the extraction.
import {
  getSstHistorySummary,
  getSstForecastSummary,
  getWind5dSummary,
  getSwell5dSummary,
  getCurrent5dSummary,
} from "../lib/dataSource.js";
import { resolveSstMode } from "../lib/sstMode.js";
import {
  defaultSstSelection,
  sstSelectionHasData,
} from "../components/SstTimeline.jsx";
import {
  defaultWindSelection,
  selectionHasData,
} from "../components/WindDayGrid.jsx";
import {
  currentSelectionHasData,
  defaultCurrentSelection,
} from "../components/CurrentTimeline.jsx";

export function useTimelineSelections(dataState) {
  const [sstMode, _setSstMode] = useState("history");
  const [sstSel, setSstSel] = useState({ slot: "d0" });
  const [sstForecastSel, setSstForecastSel] = useState({ slot: "f0" });
  const [windSel, setWindSel] = useState({ day: 0, bucket: "morning", hour: null });
  const [swellSel, setSwellSel] = useState({ day: 0, bucket: "morning", hour: null });
  const [currentSel, setCurrentSel] = useState({ day: 0, bucket: "midday" });

  // Track whether the user has manually toggled SST mode. The auto-pick
  // effect skips when this is true, so a user's explicit history-vs-
  // forecast choice survives subsequent data refreshes.
  const userToggledRef = useRef(false);

  // PUBLIC setter — flips the ref so this counts as a user choice.
  const setSstMode = useCallback((next) => {
    userToggledRef.current = true;
    _setSstMode(next);
  }, []);

  // Reconcile + auto-pick. Runs whenever a fresh manifest lands. The
  // selection-reconciliation pass catches stale selections (e.g. user
  // had `f3` selected but the new manifest only emits f0..f2); the
  // auto-pick pass handles the MUR-lag case by switching mode to
  // wherever wall-clock today actually lives.
  useEffect(() => {
    const tSummary = getSstHistorySummary();
    if (tSummary && !sstSelectionHasData(tSummary, sstSel)) {
      setSstSel(defaultSstSelection(tSummary));
    }
    const tfSummary = getSstForecastSummary();
    if (tfSummary && !sstSelectionHasData(tfSummary, sstForecastSel)) {
      setSstForecastSel(defaultSstSelection(tfSummary));
    }
    const wSummary = getWind5dSummary();
    if (wSummary && !selectionHasData(wSummary, windSel)) {
      setWindSel(defaultWindSelection(wSummary));
    }
    const sSummary = getSwell5dSummary();
    if (sSummary && !selectionHasData(sSummary, swellSel)) {
      setSwellSel(defaultWindSelection(sSummary));
    }
    const cSummary = getCurrent5dSummary();
    if (cSummary && !currentSelectionHasData(cSummary, currentSel)) {
      setCurrentSel(defaultCurrentSelection(cSummary));
    }

    // 2026-05-22: Auto-pick SST mode based on where wall-clock today
    // lives. MUR satellite SST publishes ~1-2 days behind, so on a
    // normal day:
    //   * history's latest = d0 = anchor_date (2 days ago)
    //   * forecast's f<lag> = today (the model-projected nowcast)
    // The default sstMode = "history" lands the playhead on d0, which
    // the user sees as "today is missing." Auto-switch to forecast if
    // today only lives in the forecast summary.
    //
    // Uses the RAW setter (_setSstMode) so this auto-pick doesn't flip
    // userToggledRef — only EXPLICIT calls via the public setSstMode
    // count as user intent.
    if (!userToggledRef.current && tSummary && tfSummary) {
      const today = new Date().toISOString().slice(0, 10);  // YYYY-MM-DD UTC
      const historyHasToday = tSummary.days?.some((d) => d.date === today);
      const forecastHasToday = tfSummary.days?.some((d) => d.date === today);
      // Only flip when forecast has today AND history doesn't. If both
      // have today (MUR caught up), prefer history (observed > modelled).
      // If neither has today (extreme MUR outage), stay on history so
      // user sees the most recent OBSERVATION rather than a deeply-
      // decayed forecast.
      if (forecastHasToday && !historyHasToday && sstMode !== "forecast") {
        _setSstMode("forecast");
      } else if (historyHasToday && sstMode !== "history") {
        _setSstMode("history");
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataState?.ready, dataState?.manifest?.generated_at]);

  // ---- Derived SST mode / selection (Stage 5b harmonisation) ----------
  // Computed each render against the LIVE summaries so layers know whether
  // history or forecast is the active track and what slot is selected
  // there. Two-system duplication (MapShell + MobileSheet each computing
  // this with slightly-different inline conditionals) collapsed to one
  // call site here. Callers receive ready-to-render activeSstMode +
  // sstActiveSel + setSstActiveSel + sstTimelineSummary + hasSstTimeline.
  const sstHistorySummary = getSstHistorySummary();
  const sstForecastSummary = getSstForecastSummary();
  const activeSstMode = resolveSstMode(sstMode, sstHistorySummary, sstForecastSummary);
  const sstTimelineSummary = activeSstMode === "forecast" ? sstForecastSummary : sstHistorySummary;
  const sstActiveSel = activeSstMode === "forecast" ? sstForecastSel : sstSel;
  const setSstActiveSel = activeSstMode === "forecast" ? setSstForecastSel : setSstSel;
  const hasSstTimeline = Boolean(sstTimelineSummary);

  return {
    sstMode, setSstMode,
    sstSel, setSstSel,
    sstForecastSel, setSstForecastSel,
    windSel, setWindSel,
    swellSel, setSwellSel,
    currentSel, setCurrentSel,
    // Derived (Stage 5b):
    activeSstMode,
    sstActiveSel, setSstActiveSel,
    sstTimelineSummary,
    hasSstTimeline,
  };
}
