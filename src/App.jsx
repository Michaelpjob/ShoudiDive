// Post-Stage-4 (2026-05-23): App.jsx is the thin top-level shell.
// It owns app-wide state (prefs, layer, timeline selections via
// useTimelineSelections), renders the TopBar + SettingsPopover, then
// hands off to MapShell which owns the map stage + all data overlay
// rendering. Everything MapShell uses imports from its own module,
// not from here.
import { useEffect, useState } from "react";
import { activeRegion } from "./lib/region.js";
import TopBar from "./components/TopBar.jsx";
import SettingsPopover from "./components/SettingsPopover.jsx";
import MapShell from "./components/MapShell.jsx";
import { useTimelineSelections } from "./hooks/useTimelineSelections.js";
import { resolveSstMode } from "./lib/sstMode.js";
import {
  loadManifest,
  subscribe,
  getDataState,
  getWind5dSummary,
  getCurrent5dSummary,
  getSstForecastSummary,
  getSstHistorySummary,
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



const PREF_KEY = "ca-coast-conditions:prefs:v1";
const OVERLAY_DEFAULTS_MIGRATION_KEY = "ca-coast-conditions:prefs:migrations:overlay-defaults-v1";
const DEFAULT_PREFS = { theme: "light", opacity: 0.62, units: "F", mpaOn: true, bathyOn: true };

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    const prefs = raw ? { ...DEFAULT_PREFS, ...JSON.parse(raw) } : { ...DEFAULT_PREFS };
    if (!localStorage.getItem(OVERLAY_DEFAULTS_MIGRATION_KEY)) {
      prefs.bathyOn = true;
      localStorage.setItem(OVERLAY_DEFAULTS_MIGRATION_KEY, "1");
    }
    return prefs;
  } catch {
    return DEFAULT_PREFS;
  }
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
  const [prefs, setPrefs] = useState(loadPrefs);
  const [layer, setLayer] = useState("sst");
  const [composite, setComposite] = useState(2);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const dataState = useDataVersion();

  // Timeline selection state + MUR-lag auto-pick of SST mode. Extracted
  // into a hook on 2026-05-23 (Stage 3 of the refactor); see
  // src/hooks/useTimelineSelections.js for the reconcile + auto-pick
  // effect that this hook runs on dataState change.
  const {
    sstMode, setSstMode,
    sstSel, setSstSel,
    sstForecastSel, setSstForecastSel,
    windSel, setWindSel,
    swellSel, setSwellSel,
    currentSel, setCurrentSel,
  } = useTimelineSelections(dataState);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", prefs.theme);
    document.body.setAttribute("data-theme", prefs.theme);
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
    } catch { /* ignore quota */ }
  }, [prefs]);

  function setPref(key, val) {
    setPrefs((p) => ({ ...p, [key]: val }));
    // Track settings changes — answers "do users actually toggle theme,
    // change opacity, switch units?". Don't include the value as a
    // string for free-form fields; just the key + a JSON-safe value.
    track("settings_change", { key, val });
  }

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
  // so it updates whenever the selected time/layer changes.
  const viewingDate = selToDate(layer, sstMode, sstSel, sstForecastSel, windSel, swellSel, currentSel);

  return (
    <div className="app">
      <TopBar
        onSettings={() => setSettingsOpen((v) => !v)}
        settingsOpen={settingsOpen}
        dataState={dataState}
      />
      {settingsOpen && (
        <SettingsPopover prefs={prefs} setPref={setPref} onClose={() => setSettingsOpen(false)} />
      )}
      <MapShell
        layer={layer}
        setLayer={trackedSetLayer}
        composite={composite}
        setComposite={setComposite}
        sstMode={sstMode}
        setSstMode={trackedSetSstMode}
        sstSel={sstSel}
        setSstSel={setSstSel}
        sstForecastSel={sstForecastSel}
        setSstForecastSel={setSstForecastSel}
        windSel={windSel}
        setWindSel={setWindSel}
        swellSel={swellSel}
        setSwellSel={setSwellSel}
        currentSel={currentSel}
        setCurrentSel={setCurrentSel}
        opacity={prefs.opacity}
        units={prefs.units}
        dataState={dataState}
        mpaOn={prefs.mpaOn}
        setMpaOn={(v) => setPref("mpaOn", v)}
        bathyOn={prefs.bathyOn}
        setBathyOn={(v) => setPref("bathyOn", v)}
        viewingDate={viewingDate}
      />
    </div>
  );
}

// Map a timeline selection to a real Date so the moon icon can update
// with the selected time. Returns null when the active layer has no
// timeline (chl/viz), so the widget falls back to "now".
function selToDate(layer, sstMode, sstSel, sstForecastSel, windSel, swellSel, currentSel) {
  if (layer === "sst") {
    const history = getSstHistorySummary();
    const forecast = getSstForecastSummary();
    const activeMode = resolveSstMode(sstMode, history, forecast);
    const summary = activeMode === "forecast" ? forecast : history;
    const slot = sstSelToSlotKey(activeMode === "forecast" ? sstForecastSel : sstSel, summary);
    const dayInfo = summary?.days?.find((d) => d.slot === slot);
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




