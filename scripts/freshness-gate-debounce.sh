#!/usr/bin/env bash
# Post-publish freshness gate with single-flap tolerance.
#
# Wraps pipeline/check_manifest_freshness.py for the "red run on breached
# budgets" step in the refresh workflows. The gate is post-publish — going
# red blocks nothing; its ONLY effect is notification (a failure email per
# run + alert-router escalation). NOMADS/GFS routinely return partial
# forecast hours for a single cycle and heal on the next run, which under
# the plain gate produced a failure email several times a day for upstream
# weather-server load we cannot act on (2026-08-10: five red CA-wind runs
# in one day, alternating with green).
#
# Debounce rule: a breach goes red ONLY if the previous completed run of
# this same workflow also failed. One-off flaps become a green run with a
# ::warning:: annotation (the findings still land in the step log and in
# pipeline/validation/data/freshness_health.json, so the watchdog and any
# human reading the run see them). A REAL outage fails run after run, so
# it goes red from its second consecutive breach — alert-router's
# "3+ failures in 2 h" escalation still works on top, one cycle later.
#
# Failure direction: if the run-history query itself fails (API hiccup,
# missing token), we go RED, not green — never trade a lost alert for a
# quiet inbox.
#
# Usage (from a workflow step, needs GH_TOKEN + actions:read):
#   bash scripts/freshness-gate-debounce.sh --layers wind,wind5d --skip-top-level --fail-on high
set -uo pipefail

python pipeline/check_manifest_freshness.py "$@"
gate=$?
if [ "$gate" -eq 0 ]; then
  exit 0
fi

# Breached. Look up the conclusion of the previous completed run of this
# workflow on this branch (skipping the current run, and skipping
# cancelled/skipped runs — a concurrency cancel is not evidence either way).
wf_file=$(basename "${GITHUB_WORKFLOW_REF%%@*}")
prev=$(gh run list --repo "$GITHUB_REPOSITORY" --workflow "$wf_file" \
  --branch "$GITHUB_REF_NAME" --limit 15 \
  --json databaseId,conclusion \
  --jq "[.[] | select(.databaseId != ${GITHUB_RUN_ID})
             | select(.conclusion == \"success\" or .conclusion == \"failure\")][0].conclusion" \
  2>/dev/null) || prev=""

if [ "$prev" = "success" ]; then
  echo "::warning title=Freshness budget breached (first occurrence — tolerated)::The freshness gate found high-severity findings (see step log + freshness_health.json), but the previous $wf_file run was green, so this is treated as an upstream flap. A second consecutive breach will fail the run."
  echo "debounce: previous $wf_file run succeeded — tolerating first breach (exit 0)"
  exit 0
fi

echo "debounce: previous $wf_file run conclusion is '${prev:-unknown}' — sustained breach, going red"
exit "$gate"
