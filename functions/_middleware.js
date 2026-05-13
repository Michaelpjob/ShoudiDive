// Cloudflare Pages middleware — global request gate.
//
// This file lives at functions/_middleware.js and runs BEFORE static
// asset serving for every request to the site. We use it to:
//   1. Return HARD 404 for scanner-known paths (/.env, /.git/*, etc.)
//      that the SPA fallback would otherwise serve as 200 + index.html.
//      CF Pages' `_redirects` file only supports 200/301/302/303/307/308
//      — NOT 404 — so this is the only way to produce a real 404 status
//      for these paths.
//   2. Pass everything else through to the next handler (static asset
//      or another Function) via `next()`.
//
// CPU cost:
//   This middleware runs on every request. The path check below is a
//   single Set lookup + a couple of startsWith calls — sub-microsecond.
//   The hot path (real assets and the SPA fallback) costs effectively
//   nothing.
//
// Why not handle this in `_redirects` with a 302 to /404?
//   Because then scanners see HTTP 302 + Location: /404 and follow it.
//   Once they fetch /404 (which would be the SPA index.html, status 200),
//   they conclude the site "exists" and escalate. A crisp 404 immediately
//   tells the scanner this URL is dead — they drop us in their priority
//   queues.

// Scanner-known path prefixes. Maintained alongside SECURITY.md.
// Order doesn't matter; we walk the whole list. Patterns:
//   * exact match  → request.url.pathname === pattern
//   * "*" suffix   → request.url.pathname.startsWith(pattern without "*")
const SCANNER_PATHS_EXACT = new Set([
  "/.env",
  "/.htaccess",
  "/.htpasswd",
  "/.DS_Store",
  "/xmlrpc.php",
  "/wlwmanifest.xml",
  "/info.php",
  "/test.php",
  "/admin.php",
  "/adminer.php",
  "/server-status",
  "/server-info",
  "/composer.json",
  "/composer.lock",
  "/package-lock.json",
  "/yarn.lock",
  "/pnpm-lock.yaml",
  "/Dockerfile",
  "/.dockerignore",
  "/docker-compose.yml",
  "/.travis.yml",
  "/dump.sql",
  "/db.sql",
  "/database.sql",
]);

const SCANNER_PREFIXES = [
  "/.env.",        // /.env.local, /.env.production, etc.
  "/.git",         // /.git/config, /.git/HEAD, etc.
  "/.svn",
  "/.hg",
  "/.aws",
  "/.ssh",
  "/.vscode",
  "/.idea",
  "/wp-admin",     // WordPress probes (#1 bot class)
  "/wp-login",
  "/wp-includes",
  "/wp-content",
  "/wp-config",
  "/wp-json",
  "/phpmyadmin",
  "/phpMyAdmin",
  "/pma/",
  "/admin/",
  "/administrator/",
  "/manager/html",
  "/cgi-bin/",
  "/console/",
  "/jenkins/",
  "/private/",
  "/backup/",
  "/backups/",
];

function isScannerPath(pathname) {
  if (SCANNER_PATHS_EXACT.has(pathname)) return true;
  for (const prefix of SCANNER_PREFIXES) {
    if (pathname === prefix || pathname.startsWith(prefix + "/") || pathname.startsWith(prefix)) {
      return true;
    }
  }
  // Source-map probes — we don't ship source maps in prod (Vite default),
  // but the SPA fallback was returning 200 for these. Crisp 404 instead.
  if (pathname.startsWith("/assets/") && pathname.endsWith(".map")) {
    return true;
  }
  return false;
}

// Tiny static 404 body. No headers leaked, no useful info for scanners.
const NOT_FOUND_BODY = "Not Found";

export async function onRequest(context) {
  let pathname;
  try {
    pathname = new URL(context.request.url).pathname;
  } catch {
    // Malformed URL — let the platform handle it.
    return context.next();
  }

  if (isScannerPath(pathname)) {
    return new Response(NOT_FOUND_BODY, {
      status: 404,
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "public, max-age=3600",
        // Bots cache 404s for a while → fewer subsequent probes.
        // Inherit the global X-Content-Type-Options + Referrer-Policy
        // from the `_headers` file (CF merges middleware + _headers).
      },
    });
  }

  return context.next();
}
