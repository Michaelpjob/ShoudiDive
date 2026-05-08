# Test checkpoint taxonomy

Industrial-grade testing has to answer a specific question: **"if a
class of bug were introduced, which check would catch it before it
reached users?"** This document maps every bug class we've actually
hit (or expect to hit) against a named checkpoint in the test suite.

A checkpoint is one or more test files grouped under a job name in
CI. Each checkpoint has:

- a clear scope (what bug class it catches)
- a clear non-scope (what it explicitly does NOT catch)
- a target runtime (~5 s for unit, <60 s for integration)
- a deterministic pass/fail signal (no probabilistic flakiness)

The test pyramid runs in two distinct stages:

1. **Dev-side**: `dev-checks.yml` on every push to `dev` and every
   PR to `main`. Catches bugs before merge.
2. **Live-side**: `deploy-verify.yml` after every refresh-data
   deploy lands. Catches bugs that only manifest against real-world
   data + production CDN behavior + actual users' browsers.

## Checkpoint table

| Checkpoint            | Stage | Runtime  | Catches                                                                                  | What it can't catch                                          |
|-----------------------|:-----:|:--------:|------------------------------------------------------------------------------------------|--------------------------------------------------------------|
| `cp-static-lint`      | dev   | <10 s    | Dangling refs (`no-undef`), dupe imports, dupe `else if`, hooks rule violations          | Type errors (we don't use TS); semantic correctness          |
| `cp-secrets-scan`     | dev   | <5 s     | Committed API keys, `.env`, PEM private keys                                             | Secrets-in-history (after the fact); rotated-but-leaked      |
| `cp-workflow-lint`    | dev   | <10 s    | Malformed `.github/workflows/*.yml`                                                      | Workflow logic bugs                                          |
| `cp-pipeline-unit`    | dev   | <30 s    | Python static-compile + pytest unit layer (kriging, scoring, watchdog, forecast math)    | Pipeline integration failures (NOAA timeouts etc.)          |
| `cp-data-shape`       | dev   | <5 s     | Manifest schema regressions (missing layer keys, broken decoders)                        | "Manifest is fresh" — that's a live-side concern             |
| `cp-manifest-validate`| dev   | <5 s     | LayerSpec contract — range/scale drift between pipeline encoder and frontend decoder     | Per-cell pixel-value drift (need a baseline diff for that)   |
| `cp-rendering-math`   | dev   | <5 s     | Colormap stops, `project()`/`unproject()` round-trip, sst-trend palette                  | Visual rendering (see `cp-visual-paint`)                     |
| `cp-sst-trend-math`   | dev   | <5 s     | Phase A `getSstTrend` math, sparkline shape, NaN propagation                             | Trend feels right (subjective)                               |
| `cp-mobile-adaptive`  | dev   | <5 s     | Mobile breakpoint helpers, gesture-isolation classes, touch-target floors                | iOS Safari runtime quirks                                    |
| `cp-app-contracts`    | dev   | <5 s     | Source-level contracts on App.jsx + MobileSheet.jsx (existing tests/*.test.js)           | Render output                                                |
| `cp-web-build`        | dev   | <60 s    | Vite production bundle compiles                                                          | Run-time errors                                              |
| `cp-mobile-static`    | dev   | <30 s    | Mobile RN data-layer + colormap jest tests                                               | Native render behavior (need real device)                    |
| `cp-runtime-smoke`    | dev   | <60 s    | Boots dist/, watches for `pageerror`/`console.error`, asserts shell mounted              | Production-data quirks                                       |
| `cp-visual-paint`     | dev   | <120 s   | For each layer, switches it on and verifies the canvas paints non-trivial pixels          | Pixel-perfect regression (no baseline image diffing yet)    |
| **`live-cp-manifest`**| live  | <10 s    | shouldidive.com manifest reachable, `generated_at` fresh, all required layers present    | Runtime UI behavior                                          |
| **`live-cp-pngs`**    | live  | <30 s    | Every layer's primary PNG returns 200, decodes, has non-trivial content                  | Per-cell value correctness                                   |
| **`live-cp-render`**  | live  | <60 s    | Hits shouldidive.com in headless Chrome, every layer chip clicks, no console errors      | What users on Edge/Firefox see (single-browser limit)        |
| **`live-cp-perf`**    | live  | <30 s    | Bundle size budget, TTFB, LCP                                                            | Network-side variance                                        |
| **`live-cp-feeds`**   | live  | <2 min   | `check_published.py` against the live deploy (existing — re-used)                        | Edge-cache staleness across regions                          |

## Bug-zone mapping (lessons learned)

Each bug we've actually shipped has a corresponding checkpoint that
would have caught it. Adding a new bug → new entry here, new test
under the matching checkpoint.

| Bug shipped                                                   | Date       | Caught by checkpoint     |
|---------------------------------------------------------------|------------|--------------------------|
| `ReferenceError: sstViewMode is not defined` (white-screen)   | 2026-05-07 | `cp-static-lint` + `cp-runtime-smoke` |
| Duplicate `./lib/dataSource.js` import                        | 2026-05-07 | `cp-static-lint` |
| Duplicate `else if (layer === "sst5d")` branch                | 2026-05-07 | `cp-static-lint` |
| iOS Safari `<foreignObject>` viewBox not transforming         | 2026-04-30 | `live-cp-render` (would need cross-browser; not yet wired) |
| Mobile breakpoint mismatch `760px` vs `1024px` UA hint        | 2026-04-29 | `cp-mobile-adaptive` |
| Slider cut off at left on iPhone (transform: translateX leak) | 2026-05-04 | `cp-mobile-adaptive` |
| `sstViewMode` rendered nothing because state was removed      | 2026-05-07 | `cp-runtime-smoke` |
| Hung deploy because data-contracts step lacked `continue-on-error` | 2026-05-07 | _process bug, not test bug — fixed in workflow_ |
| MUR L4 +0.3-0.5 °F coastal warm bias                          | ongoing    | _accuracy bug — Phase B buoy correction, surfaced via watchdog_ |

## Stage transitions

```
push to dev
   ↓
cp-static-lint → cp-secrets-scan → cp-workflow-lint   (all parallel, <10s)
   ↓
cp-pipeline-unit ┐
cp-data-shape   ┐
cp-rendering-math ┐ (all parallel, <30s)
cp-sst-trend-math ┐
cp-mobile-adaptive ┐
cp-app-contracts ┘
cp-mobile-static ┘
   ↓
cp-web-build (~60s)
   ↓
cp-runtime-smoke (~60s, needs the build artifact)
cp-visual-paint  (~120s, also needs the build artifact)
   ↓
[merge to main; refresh-data.yml + cloudflare deploy]
   ↓
live-cp-manifest → live-cp-pngs → live-cp-feeds   (parallel, <2min)
   ↓
live-cp-render   (~60s, headless Chrome against shouldidive.com)
   ↓
live-cp-perf     (~30s)
   ↓
[on red: open issue tagged `live-deploy-broken`, alert via watchdog]
```

## Adding a new checkpoint

1. Pick the right STAGE — dev (per-PR) or live (per-deploy).
2. Pick the right TEST RUNTIME — node:test for pure-JS, pytest for
   Python pipeline, Puppeteer for browser-execution checks.
3. Name the file `tests/checkpoints/<cp-name>.test.js` (or
   `tests/live-checkpoints/<cp-name>.mjs` for live tests).
4. Add a row to the checkpoint table above.
5. Wire a new job into the matching workflow (`dev-checks.yml` or
   `deploy-verify.yml`). Job name MUST match the cp-name.
6. If the job is required for merge, add it to `REQUIRED_CHECKS` in
   `scripts/setup-branch-protection.sh` and re-run that script.

## Why two stages and not one

- **Dev stage catches "it compiles but doesn't work."** Lint, build,
  unit tests, headless render against the dev preview. Every bug
  surfaceable from source + generated bundle is caught here.
- **Live stage catches "it works in headless Chrome but not in
  production."** Real CDN edge caching, real manifest staleness,
  real data missing because a fetch silently failed, real
  performance regressions. The 2026-05-07 white-screen incident
  showed up in production for ~25 minutes — `cp-runtime-smoke`
  would have caught it before merge, AND a hypothetical
  `live-cp-render` would have caught it within ~3 min of the bad
  deploy (vs ~25 min of "let's wait for the next user complaint").

## Quarantine policy

When a test goes flaky:

1. Open an issue tagged `flaky-test` describing the symptom.
2. Move the test to `tests/checkpoints/_quarantine/` so CI runs it
   but doesn't gate on the result.
3. The issue stays open until either:
   - the test is fixed and moved back, OR
   - the test is deleted with justification (false signal, no real
     bug class covered)

Don't disable a check, don't `xit` it inline. Quarantine = visible.
