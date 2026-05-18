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
    let imageRect = null;
    let imageAttrs = null;
    let parentGAttrs = null;
    let dataOverlayG = null;
    for (const img of imgs) {
      const href = img.getAttribute("href") || img.getAttribute("xlink:href") || "";
      if (!firstHref && href) firstHref = href.slice(0, 60);
      if (href.startsWith("data:image/png")) {
        dataUrlLen = href.length;
        foundDataUrl = true;
        const r = img.getBoundingClientRect();
        imageRect = { x: r.x, y: r.y, w: r.width, h: r.height };
        imageAttrs = {
          x: img.getAttribute("x"),
          y: img.getAttribute("y"),
          width: img.getAttribute("width"),
          height: img.getAttribute("height"),
          preserveAspectRatio: img.getAttribute("preserveAspectRatio"),
        };
        const parent = img.parentElement;
        if (parent) {
          const cs = window.getComputedStyle(parent);
          parentGAttrs = {
            tagName: parent.tagName,
            className: parent.getAttribute("class") || "",
            opacity: parent.getAttribute("opacity") || cs.opacity,
            display: cs.display,
            visibility: cs.visibility,
            clipPath: parent.getAttribute("clip-path") || cs.clipPath,
            mask: parent.getAttribute("mask") || cs.mask,
          };
          // Walk one more level up to find the wrapping ocean-clip <g>
          const grandparent = parent.parentElement;
          if (grandparent) {
            dataOverlayG = {
              tagName: grandparent.tagName,
              className: grandparent.getAttribute("class") || "",
              clipPath: grandparent.getAttribute("clip-path") || "",
              mask: grandparent.getAttribute("mask") || "",
            };
          }
        }
        break;
      }
    }
    // Also sample colors at known ocean coordinates by drawing the SVG
    // to a canvas and reading pixel values at fixed offsets.
    const svg = document.querySelector("svg.map-svg");
    let svgInfo = null;
    if (svg) {
      const r = svg.getBoundingClientRect();
      svgInfo = {
        viewBox: svg.getAttribute("viewBox"),
        bboxClient: { x: r.x, y: r.y, w: r.width, h: r.height },
        preserveAspectRatio: svg.getAttribute("preserveAspectRatio"),
      };
    }
    // Sample a pixel color from the map area using getComputedStyle
    // on the <image>'s parent — also walk up the SVG tree counting
    // ancestors with clip-path / mask attributes that might be hiding
    // the data overlay.
    let clipMaskAncestors = [];
    if (foundDataUrl) {
      let el = imgs[0].parentElement;
      while (el && el.tagName !== "BODY") {
        const cp = el.getAttribute && el.getAttribute("clip-path");
        const mk = el.getAttribute && el.getAttribute("mask");
        if (cp || mk) {
          clipMaskAncestors.push({
            tagName: el.tagName,
            className: (el.getAttribute("class") || ""),
            clipPath: cp || "",
            mask: mk || "",
          });
        }
        el = el.parentElement;
      }
    }
    return {
      foundDataUrl,
      dataUrlLen,
      firstHref,
      imageRect,
      imageAttrs,
      parentGAttrs,
      dataOverlayG,
      svgInfo,
      clipMaskAncestors,
    };
  });
}

// Decode the <image>'s data URL into a temp canvas and report color
// statistics. If the PNG itself is mostly transparent / one color,
// that explains "no data visible" independent of clip-path / mask.
async function inspectImagePixels(page) {
  return page.evaluate(async () => {
    const imgs = Array.from(document.querySelectorAll("svg image, image[href]"));
    let target = null;
    for (const img of imgs) {
      const href = img.getAttribute("href") || img.getAttribute("xlink:href") || "";
      if (href.startsWith("data:image/png")) { target = img; break; }
    }
    if (!target) return { error: "no data:image/png <image> found" };
    const href = target.getAttribute("href") || target.getAttribute("xlink:href");
    const dataUrl = href;
    const img = new Image();
    img.src = dataUrl;
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; });
    const cv = document.createElement("canvas");
    cv.width = img.naturalWidth;
    cv.height = img.naturalHeight;
    const ctx = cv.getContext("2d");
    ctx.drawImage(img, 0, 0);
    const px = ctx.getImageData(0, 0, cv.width, cv.height).data;
    let transparent = 0, opaque = 0;
    const colorBuckets = new Map();
    for (let i = 0; i < px.length; i += 4) {
      const a = px[i + 3];
      if (a < 16) { transparent++; continue; }
      opaque++;
      const r = Math.round(px[i] / 32) * 32;
      const g = Math.round(px[i+1] / 32) * 32;
      const b = Math.round(px[i+2] / 32) * 32;
      const key = `${r},${g},${b}`;
      colorBuckets.set(key, (colorBuckets.get(key) || 0) + 1);
    }
    const total = transparent + opaque;
    const topColors = [...colorBuckets.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([k, n]) => ({ rgb: k, frac: +(n / opaque).toFixed(3) }));
    return {
      pngWidth: cv.width,
      pngHeight: cv.height,
      totalCells: total,
      transparent,
      opaque,
      transparentFrac: +(transparent / total).toFixed(3),
      topColors,
    };
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
        imageRect:        m.imageRect,
        imageAttrs:       m.imageAttrs,
        parentGAttrs:     m.parentGAttrs,
        dataOverlayG:     m.dataOverlayG,
        svgInfo:          m.svgInfo,
        clipMaskAncestors: m.clipMaskAncestors,
      });
      console.log(`  imageRect=${JSON.stringify(m.imageRect)}`);
      console.log(`  imageAttrs=${JSON.stringify(m.imageAttrs)}`);
      console.log(`  parentGAttrs=${JSON.stringify(m.parentGAttrs)}`);
      console.log(`  clipMaskAncestors=${JSON.stringify(m.clipMaskAncestors)}`);
      console.log(`  svgInfo=${JSON.stringify(m.svgInfo)}`);
      const pixels = await inspectImagePixels(page);
      console.log(`  pixels=${JSON.stringify(pixels)}`);
      await page.screenshot({
        path: resolve(ARTIFACTS, `${label.toLowerCase()}_full.png`),
        fullPage: false,
      });
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
