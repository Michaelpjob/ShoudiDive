// Post-build step: writes the static, crawlable pages into dist/ next to the
// Vite bundle. Runs on every `npm run build`, which every deploy (including
// the twice-daily data refresh) goes through, so spot pages always carry the
// numbers that were just published.
//
//   dist/spots/<slug>/index.html   one page per saved spot, today's numbers
//   dist/spots/index.html          roster by region
//   dist/<guide>/index.html        diving, spearfishing, fishing, sailing,
//                                  science, about
//   dist/404.html                  real 404 (stops the SPA soft-404 fallback)
//   dist/sitemap.xml               every page above, with lastmod
//   dist/index.html                spot links injected into the pre-mount block
//
// A failure here must never block a data deploy, so the CLI entry catches
// everything, prints a GitHub Actions warning, and exits 0. Code bugs are
// caught before merge by tests/seoPages.test.js.

import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadRegion } from "./seo/sample.mjs";
import { buildRoster, readBundleFacts, REGION_LABEL } from "./seo/spots.mjs";
import { computeSpotConditions, renderSpotPage, spotSummaryLine } from "./seo/spotPage.mjs";
import { renderGuidePages, renderSpotsIndex, render404, homeInjection } from "./seo/guides.mjs";
import { SITE } from "./seo/layout.mjs";

// Regions served on shouldidive.com (PROD_REGIONS in src/lib/region.js).
export const SEO_REGIONS = ["ca", "baja"];

function write(outDir, rel, html) {
  const p = join(outDir, rel);
  mkdirSync(dirname(p), { recursive: true });
  writeFileSync(p, html);
}

function readJsonSafe(p) {
  try {
    return existsSync(p) ? JSON.parse(readFileSync(p, "utf8")) : null;
  } catch {
    return null;
  }
}

// Counts the guides quote, read from what is actually being deployed.
function guideContext({ outDir, dataDir, roster, regionData, chartIds, now }) {
  const mpa = readJsonSafe(join(dataDir, "mpa-boundaries.geojson"));
  const bottom = readJsonSafe(join(dataDir, "bathy-features.geojson"));
  const fleet = readJsonSafe(join(outDir, "fleet", "data.json"));
  return {
    roster,
    now,
    chartIds,
    vizSkill: regionData.ca?.vizSkill || null,
    mpaCountCa: mpa?.features?.length ?? NaN,
    bottomCount: bottom?.features?.length ?? NaN,
    fleet: fleet?.meta || null,
  };
}

export function buildSeoPages({ repoRoot, dataDir, outDir, now = new Date(), regions = SEO_REGIONS }) {
  const roster = buildRoster(repoRoot, regions);
  const regionData = {};
  for (const r of regions) regionData[r] = loadRegion(dataDir, r);

  const pages = [];
  const spotRows = [];
  const chartIds = new Set();
  for (const spot of roster) {
    const R = regionData[spot.region];
    if (!R) continue;
    const c = computeSpotConditions(spot, R, now);
    const facts = readBundleFacts(dataDir, spot.region, spot.id);
    if (facts) chartIds.add(`${spot.region}:${spot.id}`);
    const { path, html } = renderSpotPage(spot, c, facts, roster, now);
    write(outDir, `${path.slice(1)}index.html`, html);
    pages.push({ path, lastmod: now, priority: 0.7, changefreq: "daily" });
    spotRows.push({ spot, summary: spotSummaryLine(c), hasChart: !!facts });
  }
  const ctx = guideContext({ outDir, dataDir, roster, regionData, chartIds, now });

  const idx = renderSpotsIndex(spotRows, regions.map((r) => ({ id: r, label: REGION_LABEL[r] })), now);
  write(outDir, "spots/index.html", idx.html);
  pages.push({ path: idx.path, lastmod: now, priority: 0.8, changefreq: "daily" });

  for (const g of renderGuidePages(ctx)) {
    write(outDir, `${g.path.slice(1)}index.html`, g.html);
    pages.push({ path: g.path, lastmod: now, priority: 0.8, changefreq: "weekly" });
  }

  write(outDir, "404.html", render404(roster));

  const indexPath = join(outDir, "index.html");
  if (existsSync(indexPath)) {
    const html = readFileSync(indexPath, "utf8");
    const injected = html.replace("<!--seo:spots-->", homeInjection(roster));
    writeFileSync(indexPath, injected);
  }

  const urls = [{ path: "/", lastmod: now, priority: 1.0, changefreq: "hourly" }, ...pages];
  const sitemap = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.map((u) => `  <url><loc>${SITE}${u.path}</loc><lastmod>${u.lastmod.toISOString()}</lastmod><changefreq>${u.changefreq}</changefreq><priority>${u.priority.toFixed(1)}</priority></url>`).join("\n")}
</urlset>
`;
  write(outDir, "sitemap.xml", sitemap);
  return { pages: urls, spots: spotRows.length };
}

const isCli = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isCli) {
  const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const outDir = join(repoRoot, "dist");
  try {
    const t0 = Date.now();
    const res = buildSeoPages({ repoRoot, dataDir: join(outDir, "data"), outDir });
    console.log(`seo: wrote ${res.pages.length} sitemap URLs (${res.spots} spot pages) in ${Date.now() - t0} ms`);
  } catch (e) {
    console.log(`::warning title=SEO pages skipped::${String(e?.stack || e).replace(/\n/g, "%0A")}`);
  }
}
