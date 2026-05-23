import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function read(rel) {
  return readFileSync(resolve(repoRoot, rel), "utf8");
}

test("Temp keeps historical and beta forecast SST timelines instead of reverting to legacy composites", () => {
  const app = read("src/App.jsx");
  const mobileSheet = read("src/components/MobileSheet.jsx");
  const dataSource = read("src/lib/dataSource.js");
  // 2026-05-23 Stage 4: DesktopView extracted from App.jsx into
  // src/components/MapShell.jsx. The SST timeline + SstModeToggle +
  // SstCurrentCard imports/JSX live in MapShell now; pin the
  // contract on MapShell, same pattern this test uses for the
  // loaders split (dataSource → loaders/*.js) and the hook split
  // (App.jsx useState → useTimelineSelections.js).
  const mapShell = read("src/components/MapShell.jsx");

  assert.match(mapShell, /import SstTimeline,\s*\{[\s\S]*SstCurrentCard[\s\S]*SstModeToggle[\s\S]*sstSelToSlotKey/);
  assert.match(mapShell, /SstModeToggle/);
  assert.match(app, /getSstForecastSummary/);
  // 2026-05-23: useState calls for SST mode + forecast selection
  // moved into src/hooks/useTimelineSelections.js as part of the
  // Stage 3 refactor. App.jsx now destructures them from the hook;
  // pin the contract on the hook's source, mirroring how this test
  // already handles the loaders split (dataSource → loaders/*.js).
  const timelineHook = read("src/hooks/useTimelineSelections.js");
  assert.match(timelineHook, /const \[sstMode, _setSstMode\] = useState\("history"\)/);
  assert.match(timelineHook, /const \[sstForecastSel, setSstForecastSel\] = useState\(\{ slot: "f0" \}\)/);
  // App.jsx must consume the hook + destructure the names downstream
  // components depend on. If a future refactor renames the destructure
  // it breaks here BEFORE the build fails on missing identifiers.
  assert.match(app, /import \{ useTimelineSelections \} from "\.\/hooks\/useTimelineSelections\.js"/);
  assert.match(app, /sstMode,\s*setSstMode[\s\S]*sstSel,\s*setSstSel[\s\S]*sstForecastSel,\s*setSstForecastSel/);
  // Render-path assertions follow the JSX into MapShell.
  assert.match(mapShell, /layer === "sst"\s*\?\s*\(sstTimelineSummary \? sstSelToSlotKey\(sstActiveSel, sstTimelineSummary\) : composite\)/);
  assert.match(mapShell, /\{layer === "sst" && hasSstTimeline && \(\s*<SstTimeline sel=\{sstActiveSel\} setSel=\{setSstActiveSel\} units=\{units\} mode=\{activeSstMode\} \/>/);
  assert.match(mapShell, /layer === "sst" && hasSstTimeline \?\s*\(\s*<div className="composite wind-grid-host">[\s\S]*Sea temp forecast[\s\S]*<SstModeToggle[\s\S]*<SstCurrentCard sel=\{sstActiveSel\} units=\{units\} mode=\{activeSstMode\} \/>/);
  assert.match(mobileSheet, /layer === "sst" && hasSstTimeline \? `Sea temp/);
  assert.match(mobileSheet, /layer === "sst" && hasSstTimeline \?\s*\(\s*<>[\s\S]*<SstModeToggle[\s\S]*<SstCurrentCard sel=\{activeSstSel\} units=\{units\} mode=\{activeSstMode\} \/>/);
  // 2026-05-09: dataSource.js's loadManifest if/else if chain was split
  // into per-layer files under src/lib/loaders/. The state.layers.sst7d /
  // sst5d assignment lines now live in those files. Read them so the
  // contract still pins the same data shape regardless of where in the
  // codebase the assignment happens.
  const sst7dLoader = read("src/lib/loaders/sst7d.js");
  const sst5dLoader = read("src/lib/loaders/sst5d.js");
  // Loader registry must dispatch to a per-layer loader, not run an
  // inline if/else chain that's prone to duplicate-branch bugs.
  assert.match(dataSource, /LAYER_LOADERS\[layer\]/);
  assert.match(dataSource, /from "\.\/loaders\/index\.js"/);
  // The actual sst7d/sst5d state writes now live in the per-layer files.
  assert.match(sst7dLoader, /state\.layers\.sst7d = \{ summary \}/);
  assert.match(sst5dLoader, /state\.layers\.sst5d = \{ summary \}/);
});

test("Current, swell, wind, and mobile overlay features remain wired", () => {
  const mobileSheet = read("src/components/MobileSheet.jsx");
  // 2026-05-23 Stage 4: timeline JSX moved with DesktopView into MapShell.jsx.
  const mapShell = read("src/components/MapShell.jsx");
  // 2026-05-09: app.css was split into per-area files (tokens, shell,
  // popups, mobile, wind) — read them all and concat for the grep
  // contract checks below. The barrel `app.css` only carries @imports
  // now, so individual rule names live in their respective area files.
  const styles =
    read("src/styles/app.css") +
    read("src/styles/tokens.css") +
    read("src/styles/shell.css") +
    read("src/styles/popups.css") +
    read("src/styles/mobile.css") +
    read("src/styles/wind.css");

  assert.match(mapShell, /<CurrentTimeline sel=\{currentSel\} setSel=\{setCurrentSel\} \/>/);
  assert.match(mapShell, /<CurrentCurrentCard sel=\{currentSel\} \/>/);
  assert.match(mapShell, /<SwellTimeline sel=\{swellSel\} setSel=\{setSwellSel\} \/>/);
  assert.match(mapShell, /<WindTimeline sel=\{windSel\} setSel=\{setWindSel\} \/>/);
  assert.match(mobileSheet, /className="ms-overlay-quick"/);
  assert.match(mobileSheet, /aria-pressed=\{mpaOn\}/);
  assert.match(mobileSheet, /aria-pressed=\{bathyOn\}/);
  assert.match(styles, /\.ms-overlay-quick/);
  assert.match(styles, /\.mpa-popup-close/);
  assert.match(styles, /\.mpa-popup-done/);
});

test("CI runs frontend and data feature contracts before publishing", () => {
  const pkg = JSON.parse(read("package.json"));
  const refreshWorkflow = read(".github/workflows/refresh-ca-data.yml");
  const deployProdWorkflow = read(".github/workflows/deploy-prod.yml");
  const devChecksWorkflow = read(".github/workflows/dev-checks.yml");

  // Relaxed from `assert.equal` to `assert.match` so adding more glob
  // targets to the test script (e.g. tests/checkpoints/*.test.js)
  // doesn't break the contract — what matters is that `npm test`
  // covers tests/*.test.js. Tightening this back to assert.equal
  // would defeat the per-folder checkpoint pattern in CHECKPOINTS.md.
  assert.match(pkg.scripts.test, /^node --test tests\/\*\.test\.js\b/,
    "pkg.scripts.test must run `node --test` over the top-level tests/*.test.js glob, " +
    "optionally followed by additional globs (e.g. tests/checkpoints/*.test.js)");
  assert.equal(pkg.scripts["test:data-contracts"], "node tests/dataFeatureContracts.mjs");
  // dev-checks.yml is the canonical pre-merge gate — it runs `npm test`
  // (web-tests job) and `npm run build` (web-build job). The legacy
  // `frontend-tests.yml` was deleted 2026-05-08 because dev-checks
  // covers the same surface plus lint/smoke/visual-paint.
  assert.match(devChecksWorkflow, /name:\s*web-tests[\s\S]*npm test/);
  assert.match(devChecksWorkflow, /name:\s*web-build[\s\S]*npm run build/);
  // 2026-05-13: build + deploy steps moved out of refresh-ca-data.yml
  // and into deploy-prod.yml. refresh-ca-data.yml now (a) runs the
  // frontend + data-contract tests against the refreshed PNGs, (b)
  // commits the data, (c) explicitly triggers deploy-prod.yml via
  // `gh workflow run`. deploy-prod.yml owns the build + Cloudflare
  // publish via the deploy-cloudflare composite action.
  assert.match(refreshWorkflow,
    /Run frontend regression tests[\s\S]*npm test[\s\S]*Run data feature contracts[\s\S]*npm run test:data-contracts[\s\S]*Trigger production deploy[\s\S]*gh workflow run deploy-prod\.yml/,
    "refresh-ca-data.yml must run frontend + data-contract tests, then trigger deploy-prod.yml");
  assert.match(deployProdWorkflow,
    /uses:\s*\.\/\.github\/actions\/deploy-cloudflare[\s\S]*branch:\s*main/,
    "deploy-prod.yml must use the deploy-cloudflare composite action with branch=main");
});
