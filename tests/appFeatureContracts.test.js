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
  // src/components/MapShell.jsx. The SST timeline scrubber stays in
  // MapShell because it sits inside .map-stage (under the desktop
  // panels).
  // 2026-05-24 Stage 4b: the desktop chrome (Tooltip + 4 collapsible
  // panels + zoom-ctl + attribution) moved into DesktopLayout.jsx —
  // including SstModeToggle + SstCurrentCard + SstTrendChip +
  // SstSparkline (consumed inside the Saved Spots + controls panels).
  // MapShell still owns the SST timeline scrubber + sstSelToSlotKey;
  // DesktopLayout owns the mode toggle + current-value cards.
  const mapShell = read("src/components/MapShell.jsx");
  const desktopLayout = read("src/components/DesktopLayout.jsx");

  assert.match(mapShell, /import SstTimeline,\s*\{\s*sstSelToSlotKey\s*\}/);
  assert.match(desktopLayout, /import \{[\s\S]*SstCurrentCard[\s\S]*SstModeToggle[\s\S]*\} from "\.\/SstTimeline\.jsx"/);
  assert.match(desktopLayout, /SstModeToggle/);
  // 2026-05-23 Stage 5b: SST mode resolution + summary access moved
  // INTO useTimelineSelections.js. The hook computes activeSstMode +
  // sstActiveSel + sstTimelineSummary via resolveSstMode and returns
  // them pre-resolved. App.jsx no longer imports getSstForecastSummary
  // (selToDate consumes the hook's sstTimelineSummary instead) — pin
  // the contract on the hook source where the call lives now.
  const timelineHook = read("src/hooks/useTimelineSelections.js");
  assert.match(timelineHook, /import \{ resolveSstMode \} from "\.\.\/lib\/sstMode\.js"/);
  assert.match(timelineHook, /getSstHistorySummary/);
  assert.match(timelineHook, /getSstForecastSummary/);
  assert.match(timelineHook, /activeSstMode = resolveSstMode\(/);
  // 2026-05-23: useState calls for SST mode + forecast selection
  // moved into src/hooks/useTimelineSelections.js as part of the
  // Stage 3 refactor. App.jsx now destructures them from the hook;
  // pin the contract on the hook's source, mirroring how this test
  // already handles the loaders split (dataSource → loaders/*.js).
  assert.match(timelineHook, /const \[sstMode, _setSstMode\] = useState\("history"\)/);
  assert.match(timelineHook, /const \[sstForecastSel, setSstForecastSel\] = useState\(\{ slot: "f0" \}\)/);
  // App.jsx must consume the hook + destructure the names downstream
  // components depend on. If a future refactor renames the destructure
  // it breaks here BEFORE the build fails on missing identifiers.
  assert.match(app, /import \{ useTimelineSelections \} from "\.\/hooks\/useTimelineSelections\.js"/);
  // Stage 5c: App.jsx no longer destructures raw sstSel/sstForecastSel
  // — those are hook-internal now. The downstream consumers (MapShell,
  // MobileSheet) all use the derived activeSstMode/sstActiveSel.
  assert.match(app, /sstMode,\s*setSstMode/);
  assert.match(app, /activeSstMode,\s*sstActiveSel,\s*setSstActiveSel/);
  // Render-path assertions: the activeComposite ternary + the SST
  // timeline scrubber stay in MapShell (inside .map-stage). The
  // controls-panel SST mode toggle + current-value card moved to
  // DesktopLayout (Stage 4b).
  assert.match(mapShell, /layer === "sst"\s*\?\s*\(sstTimelineSummary \? sstSelToSlotKey\(sstActiveSel, sstTimelineSummary\) : composite\)/);
  assert.match(mapShell, /\{layer === "sst" && hasSstTimeline && \(\s*<SstTimeline sel=\{sstActiveSel\} setSel=\{setSstActiveSel\} units=\{units\} mode=\{activeSstMode\} \/>/);
  assert.match(desktopLayout, /layer === "sst" && hasSstTimeline \?\s*\(\s*<div className="composite wind-grid-host">[\s\S]*Sea temp forecast[\s\S]*<SstModeToggle[\s\S]*<SstCurrentCard sel=\{sstActiveSel\} units=\{units\} mode=\{activeSstMode\} \/>/);
  assert.match(mobileSheet, /layer === "sst" && hasSstTimeline \? `Sea temp/);
  assert.match(mobileSheet, /layer === "sst" && hasSstTimeline \?\s*\(\s*<>[\s\S]*<SstModeToggle[\s\S]*<SstCurrentCard sel=\{sstActiveSel\} units=\{units\} mode=\{activeSstMode\} \/>/);
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
  // 2026-05-24 Stage 4b: the timeline *scrubbers* stay in MapShell (inside
  // .map-stage), but the per-layer current-value CARDS (CurrentCurrentCard,
  // SwellCurrentCard, WindCurrentSelectionCard) moved into the controls
  // panel in DesktopLayout.jsx.
  const mapShell = read("src/components/MapShell.jsx");
  const desktopLayout = read("src/components/DesktopLayout.jsx");
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

  // Timeline scrubbers stay in MapShell. (2026-06-14: they now also take a
  // `hover` prop so the playhead can report the pinned point through the
  // forecast — match the sel/setSel wiring without pinning the prop list.)
  assert.match(mapShell, /<CurrentTimeline sel=\{currentSel\} setSel=\{setCurrentSel\}/);
  assert.match(mapShell, /<SwellTimeline sel=\{swellSel\} setSel=\{setSwellSel\}/);
  assert.match(mapShell, /<WindTimeline sel=\{windSel\} setSel=\{setWindSel\}/);
  // Current-value cards live in DesktopLayout's controls panel.
  assert.match(desktopLayout, /<CurrentCurrentCard sel=\{currentSel\} \/>/);
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
