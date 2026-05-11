# Pipeline + dashboard backlog

Queued work for the visibility pipeline and the web dashboard. Listed
in priority order. Do NOT execute without explicit go-ahead — these
are loaded into the agent's todo list and picked up sequentially when
the user unblocks each.

The first three items (PR1–PR3) are pipeline-side and target the
"too clear" diagnosis in `02-fix.md`. PR4 is web-frontend dashboard
work — independent of PR1–PR3, can be picked up in parallel by a
different agent.

---

## PR1 — Chl freshness fix (HIGHEST PRIORITY, ~50 LOC, no source change)

**Symptom**: visibility model showing places clearer than they actually
are, especially Coronados / Bight islands / cloudy NorCal days.

**Root cause** (`02-fix.md` § "Smoking gun #1"):
`pipeline/fetch.py:build_layer()` walks back up to 7 days hunting for
a non-cloudy chl pixel and writes whatever it found into
`chl_1d.png`. `pipeline/fetch_visibility.py` then reads that PNG and
hardcodes `age = 0.0`, telling the model "this is fresh today's
observation" even when the actual data is 4 days stale. Three
downstream consequences:

1. `persistence_with_decay` weight stays at 1.0 instead of decaying
   toward climatology (real-age weight would be 0.07–0.51).
2. `effective_sigma` keeps p10/p90 narrow when it should widen.
3. `assign_quality` flags everything `OBSERVED_1D` even when stale.

**Fix scope**: ~50 LOC, additive only.

  * `pipeline/fetch.py` — emit `chl_1d_age_days.png` sidecar (mode='L',
    pixel = age_days + 1; 0 = no data) using `build_age_array()` over
    the same `stack` it already iterates. Wrap with
    `if layer.startswith("chl"):` so PR2/PR3 chl variants get the
    same sidecar automatically.
  * `pipeline/fetch_visibility.py` — read the sidecar, decode, replace
    the hardcoded `age = 0.0` with the real per-cell ages.
  * `viz_predict/model.py` — no change; it already handles real ages.
  * Add `pipeline/tests/test_freshness.py` with the round-trip + the
    `assign_quality` end-to-end test.

**Manual validation**: re-run `fetch.py` + `fetch_visibility.py` for a
known-cloudy day before/after the fix; `viz_p50_ft.png` should show
LOWER viz numbers in the affected cells, and `viz_quality.png` should
shift from code 1 (`OBSERVED_1D`) to codes 2–5 in those same cells.

Spec: `02-fix.md` § "Smoking gun #1" + the build plan § "PR1".

---

## PR2 — DINEOF gap-filled chl as new primary blended source (MEDIUM PRIORITY)

**Wait for PR1 to ship + a week of data** before starting this.

**REVISED FROM EARLIER QUEUE**: original plan was NOAA MODIS Aqua daily
(`erdMH1chla1day`). New plan is NOAA NESDIS DINEOF gap-filled
multi-sensor chl — **stronger choice** because it's already merged
across VIIRS S-NPP + NOAA-20 + Sentinel-3A OLCI AND is gap-filled by
construction. MODIS Aqua had its own NaN gaps; DINEOF doesn't.

* **Dataset**: `noaacwNPPN20S3ASCIDINEOF2kmDaily`
* **Server**: `coastwatch.noaa.gov/erddap/griddap` (national CoastWatch
  ERDDAP — different host than the existing PFEG ERDDAP for SST + the
  legacy chl layer)
* **Coverage**: global 2 km, science-quality NRT, ~2 day latency,
  gap-free
* **Variable**: `chlor_a`

**Behaviour**: per-cell priority logic — prefer DINEOF when (a) it has
a valid value AND (b) its age ≤ 3 days. Otherwise fall back to legacy
VIIRS NRT, then climatology.

**Gated behind**: `ENABLE_DINEOF_CHL=1` env var. Default off until A/B
validation looks clean.

**Implementation tactic**: introduce `ERDDAP_NOAA_CW` constant for the
new server; thread `cfg.get("server", ERDDAP_BASE)` through
`erddap_url()` so existing layers keep working unchanged. Same
`build_layer` flow handles the new layer — it's layer-agnostic.

**Validation tooling**: `pipeline/diff_chl_sources.py` prints coverage
delta + per-cell value delta between sources. Run for a week before
flipping the gate.

Spec: build plan § "PR2 — DINEOF gap-filled chl".

---

## PR3 — Copernicus GlobColour tertiary cross-check (LOW-MEDIUM PRIORITY)

**Wait for PR2 to ship + healthy** before starting this.

**Why it's worth wiring up**: Copernicus GlobColour publishes its own
**`ZSD` (Secchi Transparency Depth)** product directly. That gives a
second-opinion on the model's *central output*, not just on its chl
input. When `viz_predict`'s computed Secchi (chl→a·chl^(-b)→turbidity
corrections) and Copernicus's algorithmic Secchi (Lee et al.
semi-analytical) agree, that's strong evidence the central prediction
is right. When they disagree, the per-zone breakdown points exactly at
which zones' coefficients need calibration.

* **Dataset**: `OCEANCOLOUR_GLO_BGC_L4_NRT_009_102` (NRT, daily
  gap-free)
* **Variables**: `CHL`, `ZSD` (Secchi, m), `KD490`, `RRS412..555`,
  `SPM`
* **Server**: Copernicus Marine Service. **Requires free CMEMS account
  + the `copernicusmarine` Python client** — different auth flow than
  ERDDAP.
* **Coverage**: global 4 km, daily, gap-filled L4
* **Latency**: ~24–48h NRT

**Implementation tactic**: new module
`pipeline/fetch_copernicus.py` rather than shoehorning into
`fetch.py`. Separate auth (CMEMS_USERNAME / CMEMS_PASSWORD env vars),
separate client library. Encode CHL as log10 (matches existing chl)
and Secchi as linear 0..30 m. Outputs:

  * `chl_copernicus_1d.png` — for the priority fallback chain
  * `viz_copernicus_secchi_m.png` — for the Secchi cross-check, NOT
    folded into `viz_predict`. Treat as peer forecaster (Tier 1.5 in
    the validation handoff), used only for diagnostic comparison.

**Gated behind**: `ENABLE_COPERNICUS_CHL=1`. Default off.

**Cost flag**: medium risk — new auth flow, new lib (`pip install
copernicusmarine`), separate diagnostic path. Don't combine with
PR2 in the same change.

Spec: build plan § "PR3 — Copernicus GlobColour tertiary cross-check".

---

## PR4 — Dashboard true-color satellite + SST cross-check overlays (WEB FRONTEND, ~30 LOC)

**Independent of PR1–PR3.** This is web-side UI only — adds three new
toggle layers to the existing layer registry. Zero pipeline changes,
zero ingestion, zero storage. Runs entirely in the browser via WMTS
tile URLs.

**Why**: gives divers a one-tap human-eyeball check of the model
against satellite truth. When ShoudiDive renders a region as Excellent
but the NASA true-color tile shows an obvious green plume, the bug is
visible at a glance.

### Three layers to add

1. **NASA Worldview true color** (MODIS Terra / VIIRS S-NPP / VIIRS
   NOAA-20) — radio-grouped under a "Satellite" toggle, default to
   VIIRS S-NPP (1:30pm Pacific overpass). 250 m resolution. Cap
   `maxZoom=9` to limit tile fetches on 3G.
   - GIBS WMTS endpoint, no auth.
   - `{date}` defaulted to today; fall back to yesterday on 404. Cache
     "latest available date" per sensor for 1 hour.

2. **GOES-18 GeoColor** — single image overlay (not WMTS), 10-minute
   refresh. Catches morning marine layer breakup, mid-day fog, etc.
   that polar orbiters miss.
   - URL: `https://cdn.star.nesdis.noaa.gov/GOES18/ABI/SECTOR/wus/GEOCOLOR/latest.jpg`
   - Bounds: `[[27, -135], [50, -110]]`

3. **OSTIA SST cross-check** — Copernicus WMS, served alongside the
   existing MUR SST (don't replace MUR; add OSTIA as a side-by-side
   toggle).

### Definition of done

* "Satellite" toggle group with 3 radio options renders correctly at
  zoom 3–9
* "Atmosphere" toggle shows GOES-18 with auto-refresh + visible
  "Updated HH:MM" badge
* SST picker has both MUR (default) and OSTIA options; switching is
  instant client-side
* All three credit their source in attribution
* Mobile responsive (folds into existing layer-picker drawer)
* Lighthouse performance unchanged when all three are off

### Implementation tactic

* `src/components/Basemap.jsx` and the existing layer registry get
  three new entries; toggles are styled to match existing buttons
  (don't introduce new patterns)
* `src/lib/dataSource.js` handles date fallback for GIBS layers
* No `pipeline/` change

Spec: dashboard overlays doc (`CLAUDE (9).md`).

---

## NorCal expansion — viz-model fixes (PR-NC-1 .. PR-NC-5)

Five-PR sequence to recalibrate the visibility model now that the
bbox extends to 42°N. Spec + rationale lives in
`outputs/norcal-formula-review.md`. Don't ship without explicit
go-ahead per PR — Michael picks them up sequentially and wants
residuals to settle between rollouts.

Order of operations (from the handoff):
1. PR-NC-1 (zones)             — ship first, additive only
2. PR-NC-2 (coast_normal)      — independent, can ship in parallel with #1
3. PR-NC-5 (Farallons centroid) — right after #1 so norcal_islands works
4. *(wait one week, run validation harness)*
5. PR-NC-4 (wind relaxation)
6. *(wait another week)*
7. PR-NC-3 (SF Bay outflow)    — gated behind `ENABLE_BAY_OUTFLOW=1`
8. PR-NC-6 (tidal currents)    — DEFERRED indefinitely

---

### PR-NC-1 — Add `norcal` lat band ✅ (LANDED 2026-05-10)

**Symptom**: cells north of ~36°N use `central_*` priors that were
calibrated on Monterey kelp + Pt. Conception → Cambria observations.
Reef Check / MBARI Secchi data shows NorCal nearshore systematically
over-predicted on bloom days and Davidson/Pioneer seamount cells
under-predicted on calm relaxation days.

**Root cause**: `LAT_ZONE_BOUNDS` lumped everything 34.45..90 into
`central`. No NorCal zone existed.

**Fix**: split at 36.00°N (Pt. Sur). Added 9 new keys (`norcal_*`)
to all five per-zone dicts. Made `zones.py:classify_zone` generic
so future band additions are config-only.

Files: `pipeline/viz_predict/config.py`,
`pipeline/viz_predict/zones.py`, `pipeline/tests/test_zones.py`.

---

### PR-NC-2 — Per-cell `coast_normal_deg` for upwelling (~5 LOC)

**Symptom**: upwelling anomaly uses a hardcoded coast-normal of 295°.
The CA coast bends substantially (Big Sur ~290°, Monterey ~270°,
Pt. Reyes ~280°). Single scalar is wrong everywhere except SoCal-ish.

**Fix**: `features.py:upwelling_anomaly_5d` already broadcasts when
an array is passed. `predict.py` already passes
`coast_normal_deg_field` (per-cell) for `exposure_index`. Wire the
same array into the upwelling call instead of the 295° scalar.

Files: `pipeline/viz_predict/features.py`,
`pipeline/viz_predict/predict.py`.

Risk: low. No new data dependency.

---

### PR-NC-5 — Add Farallons to `CHANNEL_ISLAND_CENTROIDS` (~5 LOC)

**Symptom**: `nearest_channel_island` returns nothing for cells north
of San Miguel because the centroids dict is hardcoded SoCal-only.
PR-NC-1's `norcal_islands` zone has no NorCal islands to match
against without this.

**Fix**: add south_farallon, north_farallon, maintop, ano_nuevo to
`CHANNEL_ISLAND_CENTROIDS`. All four use `"open"` for current-regime
side (the east/west labels are SoCal-bight-specific).

Files: `pipeline/viz_predict/config.py`.

Dependencies: PR-NC-1 (so `norcal_islands` exists).

---

### PR-NC-4 — Wind-relaxation feature (~15 LOC)

**Symptom**: NorCal vis spikes are driven by wind RELAXATION events
(sustained NW upwelling-favorable wind followed by 1-2 days of calm).
The model only sees a 5-day mean alongshore anomaly which can't
distinguish a sustained pattern from a relaxation pulse.

**Fix**: new `wind_relaxation_index_5d` feature in `features.py`.
Compares last-2-day alongshore wind against days -5..-2 mean,
returns tanh(positive_relax / 4 m/s). Coefficient zero everywhere
except `norcal_nearshore` (-0.20) and `norcal_islands` (-0.15) so
SoCal residuals don't move.

Files: `pipeline/viz_predict/features.py`,
`pipeline/viz_predict/config.py` (add `wind_relax` to
DriverCoefficients), `pipeline/viz_predict/model.py` (add term to
`driver_adjustment`).

Dependencies: PR-NC-1.

---

### PR-NC-3 — SF Bay outflow as a synthetic river (~30 LOC + new fetch)

**Symptom**: cells near the Golden Gate (37.81°N) don't see the
Sacramento + San Joaquin discharge — largest plume on the West
Coast (5,000 cfs baseline → 250,000+ cfs after big atmospheric
rivers). `fetch_rivers.py` doesn't include SF Bay because it's
an estuary, not a USGS river-mouth gauge.

**Fix**: add synthetic river `sf_bay_outflow` at (37.81, -122.48).
Fetcher pulls CDEC Dayflow `OUT` value (station `DTO`, sensor 23,
daily). Per-river e-folding distance: SF Bay = 20 km (its plume
genuinely extends ~20-30 km on big outflow days); USGS rivers stay
at 5/8 km defaults.

Gated behind `ENABLE_BAY_OUTFLOW=1` env var so it can A/B before
becoming default.

Files: `pipeline/fetch_rivers.py`, `pipeline/viz_predict/features.py`
(or new `bay_outflow_index` if cleaner), `pipeline/viz_predict/config.py`,
`pipeline/tests/test_features.py`.

Risk: medium — new external API (CDEC). Need graceful fallback to
climatology (~10,000 cfs mean Bay outflow) on HTTP failure.

Dependencies: PR-NC-1.

---

### PR-NC-6 (DEFERRED) — Tidal currents

Spec'd in `outputs/norcal-formula-review.md` § 3.3. Punted until
~3 months of NorCal observations accumulate against the new zones.
Narrow benefit (Golden Gate, Tomales, etc.) at high implementation
cost. New external API (NOAA Tidal Current Predictions). Wait until
PR-NC-1..5 residuals stabilize.

---

### Validation harness (cross-PR)

`pipeline/validation/norcal_residuals.py` (new file): pulls Reef
Check + MBARI Secchi obs for cells north of 36°N, runs them through
the model with the new zones, writes a residuals plot.

Acceptable: `viz_p50_ft` within ±5 ft of observed Secchi on 80% of
NorCal observations. Below 80% means PR-NC-1 priors need calibration.

Run after every PR in the chain. Don't promote PR-NC-1 to default
until the harness passes against at least 20 NorCal observations.

**Data-source playbook lives in `docs/norcal-vis-validation-sources.md`.**
Tiered checklist of CeNCOOS / MBARI / BAUE / Reef Check / ScubaBoard
sources to populate `pipeline/validation/data/norcal_observations.csv`
(the input file `norcal_residuals.py` reads). Tier 1 + 2 are the
fastest path to a useful first validation pass (CeNCOOS + BAUE),
Tier 3 is the long-tail backfill (ScubaBoard scraping), Tier 4 is
the gold-standard agency datasets (Reef Check). Don't start any of
this work until PR-NC-1 lands on main; this is queued, not active.

---

## Multi-region expansion — PNW + Florida/Caribbean

Full scoping in `docs/expansion-regions.md`. Decisions locked:
both regions in parallel, single app with a Region switcher, full
feature parity (all 6 layers + predicted vis), saltwater only for
FL v1. Companion PR-level handoffs (`pnw-v1-handoff.md`,
`tropical-v1-handoff.md`) to be filed after the scoping doc lands.

### PR-X-1 — Region-aware pipeline scaffold ✅ (LANDED ON DEV 2026-05-11)

`pipeline/regions/` package with a `Region` dataclass, `get_region(name)`
accessor, and three configs:
  * `ca.py` — snapshot of today's hardcoded behavior
  * `pnw.py` — skeleton (bbox + lat bands only)
  * `tropical.py` — skeleton (sub-region bboxes + viz variant marker)

Additive only — no running code imports from this yet. The drift
test in `pipeline/tests/test_regions.py` gates against the CA
snapshot diverging from `fetch.py` / `viz_predict/config.py` until
PR-X-2 wires the migration.

### PR-X-2 — Migrate pipeline fetchers to `regions/` (~150 LOC)

Replace the hardcoded `BBOX = dict(...)` constant in every
`pipeline/fetch*.py` + `pipeline/chl_blend.py` with
`BBOX = get_region(args.region or "ca").bbox`. Add a `--region`
CLI flag to each script (default `ca`). Update
`pipeline/fetch.py`'s manifest-write to land in
`public/data/<region>/manifest.json` instead of the top-level path
so CA / PNW / tropical can coexist. No frontend changes yet — the
CA bundle still serves from `public/data/` for backward compat
during the transition.

### PR-X-3 — CI matrix for multi-region refresh (~80 LOC)

`refresh-data.yml` gets a matrix:
```yaml
strategy:
  matrix:
    region: [ca, pnw, tropical]
```
Per-region jobs run in parallel. The deploy step still only fires
for `ca` until the frontend can route between regions (PR-FE-1).
PNW + tropical jobs are allowed to fail without blocking the CA
deploy (`continue-on-error: true` until the data sources are
proven stable).

### PR-FE-1 — Region switcher in frontend (~120 LOC)

`src/components/RegionSwitcher.jsx` chip in the top bar; reads /
writes `?region=` URL param + `localStorage.lastRegion`. `src/lib/
dataSource.js` (new) routes fetches to `/data/<region>/...`.

Open question per scoping doc: default region. Options are
geo-IP, last-used, always CA. Decision needed before this PR
starts.

### PNW v1 series (PR-PNW-1..4)

Scoping in `docs/expansion-regions.md` § 2. Tracked as four PRs:

  * **PR-PNW-1** — PNW bbox + zone family + spot pins + Salish Sea
    polygon (the `wa_inland` polygon zone).
  * **PR-PNW-2** — SSCOFS THREDDS/OPeNDAP fetcher for Salish Sea
    currents. The big new fetcher in this region.
  * **PR-PNW-3** — `pnw_inland` viz_predict variant (Option A from
    the doc — chl coefficient near zero, river coefficient high,
    stratification term new).
  * **PR-PNW-4** — Olympic Coast NMS + WA DNR Aquatic Reserves +
    OR Marine Reserves polygons.

Estimated total: 6–8 PRs once edge cases are itemised.

### Tropical v1 series (PR-TROP-1..7)

Scoping in `docs/expansion-regions.md` § 3. Tracked as seven PRs:

  * **PR-TROP-1** — Two sub-region bboxes (`gulf_se` + `caribbean`)
    + lat+lng zone classification + spot pin set.
  * **PR-TROP-2** — HYCOM Gulf 1/25° + Global RTOFS 1/12°
    currents fetcher.
  * **PR-TROP-3** — NASA GEOS-FP Saharan dust fetcher + UI chip.
    Open question: chip on the carousel, or feature-only input?
  * **PR-TROP-4** — NOAA NHC hurricane track overlay + 5-day cone.
    Open question: soft warning or hard cutoff during active
    advisories?
  * **PR-TROP-5** — New `subtractive_tropical` viz model. The
    biggest single piece of new work in the whole expansion.
    `secchi_m = base_vis - swell - plume - dust - rain - hurricane`.
  * **PR-TROP-6** — International MPA polygons (WDPA + NMS).
  * **PR-TROP-7** — Spot-pin curation pass (Caribbean operator
    outreach for ground truth).

Estimated total: 10–14 PRs.

### Open questions blocking PR-FE-1 + tropical work

From `docs/expansion-regions.md` § 6 — none of these block the
PR-X scaffold series but they DO block specific later PRs. Surface
them now so the answers can roll in async:

1. Region menu wording ("PNW" vs "OR + WA"; "Caribbean" vs
   "FL + Caribbean") — blocks PR-FE-1.
2. Default region behavior (geo-IP / last-used / always CA) —
   blocks PR-FE-1.
3. Saharan dust as a chip vs. feature-only input — blocks PR-TROP-3.
4. Hurricane advisory mode (soft banner vs. hard cutoff) —
   blocks PR-TROP-4.
5. Caribbean operator partnerships for validation data —
   accelerates PR-TROP-7 if pursued.
6. Springs back-burner (confirming freshwater is out of v1, but
   the config layer must allow a future `freshwater` sibling).
7. Branding update (single tagline vs. per-region SEO taglines).

---

## Queue policy

- Items are picked up in order. PR1 → PR2 → PR3, AND/OR PR4 in parallel.
- PR4 is the only item that touches `src/` (web frontend); PR1–PR3 are
  pipeline-only. They can run on different agents simultaneously.
- Each item has its own spec; the agent doesn't reinterpret the
  design decisions from these handoffs at execution time, only the
  implementation tactics.
- "Add to queue" from the user means: read, file here, don't execute.
  "Pull next from queue" means: pick the topmost unstarted item.
- The mobile app work is tracked separately in `mobile/HANDOFF.md`
  (handed off to a different agent / Codex). Don't mix mobile work
  with pipeline / dashboard work.
