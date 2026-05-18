#!/usr/bin/env node
/**
 * probe-baja-live — diagnostic probe that runs against the LIVE dev
 * preview at dev.shouldidive.pages.dev with ?region=baja. Captures
 * console errors, network failures, DataOverlay data-URL lengths per
 * layer, and a screenshot per layer. Designed to answer the question
 * "does Baja actually paint data on the map" without requiring the
 * user to manually inspect their browser.
 *
 * Exit codes:
 *   0   every layer painted a real data URL
 *   1   at least one layer didn't paint (artifact has screenshots +
 *       summary.json with the failure list)
 *   2   probe couldn't even boot the page (puppeteer / network failure)
 *
 * Outputs under test-output/probe-baja-live/:
 *   - summary.json        — per-layer results + console/network errors
 *   - <layer>_full.png    — full-viewport screenshot per layer
 *   - <layer>_overlay.png — cropped to the map area for easier eyeballing
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import puppeteer from "puppeteer";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ARTIFACTS = resolve(REPO_ROOT, "test-output", "probe-baja-live");

const BASE_URL = process.env.PROBE_BASE_URL || "https://dev.shouldidive.pages.dev";
const REGION   = process.env.PROBE_REGION   || "baja";

// Layer chips — text content must match the .lt-label spans the React
// layer picker renders. Same set as cp-visual-paint.
const LAYERS = ["Temp", "Chl", "Wind", "Swell", "Vis"];

async function selectLayer(page, label) {
  return page.evaluate((l) => {
    // Match the visible label inside .lt-label, then click the
    // enclosing button. selectLayer in visual-paint.mjs uses the
    // same pattern.
    const btns = Array.from(document.querySelectorAll("button"));
    const target = btns.find(
      (b) => (b.textContent || "").trim().includes(l),
    );
    if (!target) return false;
    target.click();
    return true;
  }, label);
}

async function measureOverlay(page) {
  return page.evaluate(() => {
    const imgs = Array.from(document.querySelectorAll("svg image, image[href]"));
    let dataUrlLen = 0;
    let foundDataUrl = false;
    let firstHref = "";
    for (const img of imgs) {
      const href = img.getAttribute("href") || img.getAttribute("xlink:href") || "";
      if (!firstHref && href) firstHref = href.slice(0, 60);
      if (href.startsWith("data:image/png")) {
        dataUrlLen = href.length;
        foundDataUrl = true;
        break;
      }
    }
    return { foundDataUrl, dataUrlLen, firstHref };
  });
}

async function inspectRegionState(page) {
  return page.evaluate(() => ({
    region:         window.localStorage?.getItem("region") || "(unset)",
    urlSearch:      window.location.search,
    bundleHash:     (Array.from(document.scripts).find((s) =>
                       /\/assets\/index-[A-Za-z0-9_-]+\.js/.test(s.src))?.src || "")
                       .match(/index-[A-Za-z0-9_-]+\.js/)?.[0] || "(no match)",
    title:          document.title,
    tagline:        (document.querySelector(".tagline, .brand-tagline")?.textContent || "").trim(),
    legend:         (document.querySelector(".legend, .sst-legend")?.textContent || "").trim().slice(0, 80),
    savedSpotNames: Array.from(document.querySelectorAll(".spot-name, .ms-spot-name"))
                       .map((n) => (n.textContent || "").trim()).slice(0, 6),
  }));
}

async function run() {
  mkdirSync(ARTIFACTS, { recursive: true });

  let browser;
  try {
    browser = await puppeteer.launch({
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
      ],
    });
  } catch (e) {
    console.error(`[probe-baja] FATAL: puppeteer launch: ${e.message}`);
    return 2;
  }

  const pageErrors = [];
  const consoleEvents = [];
  const networkFails = [];

  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });
  await page.setUserAgent(
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
  );

  page.on("pageerror", (err) => {
    pageErrors.push({ message: err.message, stack: (err.stack || "").split("\n").slice(0, 5).join("\n") });
  });
  page.on("console", (msg) => {
    consoleEvents.push({ level: msg.type(), text: msg.text() });
  });
  page.on("response", (resp) => {
    const status = resp.status();
    const url = resp.url();
    if (status >= 400 && !url.includes("cloudflareinsights")) {
      networkFails.push({ status, url });
    }
  });

  let result = 1;
  const layerResults = [];
  let initial = null;

  try {
    const targetUrl = `${BASE_URL}/?region=${encodeURIComponent(REGION)}&cb=${Date.now()}`;
    console.log(`[probe-baja] navigating to ${targetUrl}`);
    const resp = await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
    if (!resp || resp.status() !== 200) {
      console.error(`[probe-baja] homepage HTTP ${resp?.status()} (expected 200)`);
      return 1;
    }
    // Let the app mount + manifest load.
    await new Promise((r) => setTimeout(r, 6000));

    initial = await inspectRegionState(page);
    console.log(`[probe-baja] bundle=${initial.bundleHash} region=${initial.region} url=${initial.urlSearch}`);
    console.log(`[probe-baja] tagline=${JSON.stringify(initial.tagline)}`);
    console.log(`[probe-baja] saved spots=${JSON.stringify(initial.savedSpotNames)}`);

    // Full-page screenshot before any layer manipulation.
    await page.screenshot({
      path: resolve(ARTIFACTS, "_initial_full.png"),
      fullPage: false,
    });

    // Dump the FULL chain of <g> ancestors from the DataOverlay <image>
    // up to the SVG root, with each ancestor's clip-path/mask attrs.
    // This tells us definitively whether the mask wrapper is still
    // applied (despite my conditional fix being in the bundle).
    const ancestry = await page.evaluate(() => {
      const imgs = Array.from(document.querySelectorAll("svg image, image[href]"));
      let target = null;
      for (const img of imgs) {
        const href = img.getAttribute("href") || img.getAttribute("xlink:href") || "";
        if (href.startsWith("data:image/png")) { target = img; break; }
      }
      if (!target) return { error: "no data:image/png <image>" };
      const chain = [];
      let el = target;
      while (el && el !== document.body) {
        chain.push({
          tag: el.tagName,
          cls: el.getAttribute("class") || "",
          clipPath: el.getAttribute("clip-path") || "",
          mask: el.getAttribute("mask") || "",
        });
        el = el.parentElement;
      }
      return { chainLength: chain.length, chain };
    });
    console.log(`[probe-baja] ancestry of DataOverlay <image>: ${JSON.stringify(ancestry, null, 2)}`);

    // One-time mask geometry inspection: pull the ocean-mask paths
    // and report their bounding boxes to determine if any land path
    // exceeds the inner bbox and is over-clipping the data overlay.
    const maskInfo = await page.evaluate(() => {
      const mask = document.getElementById("ocean-mask");
      const clip = document.getElementById("ocean-clip");
      if (!mask || !clip) return { error: "ocean-mask or ocean-clip not found" };
      const maskRect = mask.querySelector("rect");
      const maskPaths = Array.from(mask.querySelectorAll("path"));
      const clipPaths = Array.from(clip.querySelectorAll("path"));
      function describePath(p) {
        try {
          const bbox = p.getBBox ? p.getBBox() : null;
          const d = (p.getAttribute("d") || "");
          return {
            len: d.length,
            firstChars: d.slice(0, 80),
            bbox: bbox ? { x: bbox.x.toFixed(1), y: bbox.y.toFixed(1), w: bbox.width.toFixed(1), h: bbox.height.toFixed(1) } : null,
            fill: p.getAttribute("fill"),
            fillRule: p.getAttribute("fill-rule") || p.getAttribute("clip-rule"),
          };
        } catch (e) { return { error: e.message }; }
      }
      return {
        maskRect: maskRect ? {
          x: maskRect.getAttribute("x"),
          y: maskRect.getAttribute("y"),
          w: maskRect.getAttribute("width"),
          h: maskRect.getAttribute("height"),
          fill: maskRect.getAttribute("fill"),
        } : null,
        maskPathCount: maskPaths.length,
        maskPaths: maskPaths.slice(0, 5).map(describePath),
        clipPaths: clipPaths.map(describePath),
      };
    });
    console.log(`[probe-baja] mask geometry: ${JSON.stringify(maskInfo, null, 2)}`);

    for (const label of LAYERS) {
      console.log(`[probe-baja] layer=${label}`);
      const switched = await selectLayer(page, label);
      if (!switched) {
        layerResults.push({ layer: label, ok: false, reason: "chip not found", dataUrlLen: 0 });
        continue;
      }
      // DataOverlay's useEffect repaints synchronously after layer
      // state change; give the typed-array decode + toDataURL a beat.
      await new Promise((r) => setTimeout(r, 2500));
      const m = await measureOverlay(page);
      const NON_TRIVIAL = 1500;
      const ok = m.foundDataUrl && m.dataUrlLen >= NON_TRIVIAL;
      layerResults.push({
        layer:       label,
        ok,
        reason:      ok ? null : (m.foundDataUrl ? `data URL only ${m.dataUrlLen} chars` : `no data: URL (firstHref=${m.firstHref || "none"})`),
        dataUrlLen:  m.dataUrlLen,
        foundDataUrl: m.foundDataUrl,
      });
      await page.screenshot({
        path: resolve(ARTIFACTS, `${label.toLowerCase()}_full.png`),
        fullPage: false,
      });
      // Now strip the ocean-clip / ocean-mask from any ancestor <g>
      // wrapping the DataOverlay, AND hide the LandBasemap, AND bump
      // opacity to 1. Take another screenshot — if data is suddenly
      // visible, we've isolated the cause.
      const stripResult = await page.evaluate(() => {
        const imgs = Array.from(document.querySelectorAll("svg image, image[href]"));
        let modified = [];
        for (const img of imgs) {
          const href = img.getAttribute("href") || img.getAttribute("xlink:href") || "";
          if (!href.startsWith("data:image/png")) continue;
          // Walk ancestors, strip clip-path/mask attrs
          let el = img.parentElement;
          while (el && el.tagName !== "BODY") {
            if (el.getAttribute && (el.getAttribute("clip-path") || el.getAttribute("mask"))) {
              modified.push({
                tag: el.tagName,
                clipPath: el.getAttribute("clip-path"),
                mask: el.getAttribute("mask"),
              });
              el.removeAttribute("clip-path");
              el.removeAttribute("mask");
            }
            // Also bump opacity to 1 on the data-overlay group
            if (el.getAttribute && el.getAttribute("class") === "data-overlay") {
              el.setAttribute("opacity", "1");
              modified.push({ tag: el.tagName + ".data-overlay", opacityBumpedTo: 1 });
            }
            el = el.parentElement;
          }
          break; // first img only
        }
        // Hide LandBasemap so we can see all data over land (peninsula)
        const land = document.querySelector(".basemap.basemap-land");
        if (land) {
          land.style.display = "none";
          modified.push({ tag: "basemap-land", action: "display:none" });
        }
        return modified;
      });
      console.log(`  strip result: ${JSON.stringify(stripResult)}`);
      // Force a small re-render delay
      await new Promise((r) => setTimeout(r, 800));
      await page.screenshot({
        path: resolve(ARTIFACTS, `${label.toLowerCase()}_NOMASK.png`),
        fullPage: false,
      });
      // Re-instate the clip-path/mask for the next layer iteration by
      // forcing a React re-render via clicking around. Simpler: just
      // reload the page before the next layer.
      await page.reload({ waitUntil: "domcontentloaded" });
      await new Promise((r) => setTimeout(r, 6000));
    }

    const allOk = layerResults.every((r) => r.ok);
    if (allOk && pageErrors.length === 0) {
      result = 0;
    }
  } catch (e) {
    pageErrors.push({ message: `probe threw: ${e.message}`, stack: (e.stack || "").split("\n").slice(0, 5).join("\n") });
  } finally {
    await page.close();
    await browser.close();
  }

  const summary = {
    base_url:        BASE_URL,
    region:          REGION,
    computed_at:     new Date().toISOString(),
    initial,
    layer_results:   layerResults,
    page_errors:     pageErrors,
    console_events:  consoleEvents.filter((e) => e.level === "error" || e.level === "warning").slice(0, 50),
    network_fails:   networkFails.slice(0, 30),
    overall:         result === 0 ? "pass" : "fail",
  };
  writeFileSync(resolve(ARTIFACTS, "summary.json"), JSON.stringify(summary, null, 2));

  console.log("\n[probe-baja] === SUMMARY ===");
  for (const r of layerResults) {
    console.log(`  [${r.ok ? "PASS" : "FAIL"}] ${r.layer} dataUrl=${r.dataUrlLen}${r.reason ? `  reason=${r.reason}` : ""}`);
  }
  if (pageErrors.length) {
    console.log("\n[probe-baja] === PAGE ERRORS ===");
    for (const e of pageErrors) console.log(`  ${e.message}`);
  }
  if (consoleEvents.some((e) => e.level === "error")) {
    console.log("\n[probe-baja] === CONSOLE ERRORS ===");
    for (const e of consoleEvents.filter((x) => x.level === "error").slice(0, 20)) {
      console.log(`  ${e.text}`);
    }
  }
  if (networkFails.length) {
    console.log("\n[probe-baja] === NETWORK FAILURES ===");
    for (const n of networkFails.slice(0, 20)) console.log(`  ${n.status} ${n.url}`);
  }

  return result;
}

run().then((code) => process.exit(code)).catch((e) => {
  console.error(`[probe-baja] FATAL: ${e?.stack || e}`);
  process.exit(2);
});
