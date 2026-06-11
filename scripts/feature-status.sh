#!/usr/bin/env bash
# scripts/feature-status.sh — mechanical ground-truth for in-flight feature branches.
#
# Complements docs/FEATURES.md (which records intent) with reality derived from git
# + GitHub: every feat/* and fix/* branch, how far it is ahead/behind main, whether
# it has an open PR, and that PR's check rollup. When the ledger and this disagree,
# this wins.
#
# Usage:  bash scripts/feature-status.sh
# Needs:  git, and (optionally) gh for the PR + checks columns.

set -u

BASE="origin/main"

echo "Fetching origin..." >&2
git fetch origin --quiet 2>/dev/null || echo "  (fetch failed — showing cached refs)" >&2

HAVE_GH=0
if command -v gh >/dev/null 2>&1; then HAVE_GH=1; fi

printf "\n%-34s %6s %7s %-6s %-16s %s\n" "BRANCH" "AHEAD" "BEHIND" "PR" "CHECKS" "LAST COMMIT"
printf "%-34s %6s %7s %-6s %-16s %s\n" "------" "-----" "------" "--" "------" "-----------"

# All remote feature/fix branches, minus dev/main/HEAD and dependabot noise.
for ref in $(git for-each-ref --format='%(refname:short)' refs/remotes/origin \
              | grep -E '^origin/(feat|fix)' | sort); do
  br="${ref#origin/}"
  ahead="$(git rev-list --count "${BASE}..${ref}" 2>/dev/null || echo '?')"
  behind="$(git rev-list --count "${ref}..${BASE}" 2>/dev/null || echo '?')"
  last="$(git log -1 --format='%cr — %s' "$ref" 2>/dev/null | cut -c1-46)"

  pr="-"; checks="-"
  if [ "$HAVE_GH" -eq 1 ]; then
    num="$(gh pr list --head "$br" --state open --json number --jq '.[0].number // empty' 2>/dev/null)"
    if [ -n "$num" ]; then
      pr="#${num}"
      # Roll up the PR's check states into e.g. "SUCCESS:9" / "FAILURE:1".
      checks="$(gh pr checks "$num" --json state --jq '
        [.[].state] | group_by(.) | map("\(.[0]):\(length)") | join(",")' 2>/dev/null \
        | cut -c1-16)"
      [ -z "$checks" ] && checks="(none)"
    fi
  fi

  # Stale marker: far behind main → likely drifting.
  flag=""
  case "$behind" in ''|*[!0-9]*) : ;; *) [ "$behind" -gt 200 ] && flag="  ⚠ far behind";; esac

  printf "%-34s %6s %7s %-6s %-16s %s%s\n" "$br" "$ahead" "$behind" "$pr" "$checks" "$last" "$flag"
done

echo
echo "Ledger: docs/FEATURES.md   |   ⚠ far behind = rebase on main or it'll drift."
[ "$HAVE_GH" -eq 0 ] && echo "(install gh for the PR + CHECKS columns)"
