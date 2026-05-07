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
#   - require all five dev-checks status contexts to pass
#   - require branch up-to-date with base before merging
#   - disallow force pushes
#   - disallow deletion
#   - allow admin override (so the user can break-glass if the gate
#     itself ever blocks a real emergency fix)
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
  "mobile-static"
  "secrets-scan"
  "workflow-lint"
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
