// MPA / bathy popup state — what's currently selected, the lazy-loaded
// bathy GeoJSON features, and the toggle-off effects that clear the
// popup when its layer is turned off.
//
// Extracted from App.jsx (2026-05-23) as part of the Stage 3 refactor.
// Pure mechanical extraction — same logic, new home. The two
// "selectedX = null when layerOn = false" effects keep their existing
// dependency lists, and the bathy lazy-load preserves its
// cleanup-on-cancel pattern.
//
// API:
//   const p = usePopupState({ mpaOn, bathyOn });
//   p.selectedMpa, p.setSelectedMpa       — currently-clicked MPA polygon, null = closed
//   p.selectedBathy, p.setSelectedBathy   — currently-clicked bathy feature (seamount/reef/community spot)
//   p.bathyFeatures                       — lazy-loaded GeoJSON feature array, null until bathyOn is true
//   p.setBathyFeatures                    — exposed for test/dev tooling; rarely called outside the lazy-load effect
//
// Note: the parent (DesktopView) typically wraps mpaOn/bathyOn setters
// with updateMpaOn/updateBathyOn that also clear the matching
// selected* synchronously. This hook's effects are the safety-net
// catch-all — they fire on any path that turns the layer off,
// including external/programmatic ones.

import { useEffect, useState } from "react";

import { loadBathyFeatures } from "../components/BathyLayer.jsx";

export function usePopupState({ mpaOn, bathyOn }) {
  const [selectedMpa, setSelectedMpa] = useState(null);
  const [selectedBathy, setSelectedBathy] = useState(null);
  const [bathyFeatures, setBathyFeatures] = useState(null);

  // Close MPA popup when the MPA layer is turned off.
  useEffect(() => {
    if (!mpaOn) setSelectedMpa(null);
  }, [mpaOn]);

  // Close bathy popup when the bathy layer is turned off.
  useEffect(() => {
    if (!bathyOn) setSelectedBathy(null);
  }, [bathyOn]);

  // Lazy-load bathy features whenever the layer flips on (used for
  // both the SVG markers and the screen-space labels). Cancel on
  // unmount or if the layer flips off before the fetch resolves so we
  // don't setState on an unmounted component.
  useEffect(() => {
    if (!bathyOn || bathyFeatures) return;
    let cancelled = false;
    loadBathyFeatures().then((fc) => {
      if (cancelled || !fc) return;
      setBathyFeatures(fc.features || []);
    });
    return () => { cancelled = true; };
  }, [bathyOn, bathyFeatures]);

  return {
    selectedMpa, setSelectedMpa,
    selectedBathy, setSelectedBathy,
    bathyFeatures, setBathyFeatures,
  };
}
