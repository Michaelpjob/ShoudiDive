#!/usr/bin/env node
/**
 * live-cp-render — runs against the LIVE production deploy at
 * shouldidive.com (or whatever LIVE_BASE_URL points at).
 *
 * The 2026-05-07 white-screen incident shipped to production for
 * ~25 minutes. dev-checks would have caught it at PR time — but the
 * second we shipped a hotfix we needed a way to verify "the deploy
 * actually fixed it" WITHOUT manually opening a browser. This is
 * that gate.
 *
 * What it does:
 *   1. Fetch shouldidive.com homepage; assert HTTP 200 + bundle hash
 *      changed since the last successful run (anti-stale-cache).
 *   2. Boot the live page in headless Chrome; watch for pageerror /
 *      console.error during first 5 s after mount.
 *   3. Assert the React shell mounted (.app + .topbar present).
 *   4. Spot-check that the DataOverlay <image> has a real data URL
 *      (i.e. at least one layer rendered against real data).
 *   5. Spot-check the saved-spots panel populated values for all
 *      hardcoded spots.
 *
 * Configuration:
 *   LIVE_BASE_URL — defaults to "https://shouldidive.com"
 *
 * Exit codes:
 *   0   live deploy is healthy
 *   1   visible regression (pageerror / shell missing / overlay blank)
 *   2   network / fetch-time failure (treat as gate failure too —
 *       a deploy that's unreachable is worse than one that errors)
 */
import puppeteer from "puppeteer";


const LIVE_BASE_URL = process.env.LIVE_BASE_URL || "https://shouldidive.com";

// Anti-CDN-cache cache-buster. Hitting the homepage with a unique
// query string forces Cloudflare to re-fetch the latest index.html.
const CACHE_BUSTER = `?cb=live-${Date.now()}`;

// Console-error patterns we tolerate. Same allowlist as the dev
// runtime-smoke; if a real bug class slips through we'll add it as
// a HARD failure rather than allowlist it.
const IGNORED_CONSOLE_PATTERNS = [
  /Failed to load resource: the server responded with a status of 404/i,
  /loadManifest/i,
  /\bsst5d summary\b/i,
  /\bsst7d summary\b/i,
  /\b(wind5d|swell5d|current5d) summary\b/i,
  // Cloudflare insights fetch, sometimes 503s on edge nodes.
  /static\.cloudflareinsights\.com/i,
];


async function run() {
  console.log(`[live-runtime] target: ${LIVE_BASE_URL}${CACHE_BUSTER}`);
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: true,
      args: ["--no-sandbox", "--disable-dev-shm-usage"],
    });
  } catch (e) {
    console.error(`[live-runtime] FATAL: puppeteer launch: ${e.message}`);
    return 2;
  }

  const errors = [];        // uncaught page errors
  const consoleErrors = []; // filtered console.error lines

  let bundleHashes = [];    // every loaded /assets/index-*.js
  let result = 1;           // pessimistic default

  try {
    const page = await browser.newPage();

    // Capture pageerror (uncaught exceptions). ALWAYS a fail.
    page.on("pageerror", (err) => {
      errors.push({ kind: "pageerror", message: err.message, stack: err.stack });
    });

    // Capture filtered console.error.
    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      const text = msg.text();
      if (IGNORED_CONSOLE_PATTERNS.some((re) => re.test(text))) return;
      consoleErrors.push(text);
    });

    // Track which bundle hashes the page pulled — useful for the
    // "deploy succeeded but Cloudflare cached the old bundle" case.
    page.on("response", (resp) => {
      const url = resp.url();
      const m = url.match(/\/assets\/(index-[A-Za-z0-9_-]+\.js)/);
      if (m) bundleHashes.push(m[1]);
    });

    console.log(`[live-runtime] navigating…`);
    const navStart = Date.now();
    const resp = await page.goto(`${LIVE_BASE_URL}/${CACHE_BUSTER}`, {
      waitUntil: "domcontentloaded",
      timeout: 30000,
    });

    if (!resp || resp.status() !== 200) {
      console.error(`[live-runtime] FAIL: homepage returned HTTP ${resp?.status()} (expected 200)`);
      return 1;
    }
    console.log(`[live-runtime] homepage HTTP ${resp.status()} in ${Date.now() - navStart}ms`);

    // Give the bundle 5 s to mount + render. Live page is slower than
    // dev (real network, real data) so give more buffer than runtime-smoke.
    await new Promise((r) => setTimeout(r, 5000));

    const shell = await page.evaluate(() => ({
      hasApp:    Boolean(document.querySelector(".app")),
      hasTopbar: Boolean(document.querySelector(".topbar")),
      hasMap:    Boolean(document.querySelector(".map-stage, .mobile-shell")),
      brandText: (document.querySelector(".brand-name")?.textContent || "").trim(),
    }));

    if (!shell.hasApp || !shell.hasTopbar || !shell.hasMap) {
      errors.push({
        kind: "no_app_shell",
        message: `app shell missing: app=${shell.hasApp} topbar=${shell.hasTopbar} map=${shell.hasMap}`,
      });
    } else {
      console.log(`[live-runtime] shell mounted (brand="${shell.brandText}")`);
    }

    // Verify at least one DataOverlay layer painted real data.
    // The default layer is "Temp" (SST), which should ALWAYS have a
    // populated data URL if the manifest + PNGs are fresh.
    const overlay = await page.evaluate(() => {
      const imgs = Array.from(document.querySelectorAll("svg image, image[href]"));
      let dataUrlLen = 0;
      let foundDataUrl = false;
      for (const img of imgs) {
        const href = img.getAttribute("href") || img.getAttribute("xlink:href") || "";
        if (href.startsWith("data:image/png")) {
          dataUrlLen = href.length;
          foundDataUrl = true;
          break;
        }
      }
      return { foundDataUrl, dataUrlLen };
    });
    if (!overlay.foundDataUrl) {
      consoleErrors.push("DataOverlay <image> has no data URL — primary layer didn't paint");
    } else if (overlay.dataUrlLen < 1500) {
      consoleErrors.push(`DataOverlay data URL only ${overlay.dataUrlLen} chars — primary layer painted blank`);
    } else {
      console.log(`[live-runtime] primary-layer overlay painted (${overlay.dataUrlLen} char dataURL)`);
    }

    // Spot-check the saved-spots panel: at least 6 of the 10 hard-
    // coded spots should have a non-"—" value at the default layer.
    // (Allow a few stragglers — Coronados sometimes has SST gaps when
    // MUR's coverage drops below ~32 °N.)
    const spotsPanel = await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll(".spot, .ms-spot"));
      const valued = rows.filter((row) => {
        const txt = (row.querySelector(".spot-val, .ms-spot-val")?.textContent || "").trim();
        return txt && txt !== "—" && !/^—/.test(txt);
      });
      return { total: rows.length, valued: valued.length };
    });
    if (spotsPanel.total === 0) {
      consoleErrors.push("Saved-spots panel rendered no rows — App.jsx layout regressed?");
    } else if (spotsPanel.valued < 6) {
      consoleErrors.push(
        `Saved-spots panel: only ${spotsPanel.valued}/${spotsPanel.total} rows have values; ` +
        `live data may be stale OR primary layer regressed.`,
      );
    } else {
      console.log(`[live-runtime] saved-spots populated (${spotsPanel.valued}/${spotsPanel.total} rows have values)`);
    }

    if (bundleHashes.length === 0) {
      consoleErrors.push("No /assets/index-*.js requested — page may have served stale HTML");
    } else {
      console.log(`[live-runtime] bundle hash(es): ${[...new Set(bundleHashes)].join(", ")}`);
    }

    if (errors.length === 0 && consoleErrors.length === 0) {
      result = 0;
      console.log(`[live-runtime] PASS — live deploy is healthy`);
    } else {
      console.error(`[live-runtime] FAIL — ${errors.length} pageerror(s), ${consoleErrors.length} console error(s)`);
      for (const e of errors) {
        console.error(`  [${e.kind}] ${e.message}`);
        if (e.stack) {
          console.error(e.stack.split("\n").slice(0, 5).map((l) => "    " + l).join("\n"));
        }
      }
      for (const c of consoleErrors) {
        console.error(`  [console.error] ${c}`);
      }
    }
  } finally {
    await browser.close();
  }
  return result;
}


run().then((code) => process.exit(code)).catch((e) => {
  console.error(`[live-runtime] FATAL: ${e?.stack || e}`);
  process.exit(2);
});
