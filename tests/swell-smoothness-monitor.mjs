#!/usr/bin/env node
/**
 * swell-smoothness-monitor — fetch the live Baja swell bucket PNG from
 * the dev preview, decode Hs per cell, walk transects across known
 * seam zones (WW3-wcoast Pacific edge, Vizcaíno peninsula coastal
 * transition, north/central/south Cortez), and FAIL if any single
 * adjacent-cell jump exceeds MAX_DELTA_FT.
 *
 * Background: gfswave WW3 masks shallow + enclosed water. The wind-
 * chop fallback in fetch_swell_5day.py blends SMB-derived wind seas
 * with WW3 swell using a 20-cell exponential decay. When the decay
 * is implemented correctly, transects across the WW3 model edge show
 * a smooth taper from ~4 m Hs offshore to ~0.3 m chop nearshore. When
 * it's broken, the same transect shows a single-cell jump of ~3 m
 * (~10 ft). The user reports the broken case visually as "hard cut
 * between strong offshore swell and flat nearshore."
 *
 * Usage:
 *   node tests/swell-smoothness-monitor.mjs
 *   PROBE_BASE_URL=https://shouldidive.com node tests/swell-smoothness-monitor.mjs
 *   PROBE_BUCKET=morning PROBE_DAY=0 node tests/swell-smoothness-monitor.mjs
 *
 * Exit codes:
 *   0 every monitored transect under threshold
 *   1 at least one transect has a sharp seam (see worst[] list)
 *   2 fetch / decode failure
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import puppeteer from "puppeteer";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ARTIFACTS = resolve(REPO_ROOT, "test-output", "swell-smoothness");

const BASE_URL = process.env.PROBE_BASE_URL || "https://dev.shouldidive.pages.dev";
const REGION   = process.env.PROBE_REGION   || "baja";
const DAY      = Number(process.env.PROBE_DAY || "0");
const BUCKET   = process.env.PROBE_BUCKET || "morning";

// Maximum acceptable single-cell jump in significant wave height (feet).
// Baja Pacific grid is ~0.08° (~9 km) per cell. At a 20-cell exponential
// decay, a 12 ft groundswell drops by ~0.6 ft per cell (5%/cell). The
// 2026-05-18 broken cliff was ~9 ft per cell at the Vizcaíno coast.
// 1.5 ft is the perceptual threshold — above this, the user reads it
// as "hard line" in the heatmap colour ramp.
const MAX_DELTA_FT = 1.5;

// Maximum acceptable Hs (in feet) at deep-Cortez locations far from
// any WW3-valid Pacific cell. With the fix, the exponential decay must
// not bleed Pacific groundswell across the peninsula and inflate
// Cortez values. The Cortez gets wind-chop only (typical 0.5–2 ft),
// so a deep-Cortez Hs over this cap means the decay scale or
// peninsula-blocking is wrong.
const MAX_DEEP_CORTEZ_HS_FT = 3.0;

// Transects walk in grid-cell space across known seam zones. Lat/lng
// are converted to grid indices using the manifest bbox + grid size.
// Each transect: {name, lat0, lng0, lat1, lng1, mode?: "smooth"|"low_cortez"}.
//   "smooth"     — every adjacent valid cell pair under MAX_DELTA_FT
//   "low_cortez" — every cell under MAX_DEEP_CORTEZ_HS_FT
const TRANSECTS = [
  // ---- Smoothness across the WW3 → wind-chop boundary -----------------
  // Cedros + Isla San Benito offshore edge. Extends EAST across the
  // peninsula INTO the Sea of Cortez — the user reported a hard line
  // exactly here ("9ft to 1ft seas").
  { name: "Cedros to Tiburon east-west",    lat0: 28.5, lng0: -118.0, lat1: 28.5, lng1: -112.0 },
  // Bahía Tortugas peninsula crossing.
  { name: "Tortugas to mainland east-west", lat0: 27.7, lng0: -117.5, lat1: 27.7, lng1: -111.5 },
  // Northern Pacific Baja — into Tiburón / Sonora.
  { name: "N Baja to Tiburon east-west",    lat0: 30.5, lng0: -117.8, lat1: 30.5, lng1: -113.0 },
  // Magdalena Bay across peninsula.
  { name: "Magdalena to Cabo Pulmo",        lat0: 24.5, lng0: -114.0, lat1: 24.5, lng1: -108.5 },
  // South tip — Cabo Falso wrap into Cabo Pulmo.
  { name: "Cabo south-rounding",            lat0: 22.8, lng0: -110.2, lat1: 23.4, lng1: -109.3 },

  // ---- Hard caps — deep Cortez should stay under wind-chop magnitude --
  // Mid Cortez at Salsipuedes / La Paz / Loreto. With the v3 fix, Hs
  // here should remain wind-chop-dominated (under 3 ft), not inflated
  // by Pacific swell bleeding across the peninsula.
  { name: "Salsipuedes (mid Cortez)",       lat0: 28.7, lng0: -112.7, lat1: 28.7, lng1: -112.3, mode: "low_cortez" },
  { name: "La Paz (south Cortez)",          lat0: 24.5, lng0: -110.3, lat1: 24.5, lng1: -109.5, mode: "low_cortez" },
  { name: "Loreto (central Cortez)",        lat0: 26.0, lng0: -111.3, lat1: 26.0, lng1: -110.3, mode: "low_cortez" },
];

async function probe(page, url) {
  return page.evaluate(async ({ url, transects, gridW, gridH, bbox, heightRange }) => {
    // Fetch the PNG, decode via createImageBitmap + offscreen canvas.
    const resp = await fetch(url, { cache: "no-cache" });
    if (!resp.ok) return { ok: false, error: `HTTP ${resp.status} on ${url}` };
    const blob = await resp.blob();
    const bmp = await createImageBitmap(blob);
    const canvas = new OffscreenCanvas(bmp.width, bmp.height);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(bmp, 0, 0);
    const img = ctx.getImageData(0, 0, bmp.width, bmp.height);
    const { data, width, height } = img;

    // PNG should match the grid (140x110 for swell).
    if (width !== gridW || height !== gridH) {
      return { ok: false, error: `Decoded ${width}x${height}; expected ${gridW}x${gridH}` };
    }

    // Decode helper: pixel (x, y) → {hsFt, valid}.
    function pix(x, y) {
      const i = (y * width + x) * 4;
      const a = data[i + 3];
      if (a === 0) return { hsFt: NaN, valid: false };
      const r = data[i];
      const hsM = (r / 255) * (heightRange[1] - heightRange[0]) + heightRange[0];
      return { hsFt: hsM * 3.28084, valid: true };
    }

    // Lat/lng → grid (x, y). Lat0 is grid top (north), lat1 is bottom.
    function ll2xy(lat, lng) {
      const fx = (lng - bbox.lng_min) / (bbox.lng_max - bbox.lng_min);
      const fy = (bbox.lat_max - lat) / (bbox.lat_max - bbox.lat_min);
      return { x: Math.max(0, Math.min(width - 1, Math.round(fx * (width - 1)))),
               y: Math.max(0, Math.min(height - 1, Math.round(fy * (height - 1)))) };
    }

    // Walk a transect cell-by-cell using Bresenham-ish stepping.
    function walk(t) {
      const a = ll2xy(t.lat0, t.lng0);
      const b = ll2xy(t.lat1, t.lng1);
      const dx = b.x - a.x, dy = b.y - a.y;
      const steps = Math.max(Math.abs(dx), Math.abs(dy));
      const samples = [];
      for (let s = 0; s <= steps; s++) {
        const f = steps === 0 ? 0 : s / steps;
        const x = Math.round(a.x + dx * f);
        const y = Math.round(a.y + dy * f);
        const p = pix(x, y);
        const lng = bbox.lng_min + (x / (width - 1)) * (bbox.lng_max - bbox.lng_min);
        const lat = bbox.lat_max - (y / (height - 1)) * (bbox.lat_max - bbox.lat_min);
        samples.push({ x, y, lat: +lat.toFixed(3), lng: +lng.toFixed(3),
                       hsFt: p.valid ? +p.hsFt.toFixed(2) : null, valid: p.valid });
      }
      // Compute max delta between adjacent VALID cells.
      let maxDelta = 0, maxAt = null;
      for (let i = 1; i < samples.length; i++) {
        const prev = samples[i - 1], cur = samples[i];
        if (prev.valid && cur.valid) {
          const d = Math.abs(cur.hsFt - prev.hsFt);
          if (d > maxDelta) {
            maxDelta = d;
            maxAt = { from: prev, to: cur };
          }
        }
      }
      // Max Hs anywhere on this transect (for low_cortez checks).
      let maxHs = 0, maxHsAt = null;
      for (const s of samples) {
        if (s.valid && s.hsFt > maxHs) { maxHs = s.hsFt; maxHsAt = s; }
      }
      // Count NaN runs inside the transect (excluding leading/trailing
      // run, which is "off the data grid" = expected).
      let firstValid = samples.findIndex(s => s.valid);
      let lastValid  = samples.length - 1 - [...samples].reverse().findIndex(s => s.valid);
      let interiorNaN = 0;
      if (firstValid >= 0 && lastValid >= firstValid) {
        for (let i = firstValid; i <= lastValid; i++) {
          if (!samples[i].valid) interiorNaN++;
        }
      }
      return { name: t.name, mode: t.mode || "smooth", samples,
               maxDelta, maxAt, maxHs, maxHsAt, interiorNaN,
               cells: samples.length };
    }

    const results = transects.map(walk);
    return { ok: true, results };
  }, { url, transects: TRANSECTS, gridW: 140, gridH: 110,
        bbox: { lat_min: 22.0, lat_max: 32.6, lng_min: -118.0, lng_max: -106.5 },
        heightRange: [0.0, 12.0] });
}

async function main() {
  mkdirSync(ARTIFACTS, { recursive: true });
  const url = `${BASE_URL}/data/${REGION}/swell/buckets/d${DAY}_${BUCKET}_wave.png`;
  console.log(`probing ${url}`);
  console.log(`max single-cell delta allowed: ${MAX_DELTA_FT.toFixed(1)} ft`);

  const browser = await puppeteer.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  let exit = 0;
  try {
    const page = await browser.newPage();
    // Navigate to a STATIC asset (the manifest JSON) so we're in the
    // dev URL's origin without the SPA's client-side router destroying
    // the execution context mid-evaluate. From this page, fetching the
    // swell PNG is same-origin and createImageBitmap can decode it.
    const stagingUrl = `${BASE_URL}/data/${REGION}/manifest.json`;
    await page.goto(stagingUrl, { waitUntil: "networkidle0", timeout: 60_000 });
    const out = await probe(page, url);
    if (!out.ok) {
      console.error(`FAIL: ${out.error}`);
      writeFileSync(resolve(ARTIFACTS, "summary.json"),
        JSON.stringify({ ok: false, error: out.error, url }, null, 2));
      exit = 2;
      return;
    }

    const failures = [];
    for (const r of out.results) {
      let flag = false, reason = "";
      if (r.mode === "low_cortez") {
        flag = r.maxHs > MAX_DEEP_CORTEZ_HS_FT;
        reason = flag ? `Pacific swell over-bleed (${r.maxHs.toFixed(2)} ft > ${MAX_DEEP_CORTEZ_HS_FT} ft)` : "";
      } else {
        flag = r.maxDelta > MAX_DELTA_FT;
        reason = flag ? `seam (Δmax ${r.maxDelta.toFixed(2)} ft > ${MAX_DELTA_FT} ft)` : "";
      }
      const status = flag ? "FAIL" : "ok  ";
      const detail = r.mode === "low_cortez"
        ? (r.maxHsAt ? `maxHs=${r.maxHs.toFixed(2)} ft at ${r.maxHsAt.lat},${r.maxHsAt.lng}` : "no valid samples")
        : (r.maxAt ? `Δmax=${r.maxDelta.toFixed(2)} ft  worst ${r.maxAt.from.hsFt}→${r.maxAt.to.hsFt} ft at ${r.maxAt.to.lat},${r.maxAt.to.lng}` : "no valid samples");
      console.log(`  ${status} [${r.mode}] ${r.name.padEnd(34)} ${detail}${reason ? `  ← ${reason}` : ""}`);
      if (flag) failures.push(r);
    }

    const summary = { ok: failures.length === 0, url,
                      max_delta_ft: MAX_DELTA_FT,
                      max_deep_cortez_hs_ft: MAX_DEEP_CORTEZ_HS_FT,
                      total: out.results.length, failures: failures.length,
                      results: out.results };
    writeFileSync(resolve(ARTIFACTS, "summary.json"),
                  JSON.stringify(summary, null, 2));
    console.log(`\nartifact: ${resolve(ARTIFACTS, "summary.json")}`);

    if (failures.length === 0) {
      console.log(`PASS — all ${out.results.length} transects pass`);
      exit = 0;
    } else {
      console.log(`FAIL — ${failures.length}/${out.results.length} transects flagged`);
      exit = 1;
    }
  } catch (e) {
    console.error(`probe crashed: ${e && e.stack || e}`);
    exit = 2;
  } finally {
    await browser.close();
    process.exit(exit);
  }
}

main();
