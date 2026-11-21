// `?spot=<id>` deep link. The static spot pages (/spots/<slug>/, built by
// scripts/build-seo-pages.mjs) link into the map with the spot preselected.
// Unknown ids fall back to the default so a stale or hand-typed link still
// opens the map.
import { SAVED_SPOTS } from "./mapData.js";

export function spotFromUrl() {
  try {
    const id = new URLSearchParams(window.location.search).get("spot");
    return id && SAVED_SPOTS.some((s) => s.id === id) ? id : null;
  } catch {
    return null;
  }
}

export function initialSpotId(fallback = "lajolla") {
  return spotFromUrl() || fallback;
}
