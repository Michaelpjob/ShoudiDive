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

## What runs in `dev-checks.yml`

Five jobs run in parallel; all five must pass for the PR to be
merge-able:

| Job              | What it catches |
|------------------|-----------------|
| `pipeline-tests` | Python static-compile + pytest unit layer |
| `web-build`      | `npm ci && npm run build` — Vite production bundle |
| `mobile-static`  | `npm ci && jest` in `mobile/` |
| `secrets-scan`   | Committed API keys, `.env`, PEM private keys |
| `workflow-lint`  | actionlint on `.github/workflows/*.yml` |

These job names are wired into main's branch-protection rules. Adding
a new check means: add a job to `dev-checks.yml`, then update the
required-checks list (see `scripts/setup-branch-protection.sh`).

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
