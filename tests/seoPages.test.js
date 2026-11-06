// Static SEO pages (scripts/build-seo-pages.mjs): every saved spot gets a
// page, every page is in the sitemap, the copy follows the honesty contract
// (docs/STRICT-SCIENCE.md S2) and the house style (no em dashes), and the
// build-time decode gates stay in lockstep with the browser loaders.

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync, copyFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { buildSeoPages, SEO_REGIONS } from "../scripts/build-seo-pages.mjs";
import { readSavedSpots, buildRoster } from "../scripts/seo/spots.mjs";
import { CHL_FRESH_DAYS, GAP_FILL_SOURCE_CODES } from "../scripts/seo/sample.mjs";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(resolve(repoRoot, rel), "utf8");

function walkHtml(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walkHtml(p, out);
    else if (name.endsWith(".html")) out.push(p);
  }
  return out;
}

let built = null;
function buildOnce() {
  if (built) return built;
  const outDir = mkdtempSync(join(tmpdir(), "seo-pages-"));
  copyFileSync(resolve(repoRoot, "index.html"), join(outDir, "index.html"));
  mkdirSync(join(outDir, "fleet"), { recursive: true });
  copyFileSync(resolve(repoRoot, "public/fleet/data.json"), join(outDir, "fleet", "data.json"));
  const res = buildSeoPages({
    repoRoot,
    dataDir: resolve(repoRoot, "public/data"),
    outDir,
    now: new Date("2026-10-12T18:00:00Z"),
  });
  built = { outDir, res, html: walkHtml(outDir) };
  process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));
  return built;
}

test("spot roster is read from REGION_SAVED_SPOTS for every SEO region", () => {
  const saved = readSavedSpots(repoRoot);
  for (const r of SEO_REGIONS) {
    assert.ok(Array.isArray(saved[r]) && saved[r].length >= 10, `expected spots for ${r}`);
  }
  const roster = buildRoster(repoRoot, SEO_REGIONS);
  const slugs = roster.map((s) => s.slug);
  assert.equal(new Set(slugs).size, slugs.length, "spot slugs must be unique");
  for (const s of slugs) assert.match(s, /^[a-z0-9-]+$/);
});

test("SEO_REGIONS matches PROD_REGIONS in src/lib/region.js", () => {
  const m = read("src/lib/region.js").match(/const PROD_REGIONS = \[([^\]]*)\]/);
  assert.ok(m, "PROD_REGIONS not found");
  const prod = m[1].split(",").map((s) => s.trim().replace(/["']/g, "")).filter(Boolean);
  assert.deepEqual([...SEO_REGIONS].sort(), prod.sort());
});

test("build-time honesty gates match the browser loaders", () => {
  const scalar = read("src/lib/loaders/scalarPng.js");
  assert.match(scalar, new RegExp(`OBSERVED_FRESH_DAYS = \\{ chl: ${CHL_FRESH_DAYS} \\}`));
  const codes = [...GAP_FILL_SOURCE_CODES].join(", ");
  assert.match(scalar, new RegExp(`GAP_FILL_SOURCE_CODES = new Set\\(\\[${codes}\\]\\)`));
  assert.match(read("src/lib/loaders/viz.js"), /c === 0 \|\| c >= 3/);
});

test("every roster spot gets a page and every page is in the sitemap", () => {
  const { outDir, res } = buildOnce();
  const roster = buildRoster(repoRoot, SEO_REGIONS);
  assert.equal(res.spots, roster.length);
  const sitemap = readFileSync(join(outDir, "sitemap.xml"), "utf8");
  for (const s of roster) {
    assert.ok(statSync(join(outDir, "spots", s.slug, "index.html")).isFile(), s.slug);
    assert.ok(sitemap.includes(`https://shouldidive.com/spots/${s.slug}/`), `sitemap missing ${s.slug}`);
  }
  for (const g of ["diving", "spearfishing", "fishing", "sailing", "science", "about", "spots"]) {
    assert.ok(sitemap.includes(`https://shouldidive.com/${g}/`), `sitemap missing /${g}/`);
  }
  assert.ok(statSync(join(outDir, "404.html")).isFile());
});

test("generated pages have one title, description, canonical, and h1, and valid JSON-LD", () => {
  const { html } = buildOnce();
  for (const p of html) {
    const doc = readFileSync(p, "utf8");
    if (doc.includes('id="root"')) continue; // the app shell, checked separately
    assert.equal((doc.match(/<title>/g) || []).length, 1, p);
    assert.equal((doc.match(/name="description"/g) || []).length, 1, p);
    assert.equal((doc.match(/<h1>/g) || []).length, 1, p);
    const canon = (doc.match(/rel="canonical"/g) || []).length;
    assert.equal(canon, p.endsWith("404.html") ? 0 : 1, p);
    for (const m of doc.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)) {
      assert.doesNotThrow(() => JSON.parse(m[1]), `bad JSON-LD in ${p}`);
    }
  }
});

test("copy has no em dashes and no unearned validation language", () => {
  const { html } = buildOnce();
  const files = [...html, resolve(repoRoot, "index.html"), resolve(repoRoot, "public/llms.txt")];
  for (const p of files) {
    const text = readFileSync(p, "utf8").replace(/<!--[\s\S]*?-->/g, "");
    assert.ok(!text.includes("—"), `em dash in ${p}`);
    // S2: no "validated"/"calibrated" claims; negated forms are fine.
    for (const m of text.matchAll(/\b(?:\w+\s+){0,3}(validated|calibrated)\b/gi)) {
      assert.match(m[0], /\b(not|un|never|before)\b|uncalibrated/i, `unearned "${m[1]}" in ${p}: "${m[0]}"`);
    }
  }
});

test("visibility on spot pages is always labelled Modeled", () => {
  const { outDir } = buildOnce();
  const roster = buildRoster(repoRoot, SEO_REGIONS);
  for (const s of roster) {
    const doc = readFileSync(join(outDir, "spots", s.slug, "index.html"), "utf8");
    assert.match(doc, /Visibility <span class="tag modeled">Modeled<\/span>/, s.slug);
  }
});

test("home page carries canonical, social tags, and the spot link block", () => {
  const src = read("index.html");
  assert.match(src, /<link rel="canonical" href="https:\/\/shouldidive\.com\/" \/>/);
  assert.match(src, /property="og:image"/);
  assert.match(src, /<div id="root">[\s\S]*<!--seo:spots-->[\s\S]*<\/div>\s*<script type="module"/);
  const { outDir } = buildOnce();
  const out = readFileSync(join(outDir, "index.html"), "utf8");
  assert.ok(!out.includes("<!--seo:spots-->"), "marker should be replaced at build");
  assert.match(out, /href="\/spots\/la-jolla\/"/);
});

test("the build script runs the SEO step after vite", () => {
  const pkg = JSON.parse(read("package.json"));
  assert.match(pkg.scripts.build, /vite build && node scripts\/build-seo-pages\.mjs/);
});

test("the runtime CA title matches the static title Google sees first", () => {
  const staticTitle = read("index.html").match(/<title>([^<]+)<\/title>/)[1];
  const app = read("src/App.jsx");
  const ca = app.match(/"(live ocean conditions map for California and Baja)";\s*document\.title = `ShouldIDive: \$\{subtitle\}`/);
  assert.ok(ca, "CA subtitle not found in useRegionAwareTitle");
  assert.equal(`ShouldIDive: ${ca[1]}`, staticTitle);
});

test("the app honors ?spot= deep links from the spot pages", () => {
  assert.match(read("src/components/MapShell.jsx"), /useState\(initialSpotId\)/);
});

test("spot cards skip forecast slots that are already past at build time", async () => {
  const { loadRegion } = await import("../scripts/seo/sample.mjs");
  const { computeSpotConditions } = await import("../scripts/seo/spotPage.mjs");
  const R = loadRegion(resolve(repoRoot, "public/data"), "ca");
  const first = R.wind?.days?.[0]?.date;
  if (!first || !R.wind.days[1]) return; // nothing to roll over to
  // 23:30 Pacific on the first forecast day: every bucket that day is past.
  const lateNight = new Date(`${first}T23:30:00-07:00`);
  const spot = { id: "lajolla", appName: "La Jolla", name: "La Jolla", region: "ca", lng: -117.28, lat: 32.85, tz: "America/Los_Angeles" };
  const c = computeSpotConditions(spot, R, lateNight);
  assert.ok(c.forecast.length > 0);
  assert.ok(c.forecast[0].date > first, `expected the outlook to start after ${first}, got ${c.forecast[0].date}`);
});
