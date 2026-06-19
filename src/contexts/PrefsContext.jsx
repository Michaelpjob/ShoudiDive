// PrefsContext — single source of truth for user preferences (theme,
// opacity, units, MPA/bathy overlay toggles).
//
// Extracted 2026-05-23 (Stage 5c) so that:
//   * App.jsx stops being the central wire-up point for prefs
//   * MapShell / MobileSheet / SettingsPopover stop receiving them as props
//     and read directly via usePrefs()
//   * the localStorage persistence + theme-attribute side-effects live
//     next to the state they describe
//
// Reading / writing:
//   const { prefs, setPref } = usePrefs();
//   setPref("theme", "dark");
//
// The provider must wrap the entire React tree (see src/main.jsx).
// The OVERLAY_DEFAULTS_MIGRATION_KEY one-shot ensures users who saved
// an older shape (no mpaOn/bathyOn) get the new defaults applied once.

import { createContext, useContext, useEffect, useState } from "react";
import { track } from "../lib/analytics.js";

const PREF_KEY = "ca-coast-conditions:prefs:v1";
const OVERLAY_DEFAULTS_MIGRATION_KEY = "ca-coast-conditions:prefs:migrations:overlay-defaults-v1";
// waterColumnOn gates the depth-resolved visibility readout (PRD
// water-column D2) — ON by default with a BETA badge, matching how
// Current/Vis ship as visible-with-BETA-tag; the settings toggle is
// the off-switch. kelpOn is dev's kelp-overlay preview pref.
const DEFAULT_PREFS = { theme: "light", opacity: 0.62, units: "F", mpaOn: true, bathyOn: true, kelpOn: true, waterColumnOn: true, closuresOn: true };

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
    return { ...DEFAULT_PREFS };
  }
}

const PrefsContext = createContext(null);

export function PrefsProvider({ children }) {
  const [prefs, setPrefs] = useState(loadPrefs);

  // Persist + apply data-theme attribute. Was an inline useEffect in
  // App.jsx pre-Stage-5c.
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

  return (
    <PrefsContext.Provider value={{ prefs, setPref }}>
      {children}
    </PrefsContext.Provider>
  );
}

export function usePrefs() {
  const ctx = useContext(PrefsContext);
  if (!ctx) {
    throw new Error("usePrefs() called outside <PrefsProvider>. Wrap your app root in src/main.jsx.");
  }
  return ctx;
}
