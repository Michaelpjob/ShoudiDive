#!/usr/bin/env bash
# merge-to-dev.sh — merge a feature branch into the `dev` preview, the safe way.
#
# `dev` is a long-lived preview that drifts far from `main` (it accumulates
# feature merges AND its own daily data-refresh commits AND another fork's
# direct, un-branched work). So merging any fresh feat/* into it collides on
# dozens-to-hundreds of generated data files — but almost never on real code.
# Hand-resolving 50-150 binary/JSON conflicts every time is the pain this
# script removes.
#
# What it does:
#   * merges origin/<feat> onto the CURRENT origin/dev,
#   * AUTO-resolves conflicts under public/data/** and pipeline/validation/data/**
#     to dev's copy (those are generated; the data-refresh crons own them),
#   * STOPS and lists anything else (a real code conflict) for you to resolve,
#   * pushes dev with a plain (non-force) push, retrying if dev moved meanwhile.
#
# Why never --force: `dev` carries another fork's work that lives ONLY on dev
# (not on main or a feat branch). A force-push would destroy it. A normal push
# is additive and refuses rather than clobber — exactly what we want. (To do a
# clean rebuild of dev instead, that's the separate, destructive procedure in
# CLAUDE.md that needs a human + preserving dev-only work first.)
#
# Usage:
#   bash scripts/merge-to-dev.sh feat/<slug> [feat/<slug2> ...]
#
# Run it from a throwaway checkout/worktree — it switches to a temp branch.
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 feat/<slug> [feat/<slug2> ...]" >&2
  exit 2
fi
BRANCHES=("$@")
DATA_RE='^(public/data/|pipeline/validation/data/)'
TMP="__merge_to_dev_$$"

cleanup() { git checkout -q - 2>/dev/null || true; git branch -qD "$TMP" 2>/dev/null || true; }
trap cleanup EXIT

git fetch -q origin dev "${BRANCHES[@]}"

for attempt in 1 2 3; do
  git checkout -q -B "$TMP" origin/dev
  base="$(git rev-parse --short HEAD)"

  for b in "${BRANCHES[@]}"; do
    git merge --no-ff --no-commit "origin/$b" >/dev/null 2>&1 || true

    # Auto-resolve generated-data conflicts to dev's side.
    auto=0
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      if printf '%s\n' "$f" | grep -qE "$DATA_RE"; then
        git checkout --ours -- "$f"
        git add -- "$f"
        auto=$((auto + 1))
      fi
    done < <(git diff --name-only --diff-filter=U)

    # Anything left is a real code conflict — bail for manual resolution.
    code="$(git diff --name-only --diff-filter=U | grep -vE "$DATA_RE" || true)"
    if [ -n "$code" ]; then
      echo "CODE conflict(s) merging $b (auto-resolved $auto data files):" >&2
      printf '  %s\n' $code >&2
      echo "Resolve them, 'git commit', then 'git push origin HEAD:dev'." >&2
      trap - EXIT
      exit 1
    fi

    git commit -q --no-edit \
      -m "merge $b into dev (preview) — $auto data files auto-resolved to dev, no code conflicts" || true
    echo "merged $b onto dev@$base ($auto data files auto-resolved)"
  done

  if git push origin "$TMP:dev" >/dev/null 2>&1; then
    echo "pushed dev -> $(git rev-parse --short HEAD)"
    exit 0
  fi
  echo "dev moved during merge; retrying ($attempt/3)..." >&2
  git fetch -q origin dev
done

echo "could not push dev after 3 tries (it keeps moving) — re-run." >&2
exit 1
