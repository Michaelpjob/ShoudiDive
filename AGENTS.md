# Repository conventions for coding agents

This file is read automatically by Claude Code on every session.
A copy at `AGENTS.md` is read by OpenAI Codex and other agents that
follow that convention. The two files are kept identical — edit one,
copy to the other.

## TL;DR — branching rules

**One feature = one `feat/<slug>` branch off `main`, promoted on its own.**
A finished feature must never wait on an unfinished one — so we never bundle
everything through a single long-lived `dev` line.

```
feat/<slug> off main ──► push ──► open PR: feat/<slug> → main
                                          │
                                          ▼
                                  dev-checks.yml  (≈ 90 s)
                                          │
                                          ▼
                                  human reviews + merges
                                          │
                                          ▼
                                  deploy to production (shouldidive.com)

  (optional) merge feat/* into `dev` → combined preview at
  dev.shouldidive.pages.dev — but NEVER promote FROM `dev`.
```

**Never push directly to `main`** (branch protection rejects it) and
**never open a promotion PR from `dev`** — that drags every unfinished
feature on `dev` into the PR, which is the exact trap this process exists
to avoid. Each feature promotes via its own `feat/<slug> → main` PR.

Track in-flight features in [`docs/FEATURES.md`](docs/FEATURES.md); see
mechanical ground truth (ahead/behind, open PR, checks) with
`bash scripts/feature-status.sh`.

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

1. **Branch off `main`, one branch per feature:**
   ```bash
   git fetch origin
   git switch -c feat/<slug> origin/main
   ```
   Add a row to [`docs/FEATURES.md`](docs/FEATURES.md) with `status: wip`.

2. **Make your change. Commit normally** (do not skip hooks).
   - **Dark-launch when you can:** if it's gateable UI, you may merge it to
     `main` early *behind a flag* — `PROD_REGIONS` (`src/lib/region.js`), a
     `PrefsContext` pref, or a `*-beta` subdomain — and flip it on when
     ready, instead of holding a long-lived branch that drifts from `main`.

3. **Push the feature branch:**
   ```bash
   git push -u origin feat/<slug>
   ```

4. **Open the PR against `main` — from the feature branch, NOT `dev`:**
   ```bash
   gh pr create --base main --head feat/<slug> --title "<concise title>" \
     --body "<short summary>"
   ```
   `dev-checks.yml` fires on `pull_request → main`, so a `feat/* → main` PR
   gets the identical gate without routing through `dev`.

5. **Wait for `dev-checks.yml` to go green** (`gh pr checks <num>` or
   `gh run watch <id>`). To eyeball UI before merge, merge the branch into
   `dev` and use `https://dev.shouldidive.pages.dev` — `dev` is a throwaway
   preview, never the promotion source.

6. **Set the ledger row to `status: ready`. Wait for the human to review +
   merge.** Do not auto-merge.

## Feature tracking + the `dev` preview

- **[`docs/FEATURES.md`](docs/FEATURES.md)** is the source of truth for what
  each in-flight feature is, which branch carries it, and its status. Update
  it when you start (`wip`), finish (`ready`), block (`blocked`), or ship a
  feature. `bash scripts/feature-status.sh` prints the mechanical reality
  (every `feat/*`/`fix/*` branch's ahead/behind vs `main`, open PR, checks)
  — when it disagrees with the ledger, the script is right.

- **`dev` is a disposable preview, not a promotion lane.** Its only job is to
  let you eyeball several in-flight features together at
  `dev.shouldidive.pages.dev`. Because nothing is ever promoted *from* `dev`,
  a half-finished feature sitting on it blocks nothing.

### Rebuilding the `dev` preview

When `dev` drifts far from `main` (the `feature-status.sh` "far behind"
marker), rebuild it as `main` + the active feature branches you want to
preview. **This force-pushes `dev` — destructive; confirm with the human
first**, and only after the real work on `dev` is preserved on its own
`feat/*` branch:

```bash
git fetch origin
git switch -C dev origin/main
for b in feat/<a> feat/<b>; do git merge --no-edit "origin/$b"; done
git push --force-with-lease origin dev
```

The data-refresh crons repopulate `dev`'s data on their next run, so the
discarded data history is not a loss.

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
- ❌ Open a promotion PR from `dev` → `main` — it bundles every
   unfinished feature on `dev` into the PR. Promote per-feature:
   `feat/<slug> → main`.
- ❌ Branch off `dev` for a new feature — branch off `main` so each
   feature is independently promotable.
- ❌ Leave a `ready` feature stranded behind an unfinished one — that's
   the trap [`docs/FEATURES.md`](docs/FEATURES.md) exists to prevent.
- ❌ Push commits to `main` "just to skip CI" — they'll be rejected.
- ❌ Merge a PR with red checks "because it's a small change" — every
   merge to main goes through the gate, no exceptions.

## Manual override (humans only)

Repo admins can bypass branch protection via the GitHub UI's
"Override" button. This exists for emergencies (e.g. if the gate
itself breaks). Agents should never invoke this path; if a check is
flaky, fix the check, not the bypass.

## In-app analytics

Privacy-respecting usage tracking: no cookies, no third-party
trackers, no PII. Events post to our own Cloudflare Pages Function
at `/api/analytics/event` from the same origin.

Architecture:

```
React app (App.jsx, components)
   │
   │  track("layer_change", { from, to })
   ▼
src/lib/analytics.js   buffer 25 events / 30 s
   │
   │  POST /api/analytics/event   (sendBeacon for tab-close reliability)
   ▼
functions/api/analytics/event.js   (Cloudflare Pages Function)
   │
   ├── Phase 1 (today): console.log → Cloudflare Pages
   │                    Real-time Logs tab
   └── Phase 2 (todo):  also write to ANALYTICS_KV
                        bound namespace; /stats page reads it
```

Tracked events today:
- `pageview` — fired once per tab on init
- `layer_change` — sst/chl/wind/swell/current/viz chip clicks (props: from, to)
- `sst_mode_change` — history vs forecast toggle
- `spot_click` — saved-spot panel selections (props: from, to, layer)
- `popup_open` — MPA / bathy-feature popups
- `settings_change` — theme, units, opacity, mpaOn, bathyOn
- `tip_click` — Venmo tip jar in topbar

**Reading the data (Phase 1 — right now):**
```bash
# Cloudflare dash → Workers & Pages → shouldidive → Logs tab → live tail.
# Or via wrangler:
npx wrangler pages deployment tail --project-name=shouldidive | grep ANALYTICS
```

**Privacy contract:**
- No IP address logged (Cloudflare provides it; we discard it)
- No User-Agent logged
- Country code from `request.cf.country` is logged (already-anonymized
  by Cloudflare, useful aggregate signal)
- Session ID is a random 64-bit token in `sessionStorage` — dies on
  tab close
- Honors browser DNT and a `localStorage["sd:analytics:off"]="1"`
  opt-out flag; analytics never fires for users who set either.

**Adding a new event type:**
1. Pick a name from the allowlist in
   `functions/api/analytics/event.js`'s `ALLOWED_NAMES` set, or add a
   new entry there. Names not in the allowlist are silently dropped
   server-side (defends against client-side bugs flooding logs).
2. Call `track("event_name", { prop1, prop2 })` from the React tree.
   Props are flat primitives only (string/number/bool/null).
3. Verify the event lands by tailing the Cloudflare logs (above).
