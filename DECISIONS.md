# DECISIONS — judgment calls made during the build

> Working artifact per the PRD (§7): what was ambiguous → what was
> chosen → why. Newest first within each group.

## Group S (2026-06-12)

**D1 — "Fix the producer, not the probe" met two distinct failures; both fixed.**
The PRD hypothesized staleness/missing-layer/PNG-decode. Reality: (a)
Cloudflare bot-scoring 403'd the probe's bare `node:fetch` from GHA
runner IPs intermittently for weeks (the 2026-05-26 "CDN warmup" sleep
and the spoofed Chrome UA were earlier mitigations of the same
misdiagnosed cause), AND (b) a genuine producer failure — NASA OB.DAAC
down globally since ~06-09, whose per-date retry stalls (240 s read
timeout × 3 attempts × 5 dates × 2 products) blew the 35-min fetch
step and left production's manifest >36 h stale. Probe transport moved
into real Chrome (same client as live-cp-render and as real users; all
assertions/thresholds unchanged), and the pipeline got a per-host
circuit breaker. Neither alone would have made deploy-verify durably
green.

**D2 — Circuit breaker semantics (pipeline/lib/http.py).**
Threshold 2 consecutive fully-failed calls per host; 300 s cooldown
with a single half-open probe; only transport-layer failures count
(any HTTP response, even 5xx, proves the host alive and resets);
scalar timeouts split into (10 s connect, caller read). Rationale:
fail fast on connect-dead hosts without ever masking HTTP-level
errors, and without permanently blinding a long refresh if the host
recovers mid-run. `use_breaker=False` escape hatch for
reachability-measuring callers.

**D3 — deploy-verify's 90 s "CDN warmup" sleep removed.**
It was added (2026-05-26) for 403s now explained by bot-scoring, and
Pages deploys are atomic. If post-deploy runs ever flap again the
cause will be visible in the probe's new CF diagnostic headers
(cf-mitigated / cf-ray logged on failure) instead of being slept over.

**D4 — health-check's live probe consolidated onto the node probe.**
`check_published.py`'s python-requests live fetch had the same 403
exposure (it kept #6 open with a phantom critical). health-check.yml
now runs `live-manifest.mjs --report` (same JSON schema/exit codes);
check_published.py remains for refresh-workflow-local validation.
Behavior delta accepted: medium-only findings no longer open the
data-health issue (exit 0 with warns rendered in the body when an
issue is otherwise open) — less noise, criticals/highs unchanged.

**D5 — dev's broken data: fixed at the merge boundary, not in fetch.py.**
fetch.py already derives manifest grid dims from the actual arrays;
the 586×511-vs-234×206 mismatch on dev was a git-merge artifact (two
bots writing the same files on two branches; hunk-level `-X theirs`
mixes their atomic sets). No pipeline guard can prevent a merge from
pairing old manifest with new PNGs, so sync-dev now wholesale-resets
`public/data/` to main's set post-merge.

**D6 — Accepted tradeoff: ca-beta preview data gets clobbered hourly.**
After every sync (≈hourly, on main's wind refreshes), dev's
`public/data/` equals main's; ca-beta's own refresh re-asserts dev's
pipeline output until the next sync. While no dev-side pipeline
changes are in flight this is invisible. The durable fix — an isolated
`public/data-ca-beta/` like the other betas — belongs in Group T's
workflow consolidation (T7) and is noted in PLAN.md.

**D7 — dev-checks `paths-ignore` on push only, not pull_request.**
Feature PRs must never carry `public/data*` changes (bot-owned paths),
so PR-event filtering is unnecessary — and #134, the only PR whose
head received bot data pushes (the action_required stalls), is being
closed as an anti-pattern rather than accommodated.

**D8 — Build jobs stay on Node 20.**
The 2026-06-16 deadline concerns the ACTIONS' runtime (node20-based
action majors), fixed by bumping to node24-native majors everywhere.
Changing the `node-version` the build jobs install is a separate
runtime decision with Vite-output blast radius — not bundled into a
stabilization PR. The two probe jobs I authored run node 24.

**D9 — Stale alert issues: fixed forward, plus normalization.**
alert-router now maps workflow PATH → display name (startup_failure
runs report the path), so path-titled issues like #95 self-close on
the next green window instead of dangling forever.

**D10 — One-time manual coherence push to dev.**
Pushed main's `public/data/` to dev directly (bot-owned, disposable
preview data; rewritten by bots within hours) so #137 clears today
instead of waiting for the next organic sync after this PR merges.
