# NorCal expansion — full scoping

**Goal:** extend shouldidive.com from current bbox `lat ∈ [31.8°, 37.6°]`
(SoCal + Bay Area edge) to `lat ∈ [31.8°, 42.0°]` — full California
coast, Coronado Islands → Oregon border.

**Lng range was also cropped** during the visual review:
`lng ∈ [-124.0°, -116.8°]` → `[-124.0°, -117.5°]`. The original eastern
bound was filling the right side of the map with inland CA (Sacramento
Valley, Inland Empire, Riverside) — irrelevant for a dive forecast.
`-117.5°` keeps the SoCal coast (LA, La Jolla, San Diego harbor),
Catalina, and the Coronado Islands; drops Tijuana-east and the inland
LA basin.

That's a **+4.4° lat extension** = ~310 nm = a 76% increase in N-S
coverage, **with a -0.7° lng tightening** that cuts ~10% of the
lng range. Net grid growth is ~60% larger area.

## TL;DR effort estimate

| Phase | Scope | Effort | Risk |
|-------|-------|--------|------|
| **1. Mechanical bbox bump** | BBOX in 15 files; uncomment 3 NorCal NDBC buoys; add ~10 NorCal place labels | **~1 day** | Low — file-search-and-replace, well-typed |
| **2. NorCal data sources** | Tides (Crescent City, Humboldt Bay), rivers (Russian, Eel, Klamath, Smith), CDIP NorCal buoys | ~2 days | Low — extend existing fetcher configs |
| **3. NorCal ground-truth scrapers** | Bay Area + Mendocino + Humboldt dive shops, NorCal-specific Reddit subs | ~3-5 days | Medium — most NorCal dive shops are aggregators per audit; webcam-focused signals dominate |
| **4. Visibility-model recalibration** | Re-run regression guard against NorCal observations once data accumulates; adjust per-zone coefficients | **~30 days** wait + 1 day work | Medium — model trained on SoCal patterns may have systematic NorCal bias |
| **5. UX polish** | Extend hand-drawn COASTLINE polyline, region selector in saved spots, geolocation-aware default zoom | ~2-3 days | Low |

**Total active engineering: ~10 days. Total wall-clock to confidence: ~6 weeks** (gated by the ~30 days of data accumulation in step 4).

Phase 1 ships TODAY in this PR; Phases 2-5 are follow-ups.

---

## What changes

### 1. Pipeline (Python) — 14 files touch BBOX

```
pipeline/fetch.py                     BBOX dict
pipeline/fetch_bathy.py               BBOX dict
pipeline/fetch_climatology.py         BBOX dict
pipeline/fetch_coastline.py           BBOX dict
pipeline/fetch_currents.py            BBOX dict
pipeline/fetch_mpa.py                 BBOX dict
pipeline/fetch_precip.py              BBOX dict
pipeline/fetch_sst_5day.py            BBOX dict
pipeline/fetch_swell_5day.py          BBOX dict
pipeline/fetch_visibility.py          BBOX dict
pipeline/fetch_waves.py               BBOX dict
pipeline/fetch_wind.py                BBOX dict
pipeline/fetch_wind_5day.py           BBOX dict
pipeline/chl_blend.py                 BBOX dict
```

**Architectural finding:** Every fetcher hardcodes its own BBOX dict
instead of importing from a shared module. A bbox change is therefore
a 14-file diff. **Recommended Phase 6 (post-NorCal):** extract a single
`pipeline/lib/bbox.py` and have everyone import from it. Today's
expansion is a useful forcing function for that refactor — but doing
both at once doubles the risk surface, so we ship the bump now and the
DRY refactor as a follow-up.

**Per-fetcher data-source coverage check (all green for NorCal):**

| Layer | Source | NorCal coverage |
|-------|--------|-----------------|
| SST | GHRSST MUR L4 (jplMURSST41) | Global 1km — ✅ |
| chl-a | NOAA CoastWatch DINEOF VIIRS | Global 4km — ✅ |
| kd490 | NOAA CoastWatch DINEOF | Global 2km — ✅ |
| Wind (now) | NOAA HRRR | CONUS ~3km — ✅ (HRRR domain extends to ~50°N) |
| Wind (5d) | NOAA GFS | Global ~25km — ✅ |
| Swell (5d) | NOAA gfswave | Global — ✅ |
| Surface currents | HFR | **⚠️ SF→OR coverage thins** — see below |
| Tides | NOAA CO-OPS | Per-station — needs new station IDs |
| Rivers | USGS NWIS | Per-station — needs new station IDs |
| Bathy | GMRT | Global — ✅ |
| Coastline | Natural Earth via fetch_coastline.py | Global — ✅ |
| MPAs | CA DFW (state) | CA-wide — ✅ |
| Climatology | ERDDAP MUR climo | Global — ✅, but climo cache needs flush on next month rollover |

**HFR currents caveat:** HFR (high-frequency radar) station network is
densest in SoCal + SF Bay. Stations get sparse from Pt Arena to Cape
Mendocino. Coverage gaps are common in winter. The `fetch_currents.py`
fallback (climo + tide + wind blend) handles this, but accuracy
degrades. Document explicitly in the layer's UI tooltip if expanding.

### 2. Frontend (React) — 1 file touches BBOX

```
src/lib/mapData.js                    BBOX const, COASTLINE polyline
src/components/Basemap.jsx            PLACE_LABELS array (NorCal additions)
```

Frontend-side, the bbox is exported once from `mapData.js` and every
projection helper keys off it. Changing `latMax: 42.0` propagates
automatically through `project()` / `unproject()` / `getFitted()`.

**Aspect ratio shift:** the bbox aspect changes from 5.8°×7.2° (~0.81)
to 10.2°×7.2° (~1.42). The map becomes taller-than-wide. Need to
re-tune the fitted-rectangle math + re-eyeball the desktop layout
(spots panel + timeline at bottom may collide with the new map height).

**COASTLINE polyline (`mapData.js` line 61–124):** hand-drawn coast
polyline runs from current top (`(-122.05, 37.50)`) southward. Used
ONLY by `chlAt(lng, lat)` mock-data synthesis (not for visual
rendering). Phase 1 doesn't need to extend this — the live coastline
visual comes from `public/data/land.geojson` produced by
`fetch_coastline.py`, which auto-extends with the bbox. The hand-drawn
polyline can be extended as a polish item in Phase 5, OR the mock-data
function can be retired entirely (the live `getSST(lng, lat)` is the
real path now).

**PLACE_LABELS (`Basemap.jsx` line 70–84):** today's top label is
Monterey Bay (36.78°N). NorCal cities to add (≥36.78°N):

| City | lng | lat | priority |
|------|----:|----:|:--------:|
| San Francisco | -122.42 | 37.77 | 7 |
| Oakland / Bay Area | -122.27 | 37.80 | 5 |
| Half Moon Bay | -122.43 | 37.46 | 4 |
| Santa Cruz | -122.03 | 36.97 | 5 |
| Pt Reyes | -123.00 | 38.02 | 5 |
| Bodega Bay | -123.05 | 38.33 | 4 |
| Mendocino | -123.80 | 39.30 | 5 |
| Fort Bragg | -123.81 | 39.45 | 4 |
| Cape Mendocino | -124.41 | 40.44 | 5 |
| Eureka / Humboldt Bay | -124.16 | 40.81 | 6 |
| Crescent City | -124.20 | 41.76 | 5 |

Plus regional water-area labels: "GULF OF THE FARALLONES", "POINT
ARENA SHELF", "MENDOCINO RIDGE".

### 3. Validation / ground-truth

**NorCal NDBC buoys (already in `pipeline/validation/ingest/ndbc.py`,
just commented out):**

```python
# {"stn": "46013", "name": "Bodega Bay",         "lat": 38.235, "lng": -123.317},
# {"stn": "46014", "name": "Pt Arena",           "lat": 39.225, "lng": -123.980},
# {"stn": "46026", "name": "San Francisco",      "lat": 37.750, "lng": -122.838},
```

Phase 1 just uncomments these. Adds 3 SST + swell stations
immediately.

**NorCal CDIP buoys to add to `cdip.py`:**

| Station | Name | Lat | Lng |
|---------|------|----:|----:|
| 029 | San Francisco Bar | 37.78 | -122.63 |
| 094 | Pt Reyes Outer | 37.97 | -123.47 |
| 142 | Pt Sur | 36.34 | -122.10 |
| 138 | Half Moon Bay | 37.50 | -122.50 |
| 168 | Eureka | 40.93 | -124.55 |

(Verify station IDs are still active before adding — CDIP
occasionally deprecates buoys.)

**NorCal dive shops — per the user's source manifest audit
(2026-05-09, in `pipeline/validation/ingest/CANDIDATES.md`):**

The dive-shop scrapers in NorCal have **already been audited**. Verdict:
- Aquarius Dive Shop (Monterey) — pure aggregator. ❌
- Diver Dan's — pure aggregator. ❌
- Spanglers' Scuba — webcam-only. Future image-analysis project. ⏸
- Monterey Scuba Board — static educational page. ❌
- Channel Islands Dive Adventures /coastal-dive-sites-avila-big-sur — static.
  ❌
- SLO Ocean Currents, SLO Divers — weekly cadence only.

**Realistic NorCal viz signal sources (Phase 3):**

- **Monterey County Dive Reports (Facebook group)** — highly active per
  the source manifest. Facebook scraping is brittle; defer.
- **Spangler's webcams** — depends on the image-analysis pipeline
  (separate Phase 6 project).
- **r/scuba + r/spearfishing CA-keyword filter** — already covered.
- **bdoutdoors central-CA fishing RSS** — already covered.
- **California Diver Magazine, DAN Alert Diver** — magazine, irregular.

**The honest answer:** NorCal text-scraped viz signal is going to be
thin. The structured daily-report dive shops are a SoCal phenomenon
(LA-OC density of paying divers + ocean access). NorCal will rely
heavily on the buoy network (SST + swell only — NO secchi) plus
forum/Reddit chatter. Closing the viz-data gap meaningfully in NorCal
likely requires the webcam image-analysis project (in
`CANDIDATES.md`).

### 4. Visibility-model recalibration

`pipeline/viz_predict/config.py` carries per-zone coefficients in
`LAT_LABELS` × `DIST_LABELS`. The zones today only span SoCal lats:

```python
LAT_LABELS = ["bight_nearshore", "bight_offshore", "channel_islands"]
```

NorCal needs new zones:

| Zone | Lat range | Notes |
|------|-----------|-------|
| `central_coast` | 35.0°–37.6° | Big Sur to SF Bay; existing data partially covers |
| `gulf_farallones` | 37.6°–38.5° | SF Bay outflow, upwelling-dominated |
| `mendocino_coast` | 38.5°–40.5° | Pt Arena → Cape Mendocino; biggest swell, coldest water |
| `humboldt_north` | 40.5°–42.0° | Eureka → OR border; turbid coastal |

Each zone needs its own (in viz_predict.config):
- `PERSISTENCE_TAU_DAYS` — how fast SST anomaly decays
- `SIGMA_SST_BY_LEAD` — forecast uncertainty
- viz coefficients — kelp forest viz baseline differs (NorCal has
  bigger kelp + bigger swell + colder water + more upwelling)

**Recalibration path:**
1. Start with SoCal coefficients copied as defaults for NorCal zones.
2. Run for ~30 days collecting NorCal observations.
3. `pipeline/validation/check_regression.py` activates per-zone once
   `n ≥ 30` for that zone.
4. If RMSE jumps >20%, run `--promote-baseline` after manual review.

The regression guard catches systematic bias, but only AFTER 30 obs
land. Until then, NorCal viz predictions are **explicitly preliminary**
and the UI should label them so.

### 5. CI / infrastructure changes

- **Workflow timeouts:** today's per-step timeout is 5-8 min. NorCal
  ~doubles fetch volume. SST/chl are fast (subset of global product).
  Wind5d hourly: 25 buckets × bigger grid. Total wall-clock impact is
  +30-90s per cron run. Stays well within budget.
- **PNG storage:** each layer's PNG grows ~75% (rows 117 → 205).
  `public/data/` directory grows by ~5-10 MB. Cloudflare Pages free
  tier: fine.
- **Manifest schema:** the `bbox` field in the published manifest
  changes. The `manifest-validate` job's contract assertion on bbox
  shape (4 floats) doesn't pin specific values — no test changes
  needed.

### 6. Test changes

| Test | Effect |
|------|--------|
| `tests/checkpoints/data-shape.test.js` | None — asserts shape, not values |
| `tests/dataFeatureContracts.mjs` | None |
| `tests/appFeatureContracts.test.js` | None — pins App.jsx JSX, not bbox |
| `tests/checkpoints/rendering-math.test.js` | **Re-eyeball** — projection round-trip tests use synthetic coords; should still pass with new bbox but worth confirming |
| `tests/visual-paint.mjs` | Visual baselines change — paint thresholds may need re-tune. Likely the dominant test-side risk. |
| `tests/live-checkpoints/live-runtime.mjs` | Asserts saved-spots populate; NorCal map default may not show San Diego spots → might need to re-pick the default-active-spot |

### 7. Open architecture decisions for the user

These need a call before Phase 2-5 work begins:

1. **Default zoom / map framing.** The map currently centers on SoCal
   (about 33.5°N). With CA-wide bbox, default could be:
   - (a) Stay zoomed on SoCal (where most users are today), let users
     pan/zoom to NorCal. Loses NorCal first-impression discoverability.
   - (b) Fit-to-bbox on load — show full coast. Lose SoCal detail.
   - (c) **Geolocation-aware** — center on user's region (browser
     `navigator.geolocation.getCurrentPosition`, with permission). Best
     UX, modest complexity (~1 day).

   **Recommendation: (c).** Falls back to (b) if user denies geolocation.

2. **Saved spots panel.** Currently 10 SoCal spots. NorCal additions
   would benefit from a region selector:
   - SoCal (current default)
   - Central Coast
   - Bay Area
   - North Coast

3. **Per-zone confidence labeling.** When viz-predict isn't yet
   calibrated for a zone (n < 30 obs), the UI should explicitly mark
   its predictions as "calibrating" or similar, not show them at the
   same confidence as SoCal.

4. **Beta cohort.** Roll the NorCal expansion behind a feature flag
   `?norcal=1` URL param OR a localStorage opt-in for a beta cohort?
   Lets you collect feedback from a few divers before flipping the
   default. Cheap to implement (~30 min). High signal.

---

## Phase 1 (this PR) — what's actually in the diff

Mechanical changes only. Lowest-risk subset that gets NorCal data
flowing into the pipeline + visible on the map.

- ✅ BBOX bumped from `lat_max=37.6` to `lat_max=42.0` in 15 files
  (14 pipeline + 1 frontend)
- ✅ NorCal NDBC buoys uncommented (Bodega Bay, Pt Arena, San Francisco)
- ✅ NorCal PLACE_LABELS added (~10 cities)
- ✅ This scoping doc

**NOT in this PR (deferred to follow-ups):**

- COASTLINE polyline extension (used only by mock chl synthesizer; lower priority)
- NorCal CDIP buoy adds (requires station-ID verification)
- NorCal tide stations
- NorCal river stations
- New viz-predict zones (recalibration depends on 30 days of obs)
- NorCal dive-shop scrapers (most are aggregators; needs Phase 3 review)
- Geolocation-aware default zoom
- Saved-spots region selector

## How to verify Phase 1 on dev preview

After this PR lands on dev:

1. Visit `https://dev.shouldidive.pages.dev` (or the PR's preview URL)
2. The map should now show coast all the way up to the OR border
3. Layer chips render — SST/chl/wind/swell/viz all extend visually to NorCal
4. Saved-spots panel still shows SoCal spots (correct — adding NorCal
   spots is Phase 2)
5. Hover over a NorCal coordinate — tooltip shows real values, not "no data"
6. Watch the next refresh-data cron (or manually trigger
   `gh workflow run refresh-data.yml`) — NorCal PNGs land in `public/data/`

If anything looks off (no data over NorCal, map aspect distorted,
errors in console), the most likely culprit is one of the 15 BBOX
sites I missed in the search-and-replace. Open a Cloudflare Pages
Real-time Logs tab and watch for errors during the next visit.
