#!/usr/bin/env node
/**
 * live-cp-manifest + live-cp-pngs + live-cp-perf — fast HTTP probes
 * against the live deploy. No browser needed; ~10 s end-to-end.
 *
 * Catches:
 *   - manifest.json HTTP 4xx/5xx after a botched deploy
 *   - manifest.generated_at older than the freshness window
 *   - any required layer missing from manifest
 *   - any layer's primary PNG returning 4xx/5xx, < 200 bytes, or
 *     undecodable (corrupt PNG header)
 *   - homepage TTFB / total-bytes regression
 *
 * Distinct from check_published.py (Python pipeline-side health
 * check) in two ways: this runs LIVE post-deploy in CI and emits
 * GitHub-friendly output, while check_published.py runs as part of
 * the daily refresh-data run + is the input to the watchdog.
 */
import { performance } from "node:perf_hooks";


const LIVE_BASE_URL = process.env.LIVE_BASE_URL || "https://shouldidive.com";
const MANIFEST_PATH = "/data/manifest.json";

// Layers a healthy live deploy MUST publish. Source of truth lives
// in tests/checkpoints/data-shape.test.js (REQUIRED_LAYERS); this
// list mirrors it. If you add a layer, update both.
const REQUIRED_LAYERS = [
  "sst", "sst7d", "sst5d", "chl", "kd490",
  "wind", "wind5d", "swell5d", "viz", "wave", "precip",
];

// manifest.generated_at staleness gates. Daily refresh runs at 06:00
// UTC; allow 30 h grace before flagging "stale," 36 h before flagging
// "critical" (= the deploy probably failed silently).
const STALE_HOURS    = 30;
const CRITICAL_HOURS = 36;

// Per-layer date staleness — same numbers as check_published.py.
// kd490 has the longest lag (NASA OB.DAAC SQ DINEOF product is ~11 d
// behind today by design).
const LAYER_DATE_MAX_DAYS = {
  sst: 4, chl: 7, kd490: 10, viz: 2,
  wind: 2, wave: 2, precip: 3,
};

// Performance budgets. Lenient — we're not running Lighthouse, just
// detecting "deploy regressed by 10×" not "shave 50ms from LCP."
const PERF_BUDGETS = {
  homepage_ttfb_ms:   3000,    // time to first byte from CF
  homepage_total_ms:  10000,   // full HTML download
  manifest_ttfb_ms:   3000,
};


// ---- HTTP helper -----------------------------------------------------

async function timedFetch(url, opts = {}) {
  const t0 = performance.now();
  const res = await fetch(url, {
    headers: { "User-Agent": "ShoudiDive-LiveDeployVerify/1.0", ...opts.headers },
  });
  const ttfb_ms = performance.now() - t0;
  let bytes = null;
  if (opts.readBody !== false) {
    const buf = await res.arrayBuffer();
    bytes = buf.byteLength;
    return { res, bytes, body: buf, total_ms: performance.now() - t0, ttfb_ms };
  }
  return { res, bytes, total_ms: performance.now() - t0, ttfb_ms };
}


// ---- Findings collector ----------------------------------------------

class Findings {
  constructor() { this.list = []; }
  fail(code, title, detail = "") {
    this.list.push({ severity: "fail", code, title, detail });
  }
  warn(code, title, detail = "") {
    this.list.push({ severity: "warn", code, title, detail });
  }
  info(code, title, detail = "") {
    this.list.push({ severity: "info", code, title, detail });
  }
  hasFailures() { return this.list.some((f) => f.severity === "fail"); }
  print() {
    for (const f of this.list) {
      const tag = f.severity.toUpperCase().padEnd(4);
      console.log(`  [${tag}] ${f.code}: ${f.title}`);
      if (f.detail) {
        for (const line of f.detail.split("\n")) {
          console.log(`         ${line}`);
        }
      }
    }
  }
}


// ---- Probes ----------------------------------------------------------

async function probeHomepage(findings) {
  const url = `${LIVE_BASE_URL}/?cb=live-${Date.now()}`;
  console.log(`[live-manifest] probing homepage: ${url}`);
  let r;
  try {
    r = await timedFetch(url);
  } catch (e) {
    findings.fail("homepage_unreachable",
      "Homepage fetch failed", `${e.name}: ${e.message}`);
    return;
  }
  if (r.res.status !== 200) {
    findings.fail("homepage_http_error",
      `Homepage returned HTTP ${r.res.status}`);
    return;
  }
  if (r.ttfb_ms > PERF_BUDGETS.homepage_ttfb_ms) {
    findings.warn("homepage_ttfb_slow",
      `Homepage TTFB ${r.ttfb_ms.toFixed(0)} ms (budget ${PERF_BUDGETS.homepage_ttfb_ms} ms)`);
  }
  if (r.total_ms > PERF_BUDGETS.homepage_total_ms) {
    findings.warn("homepage_total_slow",
      `Homepage total ${r.total_ms.toFixed(0)} ms (budget ${PERF_BUDGETS.homepage_total_ms} ms)`);
  }
  // Sanity: HTML body should reference the JS bundle and be at
  // least a few KB. A 1-byte response from CF means the deploy
  // failed mid-way and CF is serving a cached error page.
  if (r.bytes < 500) {
    findings.fail("homepage_too_small",
      `Homepage body only ${r.bytes} bytes (expected >500); ` +
      `deploy may have published an empty index.html.`);
    return;
  }
  const html = new TextDecoder().decode(r.body);
  const bundleMatch = html.match(/\/assets\/(index-[A-Za-z0-9_-]+\.js)/);
  if (!bundleMatch) {
    findings.fail("homepage_no_bundle",
      "Homepage HTML doesn't reference /assets/index-*.js — " +
      "Vite build output broken or HTML cached from a prior version");
    return;
  }
  findings.info("homepage_ok",
    `Homepage 200 in ${r.ttfb_ms.toFixed(0)} ms (${r.bytes} bytes), bundle ${bundleMatch[1]}`);
}


async function probeManifest(findings) {
  const url = `${LIVE_BASE_URL}${MANIFEST_PATH}?cb=live-${Date.now()}`;
  console.log(`[live-manifest] probing manifest: ${url}`);
  let r;
  try {
    r = await timedFetch(url);
  } catch (e) {
    findings.fail("manifest_unreachable",
      "Manifest fetch failed", `${e.name}: ${e.message}`);
    return null;
  }
  if (r.res.status !== 200) {
    findings.fail("manifest_http_error",
      `Manifest returned HTTP ${r.res.status}`);
    return null;
  }
  if (r.ttfb_ms > PERF_BUDGETS.manifest_ttfb_ms) {
    findings.warn("manifest_ttfb_slow",
      `Manifest TTFB ${r.ttfb_ms.toFixed(0)} ms (budget ${PERF_BUDGETS.manifest_ttfb_ms} ms)`);
  }
  let manifest;
  try {
    manifest = JSON.parse(new TextDecoder().decode(r.body));
  } catch (e) {
    findings.fail("manifest_invalid_json",
      "Manifest is not valid JSON", String(e));
    return null;
  }

  // Freshness gate.
  const generatedAt = manifest.generated_at;
  if (!generatedAt || typeof generatedAt !== "string") {
    findings.fail("manifest_no_generated_at",
      "Manifest is missing the generated_at timestamp");
  } else {
    const ageHours = (Date.now() - new Date(generatedAt).getTime()) / 3600_000;
    if (Number.isNaN(ageHours)) {
      findings.fail("manifest_unparseable_generated_at",
        `generated_at = ${generatedAt} is not a valid ISO timestamp`);
    } else if (ageHours > CRITICAL_HOURS) {
      findings.fail("manifest_critical_stale",
        `Manifest is ${ageHours.toFixed(1)} h stale (critical threshold ${CRITICAL_HOURS} h). ` +
        `The daily refresh has been failing for >36 h — investigate refresh-data.yml.`);
    } else if (ageHours > STALE_HOURS) {
      findings.warn("manifest_stale",
        `Manifest is ${ageHours.toFixed(1)} h stale (warn threshold ${STALE_HOURS} h)`);
    } else {
      findings.info("manifest_fresh",
        `Manifest fresh (${ageHours.toFixed(1)} h old, generated_at=${generatedAt})`);
    }
  }

  // Required layers.
  const got = Object.keys(manifest.layers || {});
  const missing = REQUIRED_LAYERS.filter((k) => !got.includes(k));
  if (missing.length) {
    findings.fail("manifest_missing_layers",
      `Manifest missing required layers: ${missing.join(", ")}`,
      `Got: ${got.join(", ")}\nExpected at least: ${REQUIRED_LAYERS.join(", ")}`);
  } else {
    findings.info("manifest_layers_complete",
      `All ${REQUIRED_LAYERS.length} required layers present`);
  }
  return manifest;
}


async function probePngs(findings, manifest) {
  if (!manifest) return;
  console.log(`[live-pngs] probing primary PNG of each grayscale layer`);
  // For each "windowed" layer (sst/chl/kd490/viz/wind/wave/precip),
  // probe the primary window's URL. Skip the 5d/7d summary layers —
  // their content is JSON, tested via probeManifest's freshness gate.
  const windowedLayers = ["sst", "chl", "kd490", "viz", "wind", "wave", "precip"];
  for (const id of windowedLayers) {
    const layer = manifest.layers?.[id];
    if (!layer) continue;
    const windows = layer.windows || {};
    // Pick the primary window (preference order = check_published.py).
    const primaryKey =
      windows["2d"] ? "2d" :
      windows["1d"] ? "1d" :
      windows["now"] ? "now" :
      Object.keys(windows)[0];
    if (!primaryKey) continue;
    const win = windows[primaryKey];
    // Multi-URL layers (wind has speed_url + uv_url, wave has wave_url).
    const urlFields = ["url", "speed_url", "uv_url", "wave_url"]
      .filter((k) => typeof win[k] === "string");
    if (urlFields.length === 0) {
      findings.warn(`layer_${id}_no_url`,
        `Layer ${id} window ${primaryKey} has no PNG url field`);
      continue;
    }
    for (const field of urlFields) {
      const url = `${LIVE_BASE_URL}${win[field]}`;
      let r;
      try {
        r = await timedFetch(url);
      } catch (e) {
        findings.fail(`layer_${id}_unreachable`,
          `${id}/${primaryKey}/${field} unreachable`, `${e.name}: ${e.message}`);
        continue;
      }
      if (r.res.status !== 200) {
        findings.fail(`layer_${id}_http_error`,
          `${id}/${primaryKey}/${field} HTTP ${r.res.status}`,
          `URL: ${url}`);
        continue;
      }
      if (r.bytes < 200) {
        findings.fail(`layer_${id}_too_small`,
          `${id}/${primaryKey}/${field} only ${r.bytes} bytes (likely 1×1 placeholder)`);
        continue;
      }
      // PNG signature: 89 50 4E 47 0D 0A 1A 0A
      const buf = new Uint8Array(r.body);
      if (buf[0] !== 0x89 || buf[1] !== 0x50 || buf[2] !== 0x4e || buf[3] !== 0x47) {
        findings.fail(`layer_${id}_corrupt`,
          `${id}/${primaryKey}/${field} body doesn't start with PNG signature`);
        continue;
      }
      findings.info(`layer_${id}_ok`,
        `${id}/${primaryKey}/${field}: ${r.bytes} bytes in ${r.ttfb_ms.toFixed(0)} ms`);

      // Per-layer date staleness (when manifest exposes dates).
      const dates = win.dates;
      const maxDays = LAYER_DATE_MAX_DAYS[id];
      if (Array.isArray(dates) && dates.length && maxDays != null) {
        const latest = dates[dates.length - 1];
        const ageDays = (Date.now() - new Date(latest + "T12:00:00Z").getTime()) / 86_400_000;
        if (ageDays > maxDays) {
          findings.warn(`layer_${id}_data_stale`,
            `${id}/${primaryKey} latest data ${ageDays.toFixed(1)} d old (threshold ${maxDays} d). ` +
            `Latest date: ${latest}.`);
        }
      }
    }
  }
}


// ---- Main ------------------------------------------------------------

async function main() {
  console.log(`[live-checkpoints] starting against ${LIVE_BASE_URL}\n`);
  const findings = new Findings();

  await probeHomepage(findings);
  const manifest = await probeManifest(findings);
  await probePngs(findings, manifest);

  console.log(`\n[live-checkpoints] findings:`);
  findings.print();
  if (findings.hasFailures()) {
    console.error(`\n[live-checkpoints] FAIL — at least one critical check failed`);
    return 1;
  }
  console.log(`\n[live-checkpoints] PASS — live deploy is healthy`);
  return 0;
}


main().then((code) => process.exit(code)).catch((e) => {
  console.error(`[live-checkpoints] FATAL: ${e?.stack || e}`);
  process.exit(2);
});
