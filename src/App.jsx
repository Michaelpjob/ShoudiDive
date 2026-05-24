// Post-Stage-5 (2026-05-23): App.jsx is the thin top-level shell.
// It owns layer + composite + settingsOpen state and the timeline
// selections (via useTimelineSelections). Prefs (theme/opacity/units/
// mpaOn/bathyOn) live in PrefsContext — provider wraps the tree in
// src/main.jsx; consumers read via usePrefs() directly.
// Renders TopBar + SettingsPopover, hands off to MapShell.
import { useEffect, useState } from "react";
import { activeRegion } from "./lib/region.js";
import TopBar from "./components/TopBar.jsx";
import SettingsPopover from "./components/SettingsPopover.jsx";
import MapShell from "./components/MapShell.jsx";
import { useTimelineSelections } from "./hooks/useTimelineSelections.js";
import {
  loadManifest,
  subscribe,
  getDataState,
  getWind5dSummary,
  getCurrent5dSummary,
  getSwell5dSummary,
} from "./lib/dataSource.js";
import { sstSelToSlotKey } from "./components/SstTimeline.jsx";
import { track } from "./lib/analytics.js";



function useDataVersion() {
  const [, setTick] = useState(0);
  useEffect(() => {
    const unsub = subscribe(() => setTick((t) => t + 1));
    loadManifest();
    return unsub;
  }, []);
  return getDataState();
}



// Region-aware browser tab title. index.html ships a static
// "ShouldIDive — CA Coast Conditions" fallback; this overrides it
// once the React app boots so PNW + tropical visitors don't see
// "CA Coast" in their tab.
function useRegionAwareTitle() {
  useEffect(() => {
    const r = activeRegion();
    const subtitle =
      r === "pnw"      ? "Pacific NW Conditions" :
      r === "tropical" ? "FL + Caribbean Conditions" :
      r === "baja"     ? "Baja Mexico Conditions" :
      "California Coast Conditions";
    document.title = `ShouldIDive — ${subtitle}`;
  }, []);
}


export default function App() {
  useRegionAwareTitle();
  const [layer, setLayer] = useState("sst");
  const [composite, setComposite] = useState(2);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const dataState = useDataVersion();

  // Timeline selection state + MUR-lag auto-pick of SST mode. Extracted
  // into a hook on 2026-05-23 (Stage 3 of the refactor); see
  // src/hooks/useTimelineSelections.js for the reconcile + auto-pick
  // effect that this hook runs on dataState change.
  //
  // Stage 5b (2026-05-23) added the derived activeSstMode + sstActiveSel
  // exports so MapShell + MobileSheet don't re-derive them locally with
  // slightly-divergent inline conditionals.
  // Stage 5c: raw sstSel/setSstSel/sstForecastSel/setSstForecastSel
  // are no longer needed at App's level — downstream consumers all
  // use the derived sstActiveSel/setSstActiveSel via the hook's
  // unified API. The raw values still live in the hook for the
  // toggle/auto-pick logic, just not exposed up here.
  const {
    sstMode, setSstMode,
    windSel, setWindSel,
    swellSel, setSwellSel,
    currentSel, setCurrentSel,
    activeSstMode, sstActiveSel, setSstActiveSel,
    sstTimelineSummary, hasSstTimeline,
    hasSstHistory, hasSstForecast,
  } = useTimelineSelections(dataState);

  // Wrapped state setters that fire analytics events alongside the
  // setState. Passing these down to DesktopView (and through to
  // MobileSheet) means every layer-chip click — desktop or mobile —
  // gets one event, regardless of where in the JSX tree the click
  // originated. The closure captures the previous value so we can
  // emit `from`/`to` and answer "what's the most-common transition?".
  const trackedSetLayer = (next) => {
    if (next !== layer) {
      track("layer_change", { from: layer, to: next });
    }
    setLayer(next);
  };
  const trackedSetSstMode = (next) => {
    if (next !== sstMode) {
      track("sst_mode_change", { from: sstMode, to: next });
    }
    // setSstMode comes from useTimelineSelections — it flips the
    // hook's internal `userToggledRef` automatically, so the auto-pick
    // effect inside the hook respects this user choice on subsequent
    // data refreshes. No need to touch the ref here.
    setSstMode(next);
  };

  // Moon-phase icon should track the active time slider when those
  // layers are active, otherwise show "now". Computed at render time
  // so it updates whenever the selected time/layer changes. Stage 5b:
  // takes the hook's pre-resolved sstActiveSel + sstTimelineSummary
  // so it doesn't have to call resolveSstMode itself.
  const viewingDate = selToDate(layer, sstActiveSel, sstTimelineSummary, windSel, swellSel, currentSel);

  return (
    <div className="app">
      <TopBar
        onSettings={() => setSettingsOpen((v) => !v)}
        settingsOpen={settingsOpen}
        dataState={dataState}
      />
      {settingsOpen && (
        <SettingsPopover onClose={() => setSettingsOpen(false)} />
      )}
      <MapShell
        layer={layer}
        setLayer={trackedSetLayer}
        composite={composite}
        setComposite={setComposite}
        sstMode={sstMode}
        setSstMode={trackedSetSstMode}
        sstActiveSel={sstActiveSel}
        setSstActiveSel={setSstActiveSel}
        activeSstMode={activeSstMode}
        sstTimelineSummary={sstTimelineSummary}
        hasSstTimeline={hasSstTimeline}
        hasSstHistory={hasSstHistory}
        hasSstForecast={hasSstForecast}
        windSel={windSel}
        setWindSel={setWindSel}
        swellSel={swellSel}
        setSwellSel={setSwellSel}
        currentSel={currentSel}
        setCurrentSel={setCurrentSel}
        dataState={dataState}
        viewingDate={viewingDate}
      />
    </div>
  );
}

// Map a timeline selection to a real Date so the moon icon can update
// with the selected time. Returns null when the active layer has no
// timeline (chl/viz), so the widget falls back to "now".
//
// Stage 5b (2026-05-23): takes the already-resolved sstActiveSel +
// sstTimelineSummary from the hook instead of recomputing the
// history-vs-forecast pick locally.
function selToDate(layer, sstActiveSel, sstTimelineSummary, windSel, swellSel, currentSel) {
  if (layer === "sst") {
    const slot = sstSelToSlotKey(sstActiveSel, sstTimelineSummary);
    const dayInfo = sstTimelineSummary?.days?.find((d) => d.slot === slot);
    return dayInfo?.date ? new Date(`${dayInfo.date}T12:00:00Z`) : null;
  }
  let sel = null;
  let summary = null;
  if (layer === "wind") {
    sel = windSel;
    summary = getWind5dSummary();
  } else if (layer === "swell") {
    sel = swellSel;
    summary = getSwell5dSummary();
  } else if (layer === "current") {
    sel = currentSel;
    summary = getCurrent5dSummary();
  }
  if (!sel || !summary) return null;
  const dayInfo = summary.days?.find((d) => d.day === sel.day);
  if (!dayInfo?.date) return null;
  const [y, m, d] = dayInfo.date.split("-").map(Number);
  if (!y || !m || !d) return null;
  const hour =
    sel.hour != null
      ? sel.hour
      : { predawn: 5, morning: 8, midday: 12, afternoon: 16, evening: 20 }[
          sel.bucket
        ] ?? 12;
  return new Date(y, m - 1, d, hour, 0, 0);
}




