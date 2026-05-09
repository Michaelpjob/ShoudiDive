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
#   - ALLOW github-actions bot to bypass the PR requirement (so the
#     scheduled data-refresh + ingest workflows can commit refreshed
#     PNGs / observations.jsonl directly to main without opening a PR
#     each cycle — see bypass_pull_request_allowances below)
#
# Adding a new required check:
#   1. Add a job to .github/workflows/dev-checks.yml — copy an existing
#      job's pattern. Job-name = check-context.
#   2. Append the new context to REQUIRED_CHECKS below.
#   3. Re-run this script.
#
# 2026-05-08 update — bypass list added:
#   The refresh-data, refresh-wind, and ingest-ground-truth workflows
#   were all failing on push with `GH006: Protected branch update
#   failed for refs/heads/main. - Changes must be made through a pull
#   request.` That's by design — the PR-required rule applies to
#   everyone by default. This script now grants the github-actions
#   GitHub App an explicit bypass for that single rule, so scheduled
#   bot pushes succeed while human + agent commits still go through
#   the normal dev-checks gate.

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
    "required_approving_review_count": 0,
    "bypass_pull_request_allowances": {
      "users": [],
      "teams": [],
      "apps": ["github-actions"]
    }
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
