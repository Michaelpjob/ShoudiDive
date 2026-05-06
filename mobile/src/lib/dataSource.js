// Data layer for the mobile client.
//
// Talks to the live deployed manifest at
// https://shouldidive.com/data/manifest.json — the same one the web
// frontend reads. The pipeline (`pipeline/fetch_visibility.py` etc)
// publishes there nightly; the mobile app is a pure consumer.
//
// In v1 we deliberately DON'T decode the layer PNGs into Float32Arrays
// like the web does. The native map renders the PNG directly as an
// overlay (zero per-pixel JS work), so client-side decoding only
// matters for tooltips. Tooltip lookups will eventually use a small
// per-cell value endpoint or a server-side function; for v1 the
// status pill just shows the layer name + active window.
//
// `loadManifest` is idempotent — call it once at app startup, the
// result is cached in module state. `subscribe(cb)` lets components
// rerender when data lands.

const REMOTE_BASE = "https://shouldidive.com";
const MANIFEST_URL = `${REMOTE_BASE}/data/manifest.json`;

let manifest = null;
let inflight = null;
let ready = false;
// Last fetch error if loadManifest didn't return a real manifest. The
// previous behaviour collapsed every failure (HTTP error, JSON parse,
// network down, captive portal HTML) into manifest=null + ready=true,
// so MapScreen rendered the generic "no PNG" empty state with no
// indication that the failure was a network problem and no retry path.
// Tracking the error explicitly lets the UI distinguish "data hasn't
// loaded yet" from "data failed to load and you can retry".
let lastError = null;
const listeners = new Set();


/** Subscribe to manifest-state changes. Returns an unsubscribe fn. */
export function subscribe(cb) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function notify() {
  for (const cb of listeners) {
    try { cb(); } catch { /* ignore listener errors */ }
  }
}


/** Fetch + cache the manifest. Calls beyond the first are no-ops
 * unless the cached fetch failed. Pass force=true to retry after a
 * previous failure (e.g. user tapped a retry button). */
export async function loadManifest({ force = false } = {}) {
  if (manifest && !force) return manifest;
  if (inflight) return inflight;
  // Reset error state on retry so the UI can clear its banner.
  if (force) lastError = null;
  inflight = fetch(MANIFEST_URL)
    .then(async (r) => {
      if (!r.ok) {
        throw new Error(`HTTP ${r.status} ${r.statusText}`);
      }
      const json = await r.json();
      if (!json || typeof json !== "object" || !json.layers) {
        throw new Error("manifest payload missing 'layers'");
      }
      return json;
    })
    .then((m) => {
      manifest = m;
      lastError = null;
      ready = true;
      inflight = null;
      notify();
      return m;
    })
    .catch((err) => {
      // Don't clobber a previously-loaded manifest if a retry fails —
      // user keeps seeing stale data, just with a banner saying refresh
      // didn't work. (This matches the desktop behaviour.)
      const errMsg = err?.message || String(err);
      lastError = errMsg;
      ready = true;
      inflight = null;
      notify();
      return manifest; // null on first-time failure, last-good on retry-fail
    });
  return inflight;
}


export function getManifest() {
  return manifest;
}


export function isReady() {
  return ready;
}


/** Return the last fetch error message (or null if the manifest is fine). */
export function getError() {
  return lastError;
}


/**
 * Resolve the absolute URL of a layer's PNG for a given composite slot.
 *
 *   layer:     "sst" | "chl" | "viz"  (wind + swell come from the 5d feeds)
 *   composite: 1 | 2 | 3              (1d / 2d / 3d windows; sst+chl)
 *               or null (viz: only "now" exists)
 *
 * Returns null when the manifest hasn't loaded yet or the requested
 * slot is missing.
 */
export function getLayerPngUrl(layer, composite = null) {
  if (!manifest) return null;
  const info = manifest.layers?.[layer];
  if (!info) return null;
  const windows = info.windows || {};
  let key;
  if (layer === "viz") {
    key = "now";
  } else if (layer === "sst" || layer === "chl") {
    key = `${composite ?? 2}d`;
  } else {
    return null; // wind/swell handled by their 5d feeds, not addressed here
  }
  const w = windows[key];
  if (!w?.url) return null;
  return resolveAssetUrl(w.url);
}


/** Latest cycle timestamp (ISO) — used in the status pill. */
export function getGeneratedAt() {
  return manifest?.generated_at ?? null;
}


/** Convert a manifest-relative path (e.g. "/data/sst_2d.png") to the
 * deployed origin. Same-origin manifests don't strictly need this, but
 * being explicit keeps us insulated if we host data elsewhere later. */
function resolveAssetUrl(path) {
  if (/^https?:/i.test(path)) return path;
  return `${REMOTE_BASE}${path.startsWith("/") ? "" : "/"}${path}`;
}
