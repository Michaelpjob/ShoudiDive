# Repository conventions for coding agents

This file is read automatically by Claude Code on every session.
A copy at `AGENTS.md` is read by OpenAI Codex and other agents that
follow that convention. The two files are kept identical — edit one,
copy to the other.

## TL;DR — branching rules

```
agent commits ──► push to dev ──► dev-checks.yml runs the test suite
                                          │
                                          ▼
                                  green (≈ 90 s)
                                          │
                                          ▼
                                  open PR: dev → main
                                          │
                                          ▼
                                  human reviews + merges
                                          │
                                          ▼
                                  refresh-data.yml deploys to
                                  production (shouldidive.com)
```

**Never push directly to `main`.** Branch protection will reject the
push, and you'll waste your turn fighting git rather than shipping.
Push to `dev` first. Always.

## Why this exists

Two coding agents (Claude Code, Codex) make autonomous commits to
this repo. Without a gate, a syntactically broken commit can land at
`shouldidive.com` 5 minutes later via the auto-deploy. The gate
catches:

- Web bundle build failures (Vite syntax errors, dropped imports)
- Pipeline import errors / Python syntax regressions
- Mobile unit-test regressions in the data layer / colormap LUT
- Committed secrets (API keys, .env files, private keys)
- Workflow YAML syntax errors

The dev gate is fast (~90 seconds end-to-end) so the cost is small.

## How to ship a change

1. **Branch off `main`** (one-liner — Claude Code may auto-do this):
   ```bash
   git fetch origin
   git switch -c dev origin/main           # first time
   # or
   git switch dev && git pull --rebase origin dev   # subsequent times
   ```

2. **Make your change. Commit normally** (do not skip hooks).

3. **Push to `dev`:**
   ```bash
   git push origin dev
   ```

4. **Wait for `dev-checks.yml` to go green.**
   - The dev preview at `https://dev.shouldidive.pages.dev` updates
     automatically once checks pass — visit it to eyeball UI changes
     before promoting.
   - Use `gh run watch <run-id>` or `gh run list --branch dev` to
     monitor.

5. **Open a PR:**
   ```bash
   gh pr create --base main --head dev --title "<concise title>" \
     --body "<short summary>"
   ```

6. **Wait for the human to review + merge.** Do not auto-merge.

## What runs in `dev-checks.yml` (per-PR gate)

The full taxonomy lives in [`tests/CHECKPOINTS.md`](tests/CHECKPOINTS.md)
— including which bug class each checkpoint catches. Quick reference:

| Job                | Stage  | Catches |
|--------------------|--------|---------|
| `pipeline-tests`   | dev    | Python static-compile + pytest unit layer |
| `web-build`        | dev    | Vite production bundle compiles |
| `web-lint`         | dev    | ESLint `no-undef`, dupe imports, dupe `else if`, hooks rules |
| `web-tests`        | dev    | All `tests/*.test.js` + `tests/checkpoints/*.test.js` (data-shape, rendering-math, sst-trend, mobile-adaptive) |
| `web-smoke`        | dev    | Puppeteer boots the built bundle, watches for `pageerror`/console errors |
| `cp-visual-paint`  | dev    | 3 viewports × 5 layers paint check (non-required, advisory) |
| `mobile-static`    | dev    | RN data-layer + colormap jest tests |
| `secrets-scan`     | dev    | Committed API keys, `.env`, PEM private keys |
| `workflow-lint`    | dev    | actionlint on `.github/workflows/*.yml` |
| `manifest-validate`| dev    | LayerSpec contract — range/scale drift between pipeline encoder and frontend decoder |

Required-status-checks list lives in
`scripts/setup-branch-protection.sh`. Adding a new GATING check =
add a job + add it to `REQUIRED_CHECKS` + re-run that script.

## What runs in `deploy-verify.yml` (post-deploy gate)

After every successful refresh-data / refresh-wind deploy AND every
4 h via cron, the live-side checkpoints fire against shouldidive.com:

| Job                  | Catches |
|----------------------|---------|
| `live-cp-manifest`   | Live `manifest.json` reachable, `generated_at` fresh, all required layers present, every primary PNG returns 200 + decodes |
| `live-cp-render`     | Headless Chrome boots shouldidive.com, asserts shell mounted + DataOverlay painted + saved-spots populated |
| `sync-issue`         | On red: opens / updates rolling Issue tagged `live-deploy-broken`. On green-after-red: auto-closes it. |

The 2026-05-07 white-screen incident shipped to prod for ~25 min.
With `live-cp-render` running, the same bug would surface as an
opened Issue within ~3 min of the bad deploy.

### Why three layers of gates (lint + tests + smoke + visual + live)

Each catches a distinct failure class:

- **`web-build`**: syntax errors, broken imports.
- **`web-lint`**: dangling references, dupe imports, hook violations.
  Pure JavaScript is dynamically typed; a typo'd variable name
  compiles fine but throws at runtime. (Caught the 2026-05-07
  duplicate import + the duplicate `else if (layer === "sst5d")`.)
- **`web-tests`**: contract regressions — components/exports/keys
  that the source has agreed to publish stay published.
- **`web-smoke`**: actually boots the bundle in headless Chrome and
  watches React's first-render path. If build/lint/tests pass but
  `<App/>` throws on mount, this is the gate that fires.
- **`cp-visual-paint`**: extends the smoke to 3 viewports × every
  layer chip. Catches "layer renders blank at mobile width."
- **`live-cp-*`**: same headless-Chrome smoke + a manifest+PNG HTTP
  probe, run AGAINST PRODUCTION after the deploy lands. Catches
  CDN-cache staleness, broken upstream feeds, and "deploy succeeded
  but the published bundle has no working data."

### Why three web-side gates (lint + tests + smoke)?

Each catches a distinct failure class:

- **`web-build`** catches syntax errors and broken imports.
- **`web-lint`** catches dangling references (`no-undef`), unused
  imports, and React-hooks violations. JavaScript is dynamically
  typed; a typo'd variable name compiles fine but throws at runtime.
- **`web-tests`** catches contract regressions — the existing
  `tests/*.test.js` files assert that the SST forecast UI, mobile
  interaction guards, and data manifest contract stay wired.
- **`web-smoke`** catches the rest: Puppeteer actually boots the
  bundle and watches React's first-render path. If the build is
  clean, lint is clean, contracts hold, but `<App/>` throws on
  mount, this is the gate that fires.

The 2026-05-07 white-screen incident slipped through because only
`web-build` existed at the time — `lint`, `tests`, and `smoke` were
all gaps. They're closed now.

## Branch-protection rules on `main`

Configured (and re-applicable) via:

```bash
bash scripts/setup-branch-protection.sh
```

The settings:

- Require a pull request before merging
- Require all five status checks to pass
- Require branches to be up to date with main before merging
- Disallow force pushes
- Disallow deletion

There's no required-reviewer count — the user reviews + merges
manually. If you want stricter guardrails, edit the script.

## Special cases

### Hotfixes

Same flow. The dev gate is 90 s end-to-end — the speed-vs-safety
trade-off favors safety for a public-data product where a broken push
silently displays wrong ocean conditions to divers.

### Auto-data refresh

`refresh-data.yml` runs daily at 06:00 UTC and on push to `main`. It
ALSO deploys to production (Cloudflare Pages) and commits refreshed
PNGs back to main. This is the one workflow that touches main
directly — it runs as `github-actions[bot]`, scoped to the
data-refresh path. Don't extend it without a discussion.

### Schedule-only workflows (already on main)

`refresh-wind.yml`, `health-check.yml`, `ingest-ground-truth.yml`,
`promote-baseline.yml` — these run on cron schedules unrelated to
agent commits. They're not affected by the dev gate.

### Reverting a bad merge

```bash
git switch dev
git revert -m 1 <merge-sha>
git push origin dev
gh pr create --base main --head dev --title "Revert <bad change>"
```

The revert PR goes through the same dev-check gate. Do not
force-push to main to "undo" a merge.

## What NOT to do

- ❌ `git push --force origin main` — protection rejects, but don't try.
- ❌ Open a PR from `main` to `main` — pointless.
- ❌ Branch off `dev` for a new feature — branch off `main` so dev
   stays clean (small queue).
- ❌ Push commits to `main` "just to skip CI" — they'll be rejected.
- ❌ Merge a PR with red checks "because it's a small change" — every
   merge to main goes through the gate, no exceptions.

## Manual override (humans only)

Repo admins can bypass branch protection via the GitHub UI's
"Override" button. This exists for emergencies (e.g. if the gate
itself breaks). Agents should never invoke this path; if a check is
flaky, fix the check, not the bypass.
