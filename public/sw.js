/* ShouldIDive service worker.
 *
 * Strategy:
 *   - Bumping CACHE_VERSION evicts every old cache on the next visit, so
 *     we never serve a stale JS bundle paired with a fresh manifest.
 *   - App shell (HTML + JS + CSS + icons + fonts): cache-first, falls back
 *     to network. The shell is small and we want offline launches to work.
 *   - /data/* (PNGs + JSON): stale-while-revalidate. The page renders
 *     instantly from whatever we last had, then quietly upgrades to the
 *     newest pipeline output in the background. If the network is gone,
 *     we keep serving what's cached and the app shows it as stale.
 *   - Everything else: network-first with cache fallback.
 *
 * Bump CACHE_VERSION whenever the cache strategy needs to change. Vite's
 * fingerprinted asset filenames already invalidate the bundle on every
 * deploy without a SW change.
 */
// Bump this on any cache-strategy change so existing clients evict
// their old caches on next launch.
//   v1 — first PWA shell (had broken zoom)
//   v2 — fixed shell, stale-while-revalidate for data
//   v3 — data path switched to network-first; old SWR caches were
//        leaving Chrome showing pre-fill swell PNGs even after the
//        nearshore-fill pipeline landed. Network-first + cache
//        fallback gets fresh data when online and graceful offline
//        without holding users one cycle behind on each visit.
//   v4 — mobile shell rebuild (peek-strip + pull-up sheet, tap-to-pin,
//        always-visible layer chips). Bundle hash changes anyway, but
//        bumping evicts the old shell cache on first launch so the
//        controllerchange auto-reload kicks users straight onto the
//        new layout instead of showing them yesterday's UI for one
//        more tab cycle.
//   v5 — dive flag icon set (icon.svg + icon-{192,512}.png +
//        apple-touch-icon.png + favicon-32.png all changed). The shell
//        cache was holding the old freediver-silhouette PNGs; bump
//        forces eviction so home-screen icons refresh on next launch.
//   v6 — wind streamlines no longer flow over land. The fix is
//        WindParticles internal (no shell asset change), but the
//        cache-first shell handler holds users on the OLD bundle for
//        one cycle after each deploy unless we bump. Without v6,
//        Michael saw "same issues" because his Safari served the
//        previous index.html from cache, referencing the previous JS
//        bundle, with no land mask. Bumping forces controllerchange +
//        auto-reload onto the fixed bundle on next launch.
const CACHE_VERSION = "v6";
const SHELL_CACHE = `shouldidive-shell-${CACHE_VERSION}`;
const DATA_CACHE  = `shouldidive-data-${CACHE_VERSION}`;

// Files to pre-cache so a cold offline launch shows something. Vite's
// hashed bundle paths are added on first fetch instead — adding them
// here would mean updating SW on every deploy.
const SHELL_PRECACHE = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/icon.svg",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png",
  "/favicon-32.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_PRECACHE)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== SHELL_CACHE && k !== DATA_CACHE)
          .map((k) => caches.delete(k)),
      ),
    ),
  );
  self.clients.claim();
});

function isShellRequest(url) {
  if (url.origin !== self.location.origin) return false;
  if (url.pathname === "/" || url.pathname === "/index.html") return true;
  if (url.pathname.startsWith("/assets/")) return true;
  if (/\.(svg|png|ico|webmanifest)$/.test(url.pathname)) return true;
  return false;
}

function isDataRequest(url) {
  if (url.origin !== self.location.origin) return false;
  return url.pathname.startsWith("/data/");
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  if (isDataRequest(url)) {
    // Network-first for the live data plane. The pipeline regenerates
    // these PNGs / JSON every cron cycle, so stale-while-revalidate was
    // showing users the version *previous to* their last visit (the
    // background fetch updated the cache for NEXT time, not now). Go
    // network-first; only fall back to cache when offline.
    event.respondWith(
      caches.open(DATA_CACHE).then(async (cache) => {
        try {
          const res = await fetch(req);
          if (res && res.ok) cache.put(req, res.clone());
          return res;
        } catch {
          const cached = await cache.match(req);
          if (cached) return cached;
          throw new Error("offline and no cache for " + req.url);
        }
      }),
    );
    return;
  }

  if (isShellRequest(url)) {
    // Cache-first for the app shell, with network upgrade in the background.
    event.respondWith(
      caches.open(SHELL_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        if (cached) {
          // Refresh in background so the next visit gets the latest shell.
          fetch(req).then((res) => {
            if (res && res.ok) cache.put(req, res.clone());
          }).catch(() => {});
          return cached;
        }
        const res = await fetch(req);
        if (res && res.ok) cache.put(req, res.clone());
        return res;
      }),
    );
    return;
  }

  // Default: network-first, cache fallback.
  event.respondWith(
    fetch(req).catch(() => caches.match(req)),
  );
});
