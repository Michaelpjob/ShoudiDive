# DECISIONS — judgment calls made during the build

> Working artifact per the PRD (§7): what was ambiguous → what was
> chosen → why. Newest first within each group.

## Water Column PRD — Group V (2026-06-12)

**WC-D8 — "Tap-to-slice" = the existing hover/pin state, not a new
gesture.** Desktop slices wherever the cursor hovers (the same `hover`
state the Tooltip and legend readout already consume — cursor-follow
matches the app's feel and previews Z12's behavior); mobile slices the
tap-to-pin point; with neither, the column pins to the selected saved
spot, whose pipeline sidecar also provides the 24 h cliff series for
the diurnal strip. Zero MapShell changes, no new gesture surface.

**WC-D9 — BETA = visible by default + settings off-switch.** PRD D2
says "behind a settings flag (BETA), consistent with how Current/Vis
already carry BETA tags" — Current/Vis ship VISIBLE with a BETA badge,
so `waterColumnOn` defaults true with the badge and Settings gets the
off-switch. Flag-gating it dark would contradict the cited precedent.

**WC-D10 — Column dock = inside the "How to read this" panel.** The
PRD names "the zone currently holding 'How to read this'"; rendering
the card at the top of that panel's body (rather than a new fixed
panel) inherits its collapse behavior and avoids fixed-position
stacking math against the variable-height moon widget above it.

**WC-D11 — Frontend bathy sampling via direct bathy.png decode.** No
frontend loader existed for the bathy grid (BathyLayer renders
markers only); the vizColumn loader decodes `bathy.png` against
`bathy.json`'s `depth_range_m` and `getColumnAt` samples it with the
shared bilinear helper. When smooth-zoom Z12 lands its higher-res
spot-bundle DEMs, this is the single substitution point.

## Water Column PRD — Group C1 (2026-06-12)

**WC-D1 — Two rasters + a spot sidecar, not three rasters.** Below-cliff
vis (`viz_column_below_ft.png`, 0-80 ft — deliberately the viz layer's
exact range/scale so the existing legend semantics decode it) and cliff
depth (`viz_column_cliff_ft.png`, 0-100 ft) are per-cell rasters; the
diurnal swing is NOT a raster (it's near-constant per month across the
bbox at v1 fidelity) — the scalar `swing_ft` lives on the manifest
layer and the hourly `cliff_series_ft` lives in the per-spot sidecar
where the V5 strip actually needs it.

**WC-D2 — Coefficients are documented guesses anchored on Point Loma.**
All constants in `pipeline/viz_column/config.py` with rationale;
the PRD acceptance anchor is encoded as a unit test
(`test_point_loma_acceptance_anchor`). Notable simplifications, all
flagged in-code: single coastline angle (140°) for the upwelling
alongshore direction (Point Conception's bend is the casualty);
single Coriolis f; constant drag coefficient; monthly cliff-base
table with a flat NorCal deepening factor. C2 (ROMS MLD) replaces the
cliff-base table; C4 tunes the rest.

**WC-D3 — Internal-tide phase assumption.** Cliff deepest at HIGH
water (downwelling phase at the coast), M2 period, amplitude grown by
seasonal stratification strength. This is the weakest-evidence guess
in the model and is isolated behind `PHASE_DEEPEST_AT_HIGH_WATER` so
C4 can flip it from data without touching the series logic.

**WC-D4 — fetch_tides.py extended additively.** The cliff-swing series
needs tide PHASE; tides.json only carried the daily range. Stations
now also publish `events` (hi/lo times + heights from the same CO-OPS
response). Old consumers unaffected; when events are absent the
sidecar publishes the swing band without an hourly series (UI renders
band-only) — never blocks.

**WC-D5 — Phased REQUIRED_LAYERS gating (D1a now, D1b later).**
data-shape + the live probe assert against the COMMITTED/live
manifest; gating `viz_column` before a refresh has published it would
fail every check. C1's PR ships the LayerSpec entry + validator-proven
manifest writer; the REQUIRED_LAYERS flip is a one-line follow-up
after the first post-merge refresh.

**WC-D6 — Spot list duplicated from mapData.js, knowingly.** The
pipeline has no spot registry; the sidecar hardcodes the CA list that
mirrors `src/lib/mapData.js` REGION_SAVED_SPOTS. Unifying them into
one shared registry is queued in QUESTIONS.md (WQ2) rather than
dragging a frontend refactor into a pipeline PR.

**WC-D7 — CA-only at v1, no-op elsewhere.** `ENABLED_REGIONS=("ca",)`
guard + wiring only in refresh-ca-data.yml, per PRD §3 (other regions
inherit the heuristic when their input sets are verified — wave/wind
encodings differ per region overrides).

**WC-D12 — v1.1 cross-shore + regional cliff structure (user review,
2026-06-12).** The user flagged the offshore gradient: v1.0 applied
the upwelling shoaling at every cell, but coastal upwelling lifts the
pycnocline only within ~the baroclinic deformation radius (~10-30 km
off CA), and winds strengthen offshore — so the model shoaled the
cliff MORE offshore, a wrong-signed gradient (a 100 km-offshore point
read 23 ft; the open CA Current summer thermocline is far deeper).
Fixes: (a) upwelling shoaling now decays offshore over
UPWELLING_DECAY_KM=25; (b) the cliff relaxes +OFFSHORE_DEEPEN_FT=20
toward its open-ocean depth over OFFSHORE_DEEPEN_KM=40; (c) regional
bands at the real regime boundaries — Pt. Conception 34.45°N (SoCal
Bight: base table + upwelling damped ×0.6 for the E-W coast) and
Pt. Arena 38.95°N (CenCal +15%, NorCal +30%) — replacing the flat
36°N step that treated Big Sur like the Bight. Distance-to-shore via
scipy EDT on the bathy land mask (10 km cells vs 25-40 km decay
scales: adequate). Point Loma anchor unchanged (kelp line dts≈2 km →
23.8 ft). Known coarseness, accepted: spot sidecar samples the
10 km raster, so a coastal spot's cell can sit ~1 cell offshore and
read ~3-4 ft deep vs the beach value — kept for hover↔sidecar
consistency; C2's model MLD supersedes. June transect after the fix:
La Jolla 24.7→44.5 ft (2→150 km), Monterey 27.3→48.3, NorCal
30.7→52.0.

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
the main-owned data paths to main's set post-merge (see D11 for the
ownership map).

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

**D12 — Post-merge live shakeout (2026-06-12, ~02:40–03:00 Z).**
Merging six PRs in quick succession exercised the new machinery and
surfaced two follow-ups, fixed the same hour: (a) sync-dev's first
real sync carried `.github/workflows/**` changes and GITHUB_TOKEN is
forbidden from pushing workflow files — switched the checkout/push to
BOT_PUSH_TOKEN, which also self-triggers dev-checks on code syncs, so
the explicit dispatch step (and `actions: write`) is gone; (b) the
merge burst's concurrency cancellations made sync-issue count
"cancelled" job results as failures and open a phantom
"failed (2/2 checks)" issue (#141) — only literal "failure" counts
now, both-success claims pass, anything else leaves the rolling issue
untouched. (c) The probe jobs' `node-version: 24` made puppeteer's
postinstall silently skip the Chrome download ("added 376 packages in
14 s", no browser) — the first real verify run then died with "Could
not find Chrome" on both jobs. Reverted those jobs to node 20 (proven
by weeks of green render runs and #143's own web-smoke; D8's logic —
the 06-16 deprecation concerns ACTION majors, not the installed node)
and added an explicit `npx puppeteer browsers install chrome` step
that is a no-op on success and loud on failure. Also confirmed live:
the breaker let refresh-ca-data complete in ~16 min with NASA still
down, production generated_at went 39.9 h stale → fresh, and
#90/#130/#6 closed on merge.

**D11 — Region-data ownership map (caught + corrected mid-build).**
All region data nests under `public/data/` — CA flat, `baja/`,
`pnw/`, `tropical/` as subdirs (NOT `public/data-<region>/` as the
first draft of the coherence pass assumed). Ownership follows each
refresh workflow's `ref:` line: ca + baja (prod) refresh on main →
main-owned; pnw + tropical (beta) refresh on dev → dev-owned, and
main's copies lag by weeks. The first coherence draft (and the first
manual dev push) reset ALL of `public/data/` from main, which
clobbered dev's fresh pnw/tropical with main's ~449 h-stale copies and
flipped dev-checks' failure from [ca]-dims to pnw/tropical-freshness.
Corrected: the pass resets main-owned paths then restores the
dev-owned subdirs from the pre-merge dev tree; the manual push was
amended the same way. When a beta region is promoted to prod (its
refresh moves to main), it must be removed from DEV_OWNED_SUBDIRS in
sync-dev.yml.
