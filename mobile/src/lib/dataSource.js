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
 * unless the cached fetch failed. */
export async function loadManifest() {
  if (manifest) return manifest;
  if (inflight) return inflight;
  inflight = fetch(MANIFEST_URL)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null)
    .then((m) => {
      manifest = m;
      ready = true;
      inflight = null;
      notify();
      return m;
    });
  return inflight;
}


export function getManifest() {
  return manifest;
}


export function isReady() {
  return ready;
}


/**
 * Resolve the absolute URL of a layer's PNG for a given composite slot.
 *
 *   layer:     "sst" | "chl" | "viz"  (wind + swell come from the 5d feeds)
 *   composite: 1 | 2 | 3              (1d / 2d / 3d windows; sst+chl)
 *               or null (viz: only "now" exists)
 *
 * Mobile prefers a pre-colored RGBA asset when the manifest provides
 * one (`mobile_url`). The pipeline emits these alongside the canonical
 * grayscale PNGs; with pre-colored input the device skips the
 * client-side LUT pass entirely (no Skia readPixels, no per-pixel
 * recolour). Falls back to the grayscale `url` for layers the
 * pipeline hasn't yet pre-colorized.
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
  const assetUrl = w?.mobile_url || w?.url;
  if (!assetUrl) return null;
  return resolveAssetUrl(assetUrl);
}


/** True when the URL `getLayerPngUrl` would return is a pre-colored
 * RGBA asset (manifest's `mobile_url`). Lets the renderer skip the
 * client-side LUT pass when the pipeline already did the work. */
export function isPreColored(layer, composite = null) {
  if (!manifest) return false;
  const info = manifest.layers?.[layer];
  if (!info) return false;
  const windows = info.windows || {};
  const key = layer === "viz" ? "now" : `${composite ?? 2}d`;
  return Boolean(windows[key]?.mobile_url);
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
