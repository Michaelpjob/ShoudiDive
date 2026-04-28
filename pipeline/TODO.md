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
