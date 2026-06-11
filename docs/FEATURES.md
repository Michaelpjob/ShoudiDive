# Feature ledger

**Source of truth for in-flight code features** — what each is, which branch carries
it, its promotion status, and who owns it. Both Claude Code and Codex **read + update
this** when they start, finish, or block a feature.

For mechanical ground-truth (branch ahead/behind `main`, open PR, gate state), run:

```bash
bash scripts/feature-status.sh
```

The ledger records *intent*; the script records *reality*. When they disagree, the
script is right — fix the ledger.

---

## How we ship (hybrid model)

The unit of bundling **and** promotion is a **feature**, not the `dev` branch. This is
the whole point: a finished feature must never wait on an unfinished one.

- **Isolated / risky / pipeline work → a `feat/<slug>` branch off `main`.** Promote it
  on its own via `feat/<slug> → main` (dev-checks gates the PR; a human merges).
- **Gateable UI → merge to `main` early, dark behind a flag** (`PROD_REGIONS` in
  `src/lib/region.js`, a `PrefsContext` pref, or a `*-beta` subdomain) and flip it on
  when ready. No long-lived branch to drift.
- **`dev` is a disposable preview only** — rebuilt as `main` + the active feature
  branches you want to eyeball together at `dev.shouldidive.pages.dev`. **Never open a
  promotion PR from `dev`.** (See `CLAUDE.md` → "How to ship a change".)

## Status vocabulary

| Status | Meaning |
|--------|---------|
| `planning` | scoped, not yet coding |
| `wip` | actively building on its branch |
| `ready` | complete + gate-green, PR open, awaiting human merge |
| `blocked` | waiting on a dependency — note what in Notes |
| `shipped` | merged to `main` (drop the row after a cleanup window) |

---

## Active features

| Feature | Branch | Status | PR | Owner | Notes |
|---------|--------|--------|----|----|------|
| Offshore swell double-count fix | `fix/swell-windsea-doublecount` | `ready` | [#136](https://github.com/Michaelpjob/ShoudiDive/pull/136) | claude | Off `main`, gate green. Also carries the beta-freshness skip that unblocks isolated→main PRs. |
| Baja Pacific-vs-Cortez viz | `fix/baja-pacific-viz` | `ready` | [#135](https://github.com/Michaelpjob/ShoudiDive/pull/135) | claude | Off `main`. Was red on beta-region lag; rebase on `main` after #136 merges (it carries the skip) → green. |
| Feature-tracking process | `feat/ground-truth-engine` | `ready` | _this PR_ | claude | This ledger + `scripts/feature-status.sh` (the ground-truth engine) + the CLAUDE.md/AGENTS.md workflow rewrite. |
| CDIP MOP nearshore swell | `feat/mop-nearshore` | `wip` | — | claude | Phase 2 of the swell-accuracy effort (per-spot + ribbon + buoy validation). Branched off `dev` because the readout UI needs dev-only SpotDetailView; **the MOP _pipeline_ could be split to a `main`-based branch to decouple it** (see plan `precious-leaping-lobster.md`). |
| Kelp bed zones (observed canopy) | _canonical on `dev`_ → extract to `feat/kelp-canopy` | `wip` | — | claude / codex | CA-gated. **Dedupe done:** the old admin-beds MVP (PR #102, `feat-kelp-mvp`, 1 commit) is **superseded** by the canopy work on `dev` (`KelpLayer.jsx` 3 commits + `fetch_kelp_canopy.py`). #102 closed. Canonical kelp still needs extracting from `dev` to its own branch. |
| Spot Detail chart-plotter | _mid-rebuild on `dev`_ → extract to `feat/spot-detail-chartplotter` | `wip` | [#134](https://github.com/Michaelpjob/ShoudiDive/pull/134) | claude | Task Spot-U, "land-on-top" rebuild, +1300 lines, not finished. **#134 is a `dev→main` PR (the anti-pattern)** — should be closed and the work extracted to its own feature branch before it promotes. |

## Recently shipped

_(none yet under this process — move `ready`→here as PRs merge to `main`)_

---

## Known cleanup (not blocking, do when convenient)

- **Extract `dev`'s two real features** (kelp-canopy, spot-detail-chartplotter) into clean
  `feat/*` branches off `main`, then **rebuild `dev`** as `main` + those branches so it's a
  fresh preview instead of a 990-commit accumulation. Destructive (force-push `dev`) — see
  `CLAUDE.md` → "Rebuilding the dev preview" and confirm before running.
- **Close anti-pattern PRs**: #134 (`dev→main`) once spot-detail is on its own branch.
