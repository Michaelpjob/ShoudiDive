# PLAN — Smooth Macro→Micro Zoom + Platform Stabilization + Water Column

> Working artifact per the PRD (§7). One section per requirement group.
> Kept current as the build progresses. See DECISIONS.md for judgment
> calls and QUESTIONS.md for parked non-blocking questions.

## Group S — Stabilize (in flight: PR `feat/stabilize-pipeline`)

- [x] **S1 — live-cp-manifest green.** Two root causes found and fixed:
  - Cloudflare bot-scoring intermittently 403'd the bare `node:fetch`
    probe from GHA runner IPs (UA spoofing had already been tried —
    TLS fingerprint mismatch). Probe now runs from inside real headless
    Chrome (`tests/live-checkpoints/live-manifest.mjs`, same transport
    as the always-green live-cp-render). Assertions unchanged.
  - A REAL producer failure underneath: NASA OB.DAAC has been down
    globally since ~2026-06-09; chl/kd490 searches retried ~7 min/date
    and blew refresh-ca-data's 35-min step timeout → production
    manifest >36 h stale. Fixed with a per-host circuit breaker +
    connect/read timeout split in `pipeline/lib/http.py` so a
    connect-dead upstream degrades that layer instead of killing the
    refresh.
  - [ ] After merge: dispatch `refresh-ca-data.yml` once to clear the
    40 h staleness; verify deploy-verify green on two consecutive
    scheduled runs (issues #130/#90 auto-close).
- [x] **S2 — sync-issue + alert-router reliability.**
  - deploy-verify `sync-issue`: ran `gh` with no checkout and no
    `GH_REPO` → every open/update/close died ("not a git repository")
    since 2026-05-26. Fixed via `GH_REPO` env.
  - alert-router: heartbeat `gh issue list` lacked `|| true` under
    `set -e` (the 2026-05-26/31 router failures); classify-step `head -1`
    SIGPIPE hazard under pipefail; startup_failure runs report workflow
    PATH not display name → issues opened under the path never matched
    close-on-green (stuck #95). All fixed; path→name normalization via
    the workflows API.
  - [ ] After merge: manually close stale #95 if the next router run
    doesn't (title now normalizes, so it should self-close).
- [x] **S3 — dev-checks green on dev; no data-triggered runs.**
  - Root cause of #137: ca-beta refreshes write prod's `public/data/`
    on dev BY DESIGN; sync-dev's hunk-level `-X theirs` merge mixed
    main's and dev's atomic manifest+PNG sets into a franken-manifest
    (sst grid 586×511 vs PNG 234×206). `fetch.py` was already correct.
  - sync-dev now resets `public/data/` wholesale to main's coherent
    set after every merge (beta-region dirs untouched); dev-checks
    `paths-ignore`s `public/data*/**` on push; sync-dev dispatches
    dev-checks only when a sync brings CODE.
  - [x] One-time: push coherence commit to dev to clear the live red.
        (done 2026-06-12, commit `sync(manual)` on dev — next
        dev-checks run is green, #137 closes via alert-router)
- [x] **S4 — Node 24.** All workflows on node24-native action majors:
  checkout@v6, setup-node@v6, setup-python@v6, upload-artifact@v7,
  download-artifact@v8, github-script@v9. Job-level `node-version`
  pins for BUILD jobs left at 20 (runtime choice, separate from the
  actions deprecation; see DECISIONS.md).
- [x] **S5 — data-health critical (#6).** Same two root causes as S1
  (probe 403 + stale manifest). health-check.yml now consumes the
  browser-transport probe via `--report` (same JSON schema + exit
  codes); `check_feeds.py` unchanged. NASA OB.DAAC red feed entry is
  a true upstream outage — stays visible as a feed finding, which is
  correct and advisory-only.
- [x] **S6 — PR queue cleared** (executed 2026-06-12 02:35–03:00 Z
  under user-granted merge authority):
  1. [x] #136 merged 02:35 Z (swell double-count + beta-freshness skip).
  2. [x] #140 merged 02:39 Z (this stabilization PR) — #90/#130/#6
     closed on merge; production manifest refreshed to 02:43 Z via
     dispatched refresh-ca-data (succeeded in ~16 min with NASA still
     down — breaker verified live).
  3. [x] #135 merged (conflict with #136's freshness skip resolved —
     duplicate implementation dropped, #136's kept).
  4. [x] #138 merged (per-feature promotion process now canonical).
  5. [x] Dependabot #132/#76/#58 merged.
  6. [x] #134 closed with disposition comment; spot-detail re-cut onto
     `feat/spot-detail-ncei` is the next work item (hard prereq Z12).
  7. [x] #102 closed with disposition comment (re-cut inside Z14).
  8. #139 (field reports) — another agent's WIP draft; left open.
  - [ ] Shakeout PR (sync-dev BOT_PUSH_TOKEN + cancelled-run
    alerting): merge, then confirm dev syncs (brings the breaker to
    dev before the ~10:00 UTC pnw/tropical crons) and #141/#137/#95
    auto-close.

## Group T — Streamline (next after S merges)

- [ ] T7: consolidate per-region workflows into `workflow_call`
  reusables + thin per-region callers (27 → ≤ ~12 files). Alert-router
  name mapping must follow renames (normalization from S2 helps).
  Consider isolating ca-beta data to `public/data-ca-beta/` here —
  removes the S3 clobber-window tradeoff (see DECISIONS.md D6).
- [ ] T8: extract bundle loader/cache + bathy decode + render helpers
  from `SpotDetailView.jsx` into `src/lib/spotBundles.js` +
  `src/components/micro/*` (pure refactor); delete orphaned
  `src/components/MpaPopup.jsx`.

## Group Z — Smooth zoom (after S + T8 + #134 re-cut)

- [ ] Z9 bundle-in-viewport → Z10 LOD crossfade → Z11 conditions card
  → Z12 GPS+depth readout → Z13 legible contours → Z14 kelp truth →
  Z15 POIs → Z16 breakout retirement + deep links → Z17 spot fan-out.
  (Dependency order per PRD §2; not started.)

## Group R — Tile pyramid (USER-BLOCKED on R2 provisioning, PRD §6)

- [ ] R18 R2 bucket + tile worker; R19 publish_tiles.py; R20 XYZ in
  SVG stage. Blocked until the user provisions R2 (see QUESTIONS.md).

---

# Water Column PRD (depth-resolved vis) — `docs/PRD-water-column.md`

## Group C — column model

- [x] **C1 — v1 heuristic (PR `feat/water-column`).**
  `pipeline/viz_column/` (pure model: Bakun/Ekman upwelling from wind
  U/V; Hunt-dispersion near-bottom orbital velocity → resuspension;
  seasonal-MLD cliff base shoaled by upwelling, NorCal-deepened;
  M2 internal-tide swing phase-locked to CO-OPS high water; below-
  cliff attenuation) + `pipeline/fetch_viz_column.py` (I/O, CA-only)
  + `fetch_tides.py` now also publishes hi/lo events. Outputs:
  `viz_column_below_ft.png` (0-80 ft, viz legend semantics),
  `viz_column_cliff_ft.png` (0-100 ft), `viz_column_spots.json`
  (10 spots, 24 h cliff series). Verified end-to-end locally against
  committed data + live NOAA tides: La Jolla = surface 14.8 /
  cliff 25.0 / below 5.4 ft. 19 unit tests incl. the Point Loma
  acceptance anchor + encoder↔LayerSpec contract.
- [ ] **C2 — stratification fetcher** (ROMS/HYCOM MLD + thermocline
  rasters) → replaces the seasonal-climatology cliff base.
- [ ] **C3 — below-cliff model upgrade** (stratification + subsurface-
  chl proxy).
- [ ] **C4 — calibration harness** (Spray glider Point Loma line,
  Scripps Pier, CalCOFI, depth-stratified diver reports via #139).
- [ ] **C5 — per-layer confidence** (extend src/lib/confidence.js).

## Group V — visualization (after C1 merges + data publishes)

- [ ] V1 WaterColumn.jsx widget → V2 tap-to-slice (bathy.png sampling
  until smooth-zoom Z12 lands) → V3 planned-depth marker → V4 right
  rail + saved-spots two-number rows + "surface" legend relabel →
  V5 diurnal strip (cliff_series_ft) → V6 ground truth via #139.
  Flip `frontend_renders` + add BETA pref (D2) in V4's PR.

## Group D — contract + gating

- [x] **D1a (in C1's PR):** LayerSpec `viz_column` entry; manifest
  contract validated via `python -m pipeline.validate_manifest`
  against a locally-built manifest; CHECKPOINTS.md row.
- [ ] **D1b (follow-up after the first post-merge refresh publishes
  the layer):** add `viz_column` to REQUIRED_LAYERS in
  `tests/checkpoints/data-shape.test.js` + `tests/live-checkpoints/
  live-manifest.mjs`. Adding it before the data exists in the
  committed manifest would fail dev-checks/live probes — phased on
  purpose.
- [ ] **D2 — BETA flag** (PrefsContext pref + UI gating; lands with V4).
