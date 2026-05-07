#!/usr/bin/env node
/**
 * Runtime smoke test — boots the production Vite bundle in a headless
 * browser and watches for any error that fires during first paint.
 *
 * This is the gate that would have caught commit 7d641696 before it
 * shipped: a `ReferenceError: sstViewMode is not defined` thrown
 * from React's first render of <DesktopView/>. Vite's build doesn't
 * fail on undefined-variable references (JS is dynamic) and the
 * existing `node --test` suites don't actually execute the React
 * tree — they just grep source files for expected patterns. Together,
 * those two gaps let a white-screen bug ship to production.
 *
 * What this script asserts:
 *
 *   1. The bundle loads (HTTP 200 on / and on the JS chunks).
 *   2. There are no uncaught exceptions ("pageerror" events).
 *   3. There are no console.error lines from the page itself.
 *   4. After ~3 s the document has rendered the app shell — i.e. a
 *      `.app` or `.topbar` element exists. (Catches the case where
 *      the page silently fails to mount but the bundle "loaded.")
 *
 * Allowed by default: console warnings, network errors for the dev-
 * mock /data/manifest.json (the test serves the dist/, not the live
 * data — that's by design; we're testing the JS, not the data layer).
 *
 * Usage:    npm run build && npm run test:runtime-smoke
 * CI:       dev-checks.yml job `web-smoke`
 *
 * Exit codes:
 *   0   no errors detected, app shell rendered
 *   1   uncaught error, console.error, OR app shell missing
 *   2   server / Puppeteer setup failure (treat as gate failure)
 */
import { createReadStream, existsSync, statSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";

import puppeteer from "puppeteer";


const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST_DIR  = resolve(REPO_ROOT, "dist");
const PORT      = 4173;
const ORIGIN    = `http://127.0.0.1:${PORT}`;

// Only fail on errors that originate from the application code itself.
// The page may attempt to fetch /data/manifest.json against the static
// dist/ — those return 404 and that's expected; the data layer handles
// the missing manifest gracefully (state.ready=true with no layers).
// We also tolerate a couple of well-known network noise sources so the
// gate doesn't false-positive on infra blips.
const IGNORED_CONSOLE_PATTERNS = [
  /Failed to load resource: the server responded with a status of 404/i,
  /failed to fetch \/data\//i,
  /\/data\/manifest\.json/i,
  /loadManifest/i,            // dataSource.js logs at warn level on missing data
  /sst5d summary/i,           // same
  /sst7d summary/i,
  /wind5d/i, /swell5d/i, /current5d/i,
  /Failed to load /i,         // generic asset 404 from the static dist/ test
];


// ---- Tiny static file server -----------------------------------------
//
// We don't pull in `serve`, `sirv`, or even Vite's `vite preview`
// because (a) Vite preview adds another node process to manage, and
// (b) for a smoke test we only need to serve the static dist/ assets
// + 200-with-index.html for the SPA route. ~30 lines does the job.

function contentTypeFor(p) {
  const ext = extname(p).toLowerCase();
  return {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".mjs":  "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".svg":  "image/svg+xml",
    ".webp": "image/webp",
    ".ico":  "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
  }[ext] || "application/octet-stream";
}

function startStaticServer(rootDir) {
  return new Promise((resolveStart, rejectStart) => {
    if (!existsSync(rootDir)) {
      rejectStart(new Error(
        `dist/ directory missing at ${rootDir}. Run \`npm run build\` first.`,
      ));
      return;
    }
    const server = createServer((req, res) => {
      const url = new URL(req.url, ORIGIN);
      let filePath = resolve(rootDir, "." + url.pathname);
      // Don't escape the dist/ tree — defense in depth even though
      // this is local-only.
      if (!filePath.startsWith(rootDir)) {
        res.writeHead(403);
        res.end("forbidden");
        return;
      }
      // SPA index fallback: any path without an extension gets index.html.
      if (!extname(filePath) || (existsSync(filePath) && statSync(filePath).isDirectory())) {
        filePath = resolve(rootDir, "index.html");
      }
      if (!existsSync(filePath)) {
        res.writeHead(404, { "Content-Type": "text/plain" });
        res.end("not found");
        return;
      }
      res.writeHead(200, { "Content-Type": contentTypeFor(filePath) });
      createReadStream(filePath).pipe(res);
    });
    server.listen(PORT, "127.0.0.1", () => resolveStart(server));
    server.on("error", rejectStart);
  });
}


// ---- Smoke run --------------------------------------------------------

async function run() {
  console.log(`[smoke] starting static server on ${ORIGIN}`);
  let server;
  try {
    server = await startStaticServer(DIST_DIR);
  } catch (e) {
    console.error(`[smoke] FATAL: ${e.message}`);
    return 2;
  }

  let browser;
  try {
    console.log(`[smoke] launching headless Chrome`);
    browser = await puppeteer.launch({
      headless: true,
      // --no-sandbox required on the GitHub Actions runner.
      args: ["--no-sandbox", "--disable-dev-shm-usage"],
    });
  } catch (e) {
    console.error(`[smoke] FATAL: puppeteer launch failed: ${e.message}`);
    server.close();
    return 2;
  }

  const errors = [];
  const consoleErrors = [];

  try {
    const page = await browser.newPage();

    page.on("pageerror", (err) => {
      // Uncaught exceptions thrown by the page's JS — e.g. our
      // ReferenceError. ALWAYS a fail.
      errors.push({ kind: "pageerror", message: err.message, stack: err.stack });
    });

    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      const text = msg.text();
      if (IGNORED_CONSOLE_PATTERNS.some((re) => re.test(text))) return;
      consoleErrors.push(text);
    });

    page.on("requestfailed", (req) => {
      // Request failures go to the console.error path we already
      // monitor — don't double-count, but log for human debugging.
      console.log(`[smoke] requestfailed: ${req.url()} (${req.failure()?.errorText})`);
    });

    console.log(`[smoke] navigating to ${ORIGIN}/`);
    await page.goto(`${ORIGIN}/`, {
      waitUntil: "domcontentloaded",
      timeout: 30000,
    });

    // Give React a moment to mount + commit. 3 s is the same window
    // the Cloudflare deploy preview gives the app to settle.
    await new Promise((r) => setTimeout(r, 3000));

    // ---- App-shell sanity check --------------------------------------
    // If the JS threw before mount, the React root stays empty. Look
    // for ANY element produced by the app (any of these classes is
    // rendered by App.jsx within the first paint).
    const shellPresent = await page.evaluate(() => {
      return Boolean(
        document.querySelector(".app") ||
        document.querySelector(".topbar") ||
        document.querySelector(".map-stage") ||
        document.querySelector(".mobile-shell"),
      );
    });

    if (!shellPresent) {
      errors.push({
        kind: "no_app_shell",
        message:
          "App shell did not render — the bundle loaded but React did not commit. " +
          "Most likely cause: an uncaught error during first render (see pageerror log above).",
      });
    } else {
      console.log(`[smoke] app shell mounted`);
    }
  } finally {
    await browser.close();
    server.close();
  }

  // ---- Verdict --------------------------------------------------------
  if (errors.length === 0 && consoleErrors.length === 0) {
    console.log(`[smoke] PASS — no uncaught errors, no console.error, app shell rendered`);
    return 0;
  }

  console.error(`[smoke] FAIL — ${errors.length} runtime error(s), ${consoleErrors.length} console error(s)`);
  for (const e of errors) {
    console.error(`  [${e.kind}] ${e.message}`);
    if (e.stack) {
      console.error(e.stack.split("\n").slice(0, 6).map((l) => "    " + l).join("\n"));
    }
  }
  for (const c of consoleErrors) {
    console.error(`  [console.error] ${c}`);
  }
  return 1;
}


run().then((code) => process.exit(code)).catch((e) => {
  console.error(`[smoke] FATAL: ${e?.stack || e}`);
  process.exit(2);
});
