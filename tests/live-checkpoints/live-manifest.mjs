#!/usr/bin/env node
/**
 * live-cp-manifest + live-cp-pngs + live-cp-perf — probes against the
 * live deploy, executed from inside a real headless-Chrome page.
 *
 * Catches:
 *   - manifest.json HTTP 4xx/5xx after a botched deploy
 *   - manifest.generated_at older than the freshness window
 *   - any required layer missing from manifest
 *   - any layer's primary PNG returning 4xx/5xx, < 200 bytes, or
 *     undecodable (corrupt PNG header)
 *   - homepage TTFB / total-bytes regression
 *
 * Transport (2026-06-12): probes run as in-page `fetch()` calls inside
 * a Puppeteer-driven Chrome that has loaded shouldidive.com — the same
 * client shape as a real user. The previous bare `node:fetch` transport
 * (even with a spoofed Chrome UA) was intermittently 403'd by
 * Cloudflare's bot scoring on GitHub-Actions runner IPs: the TLS
 * fingerprint of node's fetch doesn't match the claimed Chrome UA, so
 * runs flapped red for days with the data perfectly healthy (#130/#90).
 * live-cp-render never flapped because it IS real Chrome; this probe
 * now rides the same transport. Assertions and thresholds are
 * unchanged — this is a transport fix, not a check relaxation.
 *
 * Set LIVE_PROBE_TRANSPORT=fetch to fall back to the bare-fetch
 * transport (useful locally where your IP isn't bot-scored).
 *
 * Report mode: `--report <path>` additionally writes a
 * published_health.json-shaped report (same schema check_published.py
 * emits) so health-check.yml can consume this probe for its
 * "published deploy" section. Exit codes in all modes:
 *   0 — healthy (warnings allowed)
 *   1 — non-critical failures (e.g. one PNG corrupt)
 *   2 — critical failure (manifest unreachable/stale/missing layers)
 *
 * Distinct from check_published.py (Python pipeline-side health
 * check): this runs LIVE post-deploy in CI and emits GitHub-friendly
 * output, while check_published.py runs inside the refresh workflows
 * against the runner's own freshly-written files.
 */
import { performance } from "node:perf_hooks";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";


const LIVE_BASE_URL = process.env.LIVE_BASE_URL || "https://shouldidive.com";
const MANIFEST_PATH = "/data/manifest.json";
const TRANSPORT = process.env.LIVE_PROBE_TRANSPORT || "browser";

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

// Failure codes that mean "the deploy is fundamentally broken or
// unreachable" — these exit 2 (critical) rather than 1, and map to
// severity "critical" in --report mode. Everything else fail-severity
// maps to "high"; warns map to "medium".
const CRITICAL_CODES = new Set([
  "homepage_unreachable", "homepage_http_error", "homepage_too_small",
  "homepage_no_bundle",
  "manifest_unreachable", "manifest_http_error", "manifest_invalid_json",
  "manifest_no_generated_at", "manifest_unparseable_generated_at",
  "manifest_critical_stale", "manifest_missing_layers",
]);

// Same UA live-runtime.mjs presents — a real-looking Chrome rather
// than "HeadlessChrome/…" (the single biggest tell for bot filters).
const BROWSER_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36";


// ---- Transports --------------------------------------------------------
//
// Both expose the same interface:
//   homepage()  → { status, ttfb_ms, total_ms, bytes, html, headers }
//   probe(url, { wantText }) →
//     { status, ttfb_ms, total_ms, bytes, text?, head?, headers }
//   close()
//
// `head` is a Uint8Array of the first 8 body bytes (PNG signature
// checks); `headers` carries the Cloudflare diagnostics we print on
// failure so a blocked probe is attributable from the run log alone.

const DIAG_HEADERS = ["cf-ray", "cf-mitigated", "cf-cache-status", "server"];

function pickDiagHeaders(getter) {
  const out = {};
  for (const h of DIAG_HEADERS) {
    const v = getter(h);
    if (v != null && v !== "") out[h] = v;
  }
  return out;
}

function diagString(headers) {
  const parts = Object.entries(headers || {}).map(([k, v]) => `${k}=${v}`);
  return parts.length ? `CF diagnostics: ${parts.join(" ")}` : "";
}

async function makeFetchTransport() {
  async function timedFetch(url) {
    const t0 = performance.now();
    const res = await fetch(url, { headers: { "User-Agent": BROWSER_UA } });
    const ttfb_ms = performance.now() - t0;
    const buf = new Uint8Array(await res.arrayBuffer());
    return {
      status: res.status,
      ttfb_ms,
      total_ms: performance.now() - t0,
      bytes: buf.byteLength,
      buf,
      headers: pickDiagHeaders((h) => res.headers.get(h)),
    };
  }
  return {
    async homepage() {
      const r = await timedFetch(`${LIVE_BASE_URL}/?cb=live-${Date.now()}`);
      return { ...r, html: new TextDecoder().decode(r.buf) };
    },
    async probe(url, { wantText = false } = {}) {
      const r = await timedFetch(url);
      return {
        status: r.status, ttfb_ms: r.ttfb_ms, total_ms: r.total_ms,
        bytes: r.bytes, headers: r.headers,
        text: wantText ? new TextDecoder().decode(r.buf) : undefined,
        head: r.buf.slice(0, 8),
      };
    },
    async close() {},
  };
}

async function makeBrowserTransport() {
  const { default: puppeteer } = await import("puppeteer");
  // Launch + masking mirrors tests/live-checkpoints/live-runtime.mjs
  // (2026-05-18 lineage) — the probe configuration that has passed
  // Cloudflare consistently from GHA runners.
  const browser = await puppeteer.launch({
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--disable-blink-features=AutomationControlled",
    ],
  });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });
  await page.setUserAgent(BROWSER_UA);

  let homepageResult = null;

  async function navigate() {
    const t0 = Date.now();
    const resp = await page.goto(`${LIVE_BASE_URL}/?cb=live-${Date.now()}`, {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
    const total_ms = Date.now() - t0;
    const status = resp ? resp.status() : 0;
    const headers = resp ? pickDiagHeaders((h) => resp.headers()[h]) : {};
    const html = await page.content();
    const ttfb_ms = await page.evaluate(() => {
      const nav = performance.getEntriesByType("navigation")[0];
      return nav ? nav.responseStart - nav.requestStart : null;
    }).catch(() => null);
    return {
      status, total_ms, ttfb_ms: ttfb_ms ?? total_ms,
      bytes: new TextEncoder().encode(html).byteLength, html, headers,
    };
  }

  homepageResult = await navigate();
  if (homepageResult.status === 403) {
    // Likely a Cloudflare managed challenge on first contact. Real
    // Chrome resolves it in-page; give it a moment, then renavigate
    // once. (live-cp-render has never needed this, but a single retry
    // is cheap insurance against first-touch challenge latency.)
    console.log("[live-manifest] homepage 403 on first contact — waiting 8 s for challenge to settle, then retrying once");
    await new Promise((r) => setTimeout(r, 8000));
    homepageResult = await navigate();
  }

  // Once the homepage probe is captured, park the page on the manifest
  // URL itself (a scriptless JSON document) and run all subsequent
  // in-page fetches from there. Probing from the live app page is a
  // trap: the SPA can navigate/reload underneath us (service-worker
  // update, region routing) which destroys the evaluate() execution
  // context mid-probe ("Execution context was destroyed").
  async function navProbe(url) {
    const t0 = Date.now();
    const resp = await page.goto(url, {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
    const total_ms = Date.now() - t0;
    if (!resp) {
      const err = new Error("navigation returned no response");
      err.name = "NavigationError";
      throw err;
    }
    const text = await resp.text();
    const ttfb_ms = await page.evaluate(() => {
      const nav = performance.getEntriesByType("navigation")[0];
      return nav ? nav.responseStart - nav.requestStart : null;
    }).catch(() => null);
    return {
      status: resp.status(), ttfb_ms: ttfb_ms ?? total_ms, total_ms,
      bytes: new TextEncoder().encode(text).byteLength, text,
      headers: pickDiagHeaders((h) => resp.headers()[h]),
    };
  }

  return {
    async homepage() { return homepageResult; },
    async probe(url, { wantText = false, navigate: navigateTo = false } = {}) {
      if (navigateTo) return navProbe(url);
      const r = await page.evaluate(async (u, wantTextIn) => {
        const t0 = performance.now();
        try {
          const res = await fetch(u, { cache: "no-store" });
          const ttfb = performance.now() - t0;
          const buf = new Uint8Array(await res.arrayBuffer());
          // btoa over a small slice only — PNG signature needs 8 bytes.
          let headB64 = "";
          const head = buf.slice(0, 8);
          let bin = "";
          for (const b of head) bin += String.fromCharCode(b);
          headB64 = btoa(bin);
          const hdrs = {};
          for (const h of ["cf-ray", "cf-mitigated", "cf-cache-status", "server"]) {
            const v = res.headers.get(h);
            if (v) hdrs[h] = v;
          }
          return {
            ok: true, status: res.status,
            ttfb_ms: ttfb, total_ms: performance.now() - t0,
            bytes: buf.byteLength, headB64,
            text: wantTextIn ? new TextDecoder().decode(buf) : undefined,
            headers: hdrs,
          };
        } catch (e) {
          return { ok: false, errName: e.name, errMessage: e.message };
        }
      }, url, wantText);
      if (!r.ok) {
        const err = new Error(r.errMessage);
        err.name = r.errName || "FetchError";
        throw err;
      }
      const head = Uint8Array.from(atob(r.headB64), (c) => c.charCodeAt(0));
      return {
        status: r.status, ttfb_ms: r.ttfb_ms, total_ms: r.total_ms,
        bytes: r.bytes, text: r.text, head, headers: r.headers,
      };
    },
    async close() { await browser.close(); },
  };
}


// ---- Findings collector ----------------------------------------------

class Findings {
  constructor() { this.list = []; }
  fail(code, title, detail = "", extra = {}) {
    this.list.push({ severity: "fail", code, title, detail, ...extra });
  }
  warn(code, title, detail = "", extra = {}) {
    this.list.push({ severity: "warn", code, title, detail, ...extra });
  }
  info(code, title, detail = "") {
    this.list.push({ severity: "info", code, title, detail });
  }
  hasFailures() { return this.list.some((f) => f.severity === "fail"); }
  hasCriticalFailures() {
    return this.list.some((f) => f.severity === "fail" && CRITICAL_CODES.has(f.code));
  }
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

async function probeHomepage(findings, transport) {
  console.log(`[live-manifest] probing homepage: ${LIVE_BASE_URL}/`);
  let r;
  try {
    r = await transport.homepage();
  } catch (e) {
    findings.fail("homepage_unreachable",
      "Homepage fetch failed", `${e.name}: ${e.message}`);
    return;
  }
  if (r.status !== 200) {
    findings.fail("homepage_http_error",
      `Homepage returned HTTP ${r.status}`, diagString(r.headers));
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
  const bundleMatch = r.html.match(/\/assets\/(index-[A-Za-z0-9_-]+\.js)/);
  if (!bundleMatch) {
    findings.fail("homepage_no_bundle",
      "Homepage HTML doesn't reference /assets/index-*.js — " +
      "Vite build output broken or HTML cached from a prior version");
    return;
  }
  findings.info("homepage_ok",
    `Homepage 200 in ${r.ttfb_ms.toFixed(0)} ms (${r.bytes} bytes), bundle ${bundleMatch[1]}`);
}


async function probeManifest(findings, transport) {
  const url = `${LIVE_BASE_URL}${MANIFEST_PATH}?cb=live-${Date.now()}`;
  console.log(`[live-manifest] probing manifest: ${url}`);
  let r;
  try {
    // navigate: true — in browser transport this loads the manifest as
    // the page document (and later PNG fetches run from that stable,
    // scriptless context). Fetch transport ignores the flag.
    r = await transport.probe(url, { wantText: true, navigate: true });
  } catch (e) {
    findings.fail("manifest_unreachable",
      "Manifest fetch failed", `${e.name}: ${e.message}`,
      { url: MANIFEST_PATH });
    return null;
  }
  if (r.status !== 200) {
    findings.fail("manifest_http_error",
      `Manifest returned HTTP ${r.status}`, diagString(r.headers),
      { url: MANIFEST_PATH });
    return null;
  }
  if (r.ttfb_ms > PERF_BUDGETS.manifest_ttfb_ms) {
    findings.warn("manifest_ttfb_slow",
      `Manifest TTFB ${r.ttfb_ms.toFixed(0)} ms (budget ${PERF_BUDGETS.manifest_ttfb_ms} ms)`);
  }
  let manifest;
  try {
    manifest = JSON.parse(r.text);
  } catch (e) {
    findings.fail("manifest_invalid_json",
      "Manifest is not valid JSON", String(e), { url: MANIFEST_PATH });
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


async function probePngs(findings, transport, manifest) {
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
        `Layer ${id} window ${primaryKey} has no PNG url field`,
        "", { layer: id });
      continue;
    }
    for (const field of urlFields) {
      const url = `${LIVE_BASE_URL}${win[field]}`;
      let r;
      try {
        r = await transport.probe(url);
      } catch (e) {
        findings.fail(`layer_${id}_unreachable`,
          `${id}/${primaryKey}/${field} unreachable`, `${e.name}: ${e.message}`,
          { layer: id, url: win[field] });
        continue;
      }
      if (r.status !== 200) {
        findings.fail(`layer_${id}_http_error`,
          `${id}/${primaryKey}/${field} HTTP ${r.status}`,
          `URL: ${url}\n${diagString(r.headers)}`.trim(),
          { layer: id, url: win[field] });
        continue;
      }
      if (r.bytes < 200) {
        findings.fail(`layer_${id}_too_small`,
          `${id}/${primaryKey}/${field} only ${r.bytes} bytes (likely 1×1 placeholder)`,
          "", { layer: id, url: win[field] });
        continue;
      }
      // PNG signature: 89 50 4E 47 0D 0A 1A 0A
      const buf = r.head;
      if (buf[0] !== 0x89 || buf[1] !== 0x50 || buf[2] !== 0x4e || buf[3] !== 0x47) {
        findings.fail(`layer_${id}_corrupt`,
          `${id}/${primaryKey}/${field} body doesn't start with PNG signature`,
          "", { layer: id, url: win[field] });
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
            `Latest date: ${latest}.`,
            "", { layer: id });
        }
      }
    }
  }
}


// ---- Report (published_health.json-compatible) -------------------------

function writeReport(path, findings, manifest) {
  const sevOf = (f) => {
    if (f.severity === "fail") return CRITICAL_CODES.has(f.code) ? "critical" : "high";
    return "medium"; // warns
  };
  const reportFindings = findings.list
    .filter((f) => f.severity === "fail" || f.severity === "warn")
    .map((f) => ({
      severity: sevOf(f),
      code: f.code,
      title: f.title,
      detail: f.detail || "",
      layer: f.layer ?? null,
      url: f.url ?? null,
    }));
  const count = (s) => reportFindings.filter((f) => f.severity === s).length;
  const report = {
    generated_at_utc: new Date().toISOString(),
    base_url: LIVE_BASE_URL,
    probe: "tests/live-checkpoints/live-manifest.mjs",
    transport: TRANSPORT,
    manifest_generated_at: manifest?.generated_at ?? null,
    summary: {
      total_findings: reportFindings.length,
      critical: count("critical"),
      high: count("high"),
      medium: count("medium"),
    },
    findings: reportFindings,
  };
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(report, null, 2) + "\n");
  console.log(`[live-checkpoints] wrote report → ${path}`);
}


// ---- Main ------------------------------------------------------------

async function main() {
  const reportIdx = process.argv.indexOf("--report");
  const reportPath = reportIdx !== -1 ? process.argv[reportIdx + 1] : null;
  if (reportIdx !== -1 && !reportPath) {
    console.error("[live-checkpoints] --report requires a path argument");
    return 2;
  }

  console.log(`[live-checkpoints] starting against ${LIVE_BASE_URL} (transport: ${TRANSPORT})\n`);
  const findings = new Findings();

  let transport;
  try {
    transport = TRANSPORT === "fetch"
      ? await makeFetchTransport()
      : await makeBrowserTransport();
  } catch (e) {
    console.error(`[live-checkpoints] FATAL: transport setup: ${e?.stack || e}`);
    return 2;
  }

  let manifest = null;
  try {
    await probeHomepage(findings, transport);
    manifest = await probeManifest(findings, transport);
    await probePngs(findings, transport, manifest);
  } finally {
    await transport.close().catch(() => {});
  }

  console.log(`\n[live-checkpoints] findings:`);
  findings.print();

  if (reportPath) writeReport(reportPath, findings, manifest);

  if (findings.hasCriticalFailures()) {
    console.error(`\n[live-checkpoints] FAIL — critical check failed`);
    return 2;
  }
  if (findings.hasFailures()) {
    console.error(`\n[live-checkpoints] FAIL — at least one check failed`);
    return 1;
  }
  console.log(`\n[live-checkpoints] PASS — live deploy is healthy`);
  return 0;
}


main().then((code) => process.exit(code)).catch((e) => {
  console.error(`[live-checkpoints] FATAL: ${e?.stack || e}`);
  process.exit(2);
});
