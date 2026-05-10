#!/usr/bin/env bash
# scripts/setup-branch-protection.sh
#
# Idempotent. Re-apply the branch-protection rules on `main` whenever
# the required-status-check list changes (or to verify the rules
# haven't drifted). Requires:
#
#   * gh CLI authenticated as a repo admin
#   * permissions: admin on Michaelpjob/ShoudiDive
#
# What this enforces on the `main` branch:
#
#   - require pull request before merging
#   - require all listed dev-checks status contexts to pass
#   - require branch up-to-date with base before merging
#   - disallow force pushes
#   - disallow deletion
#   - allow admin override (so the user can break-glass if the gate
#     itself ever blocks a real emergency fix)
#
# How scheduled bot pushes get past PR-required:
#   The refresh-data, refresh-wind, and ingest-ground-truth workflows
#   commit refreshed data directly to main on cron. Personal repos
#   can't use `bypass_pull_request_allowances` (that's org-only — the
#   GitHub API returns 422 with 'Only organization repositories can
#   have users and team restrictions'). The workaround on a personal
#   repo: have the workflow's `git push` step authenticate as a token
#   with admin permissions; admins are exempt from PR-required.
#
#   Setup (one-time, by repo owner):
#     1. https://github.com/settings/tokens/new — classic PAT, scope:
#        `repo`. Note: a fine-grained token with Contents:Write +
#        repo:admin equivalents also works.
#     2. Repo → Settings → Secrets → Actions → New: name BOT_PUSH_TOKEN,
#        paste the PAT.
#     3. The cron workflows already check ${{ secrets.BOT_PUSH_TOKEN }}
#        and fall back to GITHUB_TOKEN if the secret is missing — so
#        nothing breaks if you skip the PAT step, the bot push just
#        keeps failing with GH006 until the secret is added.
#
# Adding a new required check:
#   1. Add a job to .github/workflows/dev-checks.yml — copy an existing
#      job's pattern. Job-name = check-context.
#   2. Append the new context to REQUIRED_CHECKS below.
#   3. Re-run this script.

set -euo pipefail

REPO="Michaelpjob/ShoudiDive"
BRANCH="main"

REQUIRED_CHECKS=(
  "pipeline-tests"
  "web-build"
  "web-lint"          # ESLint — catches `no-undef` (would have caught
                      # the 2026-05-07 white-screen bug)
  "web-tests"         # node --test contract suites (formerly orphaned)
  "web-smoke"         # Puppeteer runtime boot — catches mount-time crashes
  "mobile-static"
  "secrets-scan"
  "workflow-lint"
  "manifest-validate" # LayerSpec contract — catches range/scale drift
                      # between pipeline encoder and frontend decoder
                      # (added 2026-05-09 in the Tier-2 architecture PR)
)

# Build the JSON contexts list inline.
contexts_json=$(printf '"%s",' "${REQUIRED_CHECKS[@]}")
contexts_json="[${contexts_json%,}]"

# Use the GH REST API directly — `gh api` PUT with the full
# protection payload. Older `gh` versions don't have a high-level
# `branch protect` command and the API gives precise control.
echo "Applying branch protection to ${REPO}:${BRANCH}…"
echo "Required status checks: ${REQUIRED_CHECKS[*]}"

# Render the payload via printf so we can inline the contexts array.
payload=$(cat <<EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": ${contexts_json}
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": false,
  "required_linear_history": false
}
EOF
)

echo "${payload}" \
  | gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" \
      --input - \
      -H "Accept: application/vnd.github+json" \
  | tee /tmp/branch-protection-result.json >/dev/null

echo
echo "✓ Branch protection applied."
echo
echo "Verify by visiting:"
echo "  https://github.com/${REPO}/settings/branches"
echo
echo "Or via the API:"
echo "  gh api repos/${REPO}/branches/${BRANCH}/protection --jq '.required_status_checks.contexts'"
