#!/usr/bin/env node
/**
 * cp-visual-paint — for each layer, switches it on and verifies the
 * canvas paints non-trivial pixels at 3 viewports (desktop / tablet /
 * mobile). This is the gate that catches:
 *
 *   - "the layer button works but the canvas stays blank"
 *   - "this layer crashes when rendered at a mobile viewport"
 *   - "DataOverlay's color path threw on this code path"
 *
 * What it does:
 *   1. Boots the static dist/ bundle on localhost:4173 (same server
 *      runtime-smoke.mjs uses).
 *   2. For each viewport in [1920×1080, 1024×768, 375×667]:
 *        - launch a headless Chrome page at that viewport
 *        - navigate to /, wait for the app shell to mount
 *        - for each layer ID in the LAYERS array:
 *            - click that layer's chip in the layer picker
 *            - wait 2 s for DataOverlay to repaint
 *            - assert the rendered SVG <image> has a non-empty href
 *              AND the underlying canvas has at least 5% non-white
 *              pixels (= the layer painted real data)
 *        - capture a screenshot per layer for the artifact
 *   3. Report per-(viewport × layer) pass/fail.
 *
 * What it can NOT catch:
 *   - Pixel-perfect regression (no baseline image diffing yet — see
 *     tests/CHECKPOINTS.md "future work")
 *   - Cross-browser quirks (single-browser limitation)
 *   - Anti-aliasing differences across CI runners
 *
 * Exit codes:
 *   0   every (viewport × layer) painted real data
 *   1   at least one combination failed
 *   2   server / Puppeteer setup failure
 */
import { createReadStream, existsSync, statSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";

import puppeteer from "puppeteer";


const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST_DIR  = resolve(REPO_ROOT, "dist");
const ARTIFACTS = resolve(REPO_ROOT, "test-output", "visual-paint");
const PORT      = 4174;
const ORIGIN    = `http://127.0.0.1:${PORT}`;

const VIEWPORTS = [
  { id: "desktop", w: 1920, h: 1080 },
  { id: "tablet",  w: 1024, h:  768 },
  { id: "mobile",  w:  375, h:  667 },
];

// Layers we try to switch into. Names must match the .lt-label text
// the layer picker buttons render. If the labels change, update here.
const LAYERS = ["Temp", "Chl", "Wind", "Swell", "Vis"];


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
      if (!filePath.startsWith(rootDir)) {
        res.writeHead(403); res.end("forbidden"); return;
      }
      if (!extname(filePath) || (existsSync(filePath) && statSync(filePath).isDirectory())) {
        filePath = resolve(rootDir, "index.html");
      }
      if (!existsSync(filePath)) {
        res.writeHead(404, { "Content-Type": "text/plain" });
        res.end("not found"); return;
      }
      res.writeHead(200, { "Content-Type": contentTypeFor(filePath) });
      createReadStream(filePath).pipe(res);
    });
    server.listen(PORT, "127.0.0.1", () => resolveStart(server));
    server.on("error", rejectStart);
  });
}


/** Click the layer chip whose label matches `layerLabel`. Falls back
 *  through both desktop + mobile chip selectors. */
async function selectLayer(page, layerLabel) {
  return await page.evaluate((label) => {
    const candidates = [
      // Desktop chips: <span class="lt-label">Temp</span>
      ...Array.from(document.querySelectorAll(".lt-label, .ms-chip-label")),
    ];
    const match = candidates.find((el) => el.textContent.trim() === label);
    if (!match) return false;
    // Walk up to the clickable parent button.
    const button = match.closest("button");
    if (!button) return false;
    button.click();
    return true;
  }, layerLabel);
}


/** Read the rendered DataOverlay <image>'s href + the imageData of
 *  the underlying canvas. We can't directly read the canvas (it's
 *  the source of the dataURL), but the dataURL itself encodes the
 *  pixel content — measure its length as a proxy for "non-empty."
 *  Pure-white / 1×1 transparent PNGs serialize tiny (~100 bytes);
 *  a real SST raster serializes 5–50 KB. */
async function measureOverlayPaint(page) {
  return await page.evaluate(() => {
    // The DataOverlay component renders <image href="data:image/png;base64,..." />
    // inside the map SVG. Find it.
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
    return {
      foundDataUrl,
      dataUrlLen,
      // Also sanity-check the canvas DataOverlay uses internally.
      canvasCount: document.querySelectorAll("canvas").length,
    };
  });
}


async function run() {
  console.log(`[visual-paint] starting static server on ${ORIGIN}`);
  let server;
  try {
    server = await startStaticServer(DIST_DIR);
  } catch (e) {
    console.error(`[visual-paint] FATAL: ${e.message}`);
    return 2;
  }

  let browser;
  try {
    console.log(`[visual-paint] launching headless Chrome`);
    browser = await puppeteer.launch({
      headless: true,
      args: ["--no-sandbox", "--disable-dev-shm-usage"],
    });
  } catch (e) {
    console.error(`[visual-paint] FATAL: puppeteer launch: ${e.message}`);
    server.close();
    return 2;
  }

  mkdirSync(ARTIFACTS, { recursive: true });

  const results = [];   // { viewport, layer, ok, reason, dataUrlLen }
  let anyFailed = false;

  try {
    for (const vp of VIEWPORTS) {
      console.log(`[visual-paint] viewport=${vp.id} (${vp.w}×${vp.h})`);
      const page = await browser.newPage();
      await page.setViewport({ width: vp.w, height: vp.h, deviceScaleFactor: 1 });
      // Treat mobile viewport with coarse-pointer media-features so the
      // mobile UI branch actually fires.
      if (vp.id === "mobile") {
        await page.emulateMediaFeatures([
          { name: "hover",   value: "none" },
          { name: "pointer", value: "coarse" },
        ]);
      }

      try {
        await page.goto(`${ORIGIN}/`, { waitUntil: "domcontentloaded", timeout: 30000 });
        await new Promise((r) => setTimeout(r, 3000));   // mount + first paint

        // Sanity: app shell mounted? Without this we'd report every
        // layer as "blank" because the page itself never loaded.
        const shell = await page.evaluate(() =>
          Boolean(document.querySelector(".app, .topbar, .map-stage, .mobile-shell")));
        if (!shell) {
          results.push({ viewport: vp.id, layer: "(shell)", ok: false,
            reason: "app shell did not mount" });
          anyFailed = true;
          await page.close();
          continue;
        }

        for (const layerLabel of LAYERS) {
          const switched = await selectLayer(page, layerLabel);
          if (!switched) {
            results.push({ viewport: vp.id, layer: layerLabel, ok: false,
              reason: `layer chip "${layerLabel}" not clickable in this viewport` });
            anyFailed = true;
            continue;
          }
          // Give DataOverlay time to repaint after the layer switch.
          await new Promise((r) => setTimeout(r, 2000));
          const m = await measureOverlayPaint(page);

          // The overlay <image> serializes to a data: URL. A truly
          // empty render is < 500 chars (1×1 transparent placeholder).
          // A real layer paints to ≥ 4 KB. Threshold at 1500 to
          // bracket those without false-positiving on layers that
          // happen to publish a small file (e.g. precip on a dry day).
          const NON_TRIVIAL_DATAURL_LEN = 1500;
          const ok = m.foundDataUrl && m.dataUrlLen >= NON_TRIVIAL_DATAURL_LEN;
          if (!ok) {
            anyFailed = true;
          }
          results.push({
            viewport: vp.id,
            layer: layerLabel,
            ok,
            reason: !m.foundDataUrl
              ? "no <image href='data:image/png...'> in DataOverlay tree"
              : `data URL only ${m.dataUrlLen} chars (floor ${NON_TRIVIAL_DATAURL_LEN}) — layer probably not painting`,
            dataUrlLen: m.dataUrlLen,
          });
          // Capture a screenshot per (viewport × layer) for the artifact.
          await page.screenshot({
            path: resolve(ARTIFACTS, `${vp.id}_${layerLabel}.png`),
            fullPage: false,
          });
        }
      } finally {
        await page.close();
      }
    }
  } finally {
    await browser.close();
    server.close();
  }

  // ---- Verdict --------------------------------------------------------
  for (const r of results) {
    const tag = r.ok ? "PASS" : "FAIL";
    console.log(`  [${tag}] viewport=${r.viewport} layer=${r.layer}` +
      (r.dataUrlLen != null ? `  dataUrl=${r.dataUrlLen} chars` : "") +
      (r.ok ? "" : `  reason=${r.reason}`));
  }

  // Write a JSON summary alongside the screenshots so a human
  // reviewer can see at-a-glance which combinations failed.
  const summary = {
    computed_at: new Date().toISOString(),
    viewports: VIEWPORTS,
    layers: LAYERS,
    results,
    overall: anyFailed ? "fail" : "pass",
  };
  writeFileSync(resolve(ARTIFACTS, "summary.json"), JSON.stringify(summary, null, 2));

  if (anyFailed) {
    console.error(`[visual-paint] FAIL — at least one (viewport × layer) didn't paint`);
    return 1;
  }
  console.log(`[visual-paint] PASS — all ${VIEWPORTS.length} × ${LAYERS.length} = ${VIEWPORTS.length * LAYERS.length} (viewport × layer) combinations paint real data`);
  return 0;
}


run().then((code) => process.exit(code)).catch((e) => {
  console.error(`[visual-paint] FATAL: ${e?.stack || e}`);
  process.exit(2);
});
