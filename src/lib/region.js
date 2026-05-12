// Region routing — picks which `/data/<slug>/` tree the app reads.
//
// Source of truth resolution order:
//   1. `?region=` URL parameter (sticky — also persisted to localStorage)
//   2. `localStorage.region`
//   3. Default: "ca"
//
// Mirrors `pipeline/regions/` on the Python side. Adding a new
// region = (a) drop a new entry in `pipeline/regions/`, (b) add it
// to VALID below, (c) add it to the RegionSwitcher REGIONS list.

const VALID = ["ca", "pnw", "tropical"];
const DEFAULT_REGION = "ca";

let _cached = null;

/**
 * Hostname → region lock. Each Cloudflare Pages preview branch gets
 * its own subdomain (pnw-beta.shouldidive.pages.dev,
 * tropical-beta.shouldidive.pages.dev). If a visitor lands on one of
 * those URLs we lock the region to that subdomain regardless of any
 * URL param or localStorage value — the WHOLE POINT of the per-region
 * preview is that PNW beta testers see only PNW.
 *
 * Returns null when the hostname doesn't match a known beta slug
 * (= use URL/localStorage/default as before).
 */
function _regionFromHostname() {
  try {
    const h = (window.location.hostname || "").toLowerCase();
    if (h.startsWith("pnw-beta.")) return "pnw";
    if (h.startsWith("tropical-beta.")) return "tropical";
  } catch {
    // SSR or no-window — fall through.
  }
  return null;
}

/**
 * Read the active region. Resolution order:
 *   1. Hostname (locks pnw-beta.* / tropical-beta.* subdomains)
 *   2. ?region= URL param
 *   3. localStorage.region
 *   4. Default: "ca"
 * Cached on first read so every consumer sees the same value within
 * a page load.
 */
export function activeRegion() {
  if (_cached !== null) return _cached;

  // Hostname-pinned region wins over everything else. Without this
  // the bundle on pnw-beta.shouldidive.pages.dev would default to
  // "ca" on a clean visit and show CA data despite the URL — which
  // is exactly what the user saw on first beta-URL load.
  const fromHost = _regionFromHostname();
  if (fromHost) {
    _cached = fromHost;
    return fromHost;
  }

  let resolved = DEFAULT_REGION;
  try {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("region");
    if (fromUrl && VALID.includes(fromUrl)) {
      resolved = fromUrl;
      try { window.localStorage.setItem("region", fromUrl); } catch {}
    } else {
      const stored = window.localStorage.getItem("region");
      if (stored && VALID.includes(stored)) resolved = stored;
    }
  } catch {
    // SSR / private-mode fallback — stick with the default.
  }
  _cached = resolved;
  return resolved;
}

/**
 * True when the current hostname pins the region (= a beta subdomain).
 * Used by the RegionSwitcher to hide itself — switching regions on
 * pnw-beta would be a no-op since hostname overrides the URL param.
 */
export function isRegionLocked() {
  return _regionFromHostname() !== null;
}

/**
 * Switch to a different region and reload. Reload is the cheap path:
 * the entire data layer (manifest, layer PNGs, MPA polygons,
 * coastline) needs to be re-fetched from the new region's tree, and
 * the worker / loader caches don't know how to invalidate themselves
 * piecemeal yet.
 */
export function setActiveRegion(next) {
  if (!VALID.includes(next)) return;
  try { window.localStorage.setItem("region", next); } catch {}
  const url = new URL(window.location.href);
  if (next === DEFAULT_REGION) url.searchParams.delete("region");
  else url.searchParams.set("region", next);
  window.history.replaceState({}, "", url);
  window.location.reload();
}

/**
 * Rewrite a `/data/...` URL into the active region's tree.
 * For CA (the default region) the path is returned unchanged so
 * everything in `public/data/` keeps working bit-for-bit.
 */
export function dataPath(absPath) {
  const r = activeRegion();
  if (r === DEFAULT_REGION) return absPath;
  if (typeof absPath !== "string") return absPath;
  return absPath.replace(/^\/data\//, `/data/${r}/`);
}

/**
 * The manifest URL for the active region. Convenience wrapper —
 * equivalent to `dataPath("/data/manifest.json")` but lets callers
 * be explicit about intent.
 */
export function manifestUrl() {
  return dataPath("/data/manifest.json");
}

/**
 * Recursively rewrite every `/data/...` string in a manifest object
 * so the loaders fetch from the correct region tree. The pipeline
 * currently writes manifest URLs as bare `/data/<file>.png` regardless
 * of which region produced it; this rewrite lets the frontend stay
 * agnostic of that detail. Mutates in place + returns the same
 * reference for chaining.
 */
export function rewriteManifestUrls(manifest) {
  const r = activeRegion();
  if (r === DEFAULT_REGION || !manifest || typeof manifest !== "object") {
    return manifest;
  }
  const prefix = `/data/${r}/`;
  function walk(obj) {
    if (Array.isArray(obj)) {
      for (const v of obj) walk(v);
      return;
    }
    if (!obj || typeof obj !== "object") return;
    for (const key of Object.keys(obj)) {
      const v = obj[key];
      if (typeof v === "string" && v.startsWith("/data/") &&
          !v.startsWith(prefix)) {
        obj[key] = prefix + v.slice("/data/".length);
      } else if (v && typeof v === "object") {
        walk(v);
      }
    }
  }
  walk(manifest);
  return manifest;
}

export function listRegions() {
  return [...VALID];
}

export const DEFAULT_REGION_ID = DEFAULT_REGION;
