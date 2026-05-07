import assert from "node:assert/strict";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function parseArgs() {
  const out = new Map();
  for (let i = 2; i < process.argv.length; i++) {
    const arg = process.argv[i];
    if (!arg.startsWith("--")) continue;
    const next = process.argv[i + 1];
    if (next && !next.startsWith("--")) {
      out.set(arg, next);
      i++;
    } else {
      out.set(arg, true);
    }
  }
  return out;
}

const args = parseArgs();
const root = resolve(repoRoot, args.get("--root") || "dist");
const baseUrl = args.get("--url") || process.env.SITE_SMOKE_URL || null;
const retries = Number(args.get("--retries") || 3);
const retryDelayMs = Number(args.get("--retry-delay-ms") || 5000);

function sleep(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}

function isHtml(text) {
  return /^\s*(<!doctype\s+html|<html[\s>])/i.test(text);
}

function assertNotHtml(text, label) {
  assert.equal(isHtml(text), false, `${label} served HTML fallback instead of the expected resource`);
}

function resolveLocalPath(ref) {
  const raw = String(ref).split("#")[0].split("?")[0];
  const clean = raw === "/" || raw === "" ? "index.html" : raw.replace(/^\//, "");
  const path = resolve(root, clean);
  assert.equal(path.startsWith(root), true, `resource escapes smoke root: ${ref}`);
  return path;
}

function resolveRemoteUrl(ref) {
  return new URL(ref, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`).toString();
}

async function fetchWithRetry(url) {
  let lastError = null;
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok || attempt === retries) return response;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await sleep(retryDelayMs);
  }
  throw lastError;
}

async function readText(ref, label, { allowHtml = false } = {}) {
  if (baseUrl) {
    const url = resolveRemoteUrl(ref);
    const response = await fetchWithRetry(url);
    assert.equal(response.ok, true, `${label} ${url} returned HTTP ${response.status}`);
    const text = await response.text();
    if (!allowHtml) assertNotHtml(text, label);
    return text;
  }

  const path = resolveLocalPath(ref);
  assert.equal(existsSync(path), true, `${label} missing: ${path}`);
  const text = readFileSync(path, "utf8");
  if (!allowHtml) assertNotHtml(text, label);
  return text;
}

async function readJson(ref, label) {
  const text = await readText(ref, label);
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`${label} is not valid JSON: ${error.message}`);
  }
}

async function probeBinary(ref, label) {
  if (baseUrl) {
    const url = resolveRemoteUrl(ref);
    const response = await fetchWithRetry(url);
    assert.equal(response.ok, true, `${label} ${url} returned HTTP ${response.status}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    assert.ok(bytes.length > 200, `${label} body too small (${bytes.length} bytes)`);
    const head = new TextDecoder().decode(bytes.slice(0, 80));
    assertNotHtml(head, label);
    return;
  }

  const path = resolveLocalPath(ref);
  assert.equal(existsSync(path), true, `${label} missing: ${path}`);
  assert.ok(statSync(path).size > 200, `${label} too small: ${path}`);
}

function assertNoConflictMarkers(text, label) {
  assert.equal(text.includes("<<<<<<<"), false, `${label} contains merge conflict marker`);
  assert.equal(text.includes(">>>>>>>"), false, `${label} contains merge conflict marker`);
}

function assetRefs(html) {
  return [...html.matchAll(/(?:src|href)="([^"]+\.(?:js|css)(?:\?[^"]*)?)"/g)].map((m) => m[1]);
}

async function smokeAssets() {
  const html = await readText("/", "index.html", { allowHtml: true });
  assert.match(html, /<title>ShouldIDive/, "index.html should be the ShouldIDive app shell");
  assertNoConflictMarkers(html, "index.html");

  const assets = assetRefs(html);
  assert.ok(assets.length >= 2, "index.html should reference built JS/CSS assets");
  for (const asset of assets) {
    const body = await readText(asset, `asset ${asset}`);
    assertNoConflictMarkers(body, `asset ${asset}`);
  }
}

const summaryMinDays = {
  sst7d: 3,
  sst5d: 5,
  wind5d: 5,
  swell5d: 5,
  current5d: 5,
};

function layerRange(info, summary) {
  return info.range || info.range_c || summary.range || summary.range_c;
}

async function smokeSummaryLayer(layerId, info) {
  assert.equal(typeof info.summary_url, "string", `${layerId} must include summary_url`);
  const summary = await readJson(info.summary_url, `${layerId} summary`);
  assert.equal(Array.isArray(summary.days), true, `${layerId} summary must include days[]`);
  assert.ok(
    summary.days.length >= summaryMinDays[layerId],
    `${layerId} summary has too few days (${summary.days.length}/${summaryMinDays[layerId]})`
  );

  if (layerId === "sst7d" || layerId === "sst5d") {
    assert.ok(Array.isArray(layerRange(info, summary)), `${layerId} must expose a decode range`);
    assert.equal(typeof (info.grid || summary.grid)?.width, "number", `${layerId} must expose grid.width`);
    assert.equal(typeof (info.grid || summary.grid)?.height, "number", `${layerId} must expose grid.height`);
    if (layerId === "sst5d") assert.equal(info.beta || summary.beta, true, "sst5d must be marked beta");
    for (const day of summary.days) {
      assert.equal(typeof day.url, "string", `${layerId} day must include url`);
      await probeBinary(day.url, `${layerId} ${day.slot || day.day}`);
    }
    return;
  }

  const sampleDays = [summary.days[0], summary.days.at(-1)].filter(Boolean);
  for (const day of sampleDays) {
    const buckets = day.buckets || [];
    assert.ok(buckets.length > 0, `${layerId} day ${day.day} must include bucket summaries`);
    const firstBucket = buckets[0];
    const url = firstBucket.uv_url || firstBucket.wave_url;
    assert.equal(typeof url, "string", `${layerId} bucket must include a probeable PNG url`);
    await probeBinary(url, `${layerId} d${day.day} bucket`);
  }
}

async function smokeData() {
  const manifest = await readJson("/data/manifest.json", "manifest");
  const layers = manifest.layers || {};
  for (const required of ["sst", "sst7d", "sst5d", "wind5d", "swell5d", "current5d", "viz"]) {
    assert.ok(layers[required], `manifest must include ${required}`);
  }

  for (const layerId of Object.keys(summaryMinDays)) {
    await smokeSummaryLayer(layerId, layers[layerId]);
  }
}

await smokeAssets();
await smokeData();

console.log(`site smoke passed (${baseUrl || root})`);
