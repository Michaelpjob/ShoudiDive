# ShouldIDive — region expansion strategy (PNW + Gulf/Caribbean)

Scoping doc for the two-region expansion. Decisions locked: both regions in parallel, single app with a Region switcher, full feature parity (all 6 layers + predicted vis), saltwater only for FL v1.

Companion docs to come (after this one is approved): `pnw-v1-handoff.md`, `tropical-v1-handoff.md` with PR-level specs in the same style as `norcal-coding-handoff.md`.

---

## 1. Architecture — making the pipeline region-aware

The existing pipeline is implicitly CA-only: bbox constants are hardcoded, `pipeline/viz_predict/zones.py` knows three lat bands all positioned for the bight, and every fetch script writes to `public/data/<layer>/...` without region scoping. Expansion requires a config layer that lets the same code run three times, once per region.

**Proposed structure**:

```
pipeline/
├── regions/
│   ├── __init__.py
│   ├── ca.py            # existing — bbox, zones, sources, spot pins
│   ├── pnw.py           # NEW
│   └── tropical.py      # NEW — gulf + east FL + Caribbean
├── fetch.py             # takes --region={ca,pnw,tropical}
├── fetch_visibility.py  # takes --region=...
├── viz_predict/
│   ├── config.py        # imports per-region overrides
│   ├── zones.py         # already generic, no change
│   └── ...
└── ...

public/data/
├── ca/                  # existing data tree, unchanged
├── pnw/
└── tropical/

src/
├── lib/
│   ├── region.js        # NEW — selected region state, URL routing
│   └── dataSource.js    # NEW — switches base path by region
├── components/
│   └── RegionSwitcher.jsx  # NEW — chip in app bar
└── ...
```

Each `regions/<name>.py` exports a dataclass with: `bbox`, `lat_zone_bounds`, `coast_normal_field_path`, source URLs (HRRR is the same everywhere, but currents/bathy differ), spot-pin coordinates, MPA polygons, viz-model variant. Existing code reads `from pipeline.regions import active_region` to get the right config.

**Frontend**: a Region menu in the top bar (CA / PNW / FL+Caribbean) sets a URL param (`?region=pnw`) and the app fetches from `/data/pnw/...`. State persists in localStorage so users default to their last-used region.

**Deployment**: still one Cloudflare Pages site. The daily refresh-data workflow now fans out — three matrix jobs, one per region. ~3× the data volume in git history (~250 KB/day instead of 85 KB) which is fine.

**Watchdog**: `check_feeds.py` and `check_published.py` already exist. Wrap each in a per-region loop; failures get tagged with region name in the GitHub issue.

---

## 2. Pacific Northwest (OR + WA) — v1 plan

**Bbox**: lat 42.0–49.0°N, lng −127.0 to −122.0°W. Covers Oregon coast, Washington outer coast, the entire Salish Sea (Puget Sound + San Juans + Strait of Juan de Fuca + Strait of Georgia southern end). Cuts off at the Canadian border but includes the BC straits because the oceanography spills over.

**Sub-region geography** to encode in zones:
1. **OR outer coast** (Coos Bay → Astoria) — open Pacific, big swell, similar dynamics to NorCal but cooler and with the Columbia River plume dominating northward.
2. **WA outer coast** (Astoria → Cape Flattery) — Olympic Coast National Marine Sanctuary, very limited dive access, mostly remote.
3. **Strait of Juan de Fuca** — tidal currents 2–4 kt, Neah Bay to Port Angeles.
4. **San Juan Islands** — sheltered, current-dominated, world-class drysuit diving.
5. **Puget Sound proper** (Central + Southern) — Hood Canal, Edmonds Underwater Park, Seacrest, Three Tree Point. Stratified water column, freshwater lens on top during winter rain.
6. **Hood Canal** — fjord, low O2 in summer, distinct.

**Proposed zone family** (replaces `bight/transition/central` with a different breakdown):

```python
LAT_ZONE_BOUNDS_PNW = {
    "wa_inland": ...,   # Salish Sea waters (defined by water-body polygon, not lat alone)
    "wa_outer":  (46.30, 49.00),
    "or_north":  (44.00, 46.30),
    "or_south":  (42.00, 44.00),
}
```

The `wa_inland` zone is the tricky one — it's not a lat band, it's a *polygon* (the Salish Sea complex). Easiest implementation: per-pixel "is_inland_pnw" mask from a coarse polygon, evaluated before the lat-band classifier.

**Per-layer data sources**:

| Layer | Source | Same as CA? | Notes |
|---|---|---|---|
| Sea Temp | GHRSST MUR L4 (`jplMURSST41`) | ✓ same | Global product, just expand bbox query. |
| Chlorophyll | NASA OB.DAAC multi-source (AQUA-MODIS, VIIRS, OLCI) | ✓ same | Confirmed coverage at 49°N. DINEOF gap-fill works at this latitude. |
| Wind | NOAA HRRR + GFS via NOMADS | ✓ same | HRRR covers CONUS fully; PNW is in domain. |
| Swell | NOAA WW3 (`gfswave`) | ✓ same | Global product. |
| Currents (outer coast) | IOOS HFRNet | ⚠️ degraded | **HFR coverage in PNW is sparse** (NOAA confirms "little radar coverage along most of the Oregon and Washington coasts"). The model-inference fallback in `fetch_currents.py` (wind + tides + lunar) becomes the *primary* path for most of the PNW outer coast, not a fallback. |
| Currents (Salish Sea) | **NOAA SSCOFS** | ✗ NEW | Salish Sea + Columbia River Operational Forecast System. FVCOM-based, 72-hr horizon, 4×/day. Nine subdomains: San Juan Islands, Whidbey Basin, Central PS, Southern PS, Strait of Juan de Fuca, Columbia River Bar, etc. Free, no auth. Pulls via THREDDS/OPeNDAP. **This is the big new fetcher**. |
| Bathymetry | GMRT (already integrated) | ✓ same | Global, just expand bbox. |
| Tides | NOAA CO-OPS (already integrated) | ✓ same | Just more stations in the query. |
| MPAs | Olympic Coast NMS + WA DNR Aquatic Reserves + OR Marine Reserves | ✗ NEW | Pull polygons from NOAA SanctView and state agencies. |

**Predicted-vis model adaptation**: the chl→Secchi math works for the *outer coast* (similar to NorCal — productive upwelling water). For the Salish Sea it doesn't — the dominant clarity drivers are river outflow (Fraser, Skagit, Cedar, Puyallup) and tidal stratification, not chl. Two options:

- **Option A — extend `viz_predict` with a `pnw_inland` zone family**: chl coefficient near zero, river coefficient high, stratification term new. This is the lift-and-shift approach.
- **Option B — separate `viz_predict_inland` module** for the Salish Sea entirely. Cleaner long-term but more code.

Recommend Option A for v1: faster, lets you validate against actual data before committing to a separate model.

**Spot pins (initial set, ~25)**:

OR: Sunset Bay, Simpson Reef, Otter Rock, Yaquina Head, Boiler Bay, Cape Kiwanda, Oceanside (Three Arch).
WA outer: Cape Flattery, Tatoosh, Neah Bay, La Push, Hole-in-the-Wall.
Strait: Salt Creek, Tongue Point.
San Juans: Lime Kiln, Salmon Bank, Turn Island, Pile Point.
Central PS: Edmonds UWP, Seacrest, Three Tree Point, Saltwater State Park.
Southern PS: Sunrise Beach, Day Island Wall.
Hood Canal: Sund Rock, Octopus Hole, Pulali Point.

**Risks specific to PNW**:
1. HFR coverage gap means currents are model-inferred for ~80% of the bbox. The beta disclaimer copy already exists in CA — re-use it more aggressively.
2. Salish Sea is fundamentally different oceanography. The viz model needs validation against PNW-specific data sources (NWUPS reports, NW Dive News, Edmonds dive club logs) before you trust it.
3. SSCOFS THREDDS server has historically had downtime; build the same per-feed health-check that `check_feeds.py` does for the CA sources.

**Estimated PR count for PNW v1**: 6–8 PRs.

---

## 3. Florida + Texas + Caribbean (saltwater only) — v1 plan

This is harder than PNW because **the chl-to-Secchi math doesn't work in tropical water**. The Caribbean is oligotrophic — chl < 0.1 mg/m³ almost everywhere — so the existing `secchi = a · chl^(-b)` formula predicts 100+ ft vis essentially everywhere, which is mostly right but doesn't capture what actually drives a "bad day" in the tropics: Sahara dust events, hurricane stirring, river plumes, and localized rainfall runoff.

**Bbox(es) — propose splitting into two sub-regions** for tractability:

| Sub-region | Bbox | Includes |
|---|---|---|
| `gulf_se` | 18–31°N, −98 to −80°W | TX coast, FL Gulf Coast, FL Keys, Flower Garden Banks NMS, western Cuba, Yucatan |
| `caribbean` | 10–24°N, −85 to −60°W | Bahamas, Cayman, Cuba, Jamaica, Hispaniola, PR, USVI, BVI, Bonaire, Lesser Antilles, Trinidad |

These can be a single `tropical` region in the frontend (one Region menu item) but two separate fetch jobs in the pipeline for data-volume reasons.

**Sub-region geography**:

1. **TX Gulf Coast** — flat, muddy, vis rarely above 40 ft. Flower Garden Banks (offshore, 100 mi out) is the gem — 100 ft vis routine.
2. **FL Gulf Coast** — sandy bottom, hurricane-driven, springs flow into coastal water in the Big Bend area.
3. **FL Keys** — coral reef tract, vis 30–80 ft typical, hugely weather-driven.
4. **FL east coast** (Palm Beach → Jupiter → Pompano → Boynton) — Gulf Stream proximity, deep wrecks, vis often 80+ ft.
5. **Bahamas Bank** — shallow gin-clear sand flats, vis often 100+ ft when calm.
6. **Caribbean reefs** — vis driven by Saharan Air Layer (SAL) intrusions, hurricane season, and river plumes from major mainland rivers (Magdalena in Colombia, Orinoco in Venezuela).

**Proposed zone family**:

```python
LAT_ZONE_BOUNDS_TROPICAL = {
    "carib_lesser":   (10.0, 18.0),     # Lesser Antilles + Trinidad
    "carib_greater":  (18.0, 24.0),     # Greater Antilles + Bahamas
    "fl_keys":        (24.0, 25.5),     # Keys-specific (different reef regime)
    "fl_east":        (25.5, 31.0),     # Palm Beach northward
    "gulf_fl":        (24.0, 31.0),     # FL Gulf Coast (longitude-gated, not just lat)
    "gulf_tx":        (24.0, 31.0),     # TX coast (longitude-gated)
}
```

Lat alone doesn't classify here — `gulf_fl` and `fl_east` overlap in latitude, so the classifier needs a longitude gate too. Build the zone fn to take both lat and lng as inputs (already does — `nearest_channel_island` does this).

**Per-layer data sources**:

| Layer | Source | Same as CA? | Notes |
|---|---|---|---|
| Sea Temp | GHRSST MUR L4 | ✓ same | Global. |
| Chlorophyll | NASA OB.DAAC multi-source | ✓ same | Always low in tropics, but you still need it for plume detection. |
| Wind | NOAA HRRR (CONUS portion only, FL east + Gulf) + GFS (Caribbean) | ⚠️ partial | HRRR doesn't cover the Caribbean Sea south of ~22°N. Fall back to GFS for the Caribbean Sea region. |
| Swell | NOAA WW3 | ✓ same | Global. Hurricane spin-up captured. |
| Currents | **HYCOM + Global RTOFS** | ✗ NEW | HFRNet has spotty FL coverage (SECOORA runs some), zero Caribbean coverage. Use HYCOM 1/25° Gulf model + Global RTOFS 1/12° for Caribbean. Both free via ERDDAP and NOMADS. |
| Bathymetry | GMRT | ✓ same | Global. |
| Tides | NOAA CO-OPS (FL/TX) + IOC stations (Caribbean) | ⚠️ partial | NOAA covers US territory. Caribbean island stations come from UNESCO IOC sea-level network. |
| MPAs | Florida Keys NMS, Flower Garden Banks NMS, plus per-country MPAs | ✗ NEW | A real headache. Each Caribbean nation has its own MPA registry. Start with Protected Planet's WDPA download for global coverage. |
| **Saharan Dust** | **NASA GEOS-FP aerosol forecast** | ✗ NEW LAYER | 10-day forecast of aerosol optical thickness (AOT). Becomes a 7th layer chip in this region (or feeds the viz model only, depending on UX call). |
| **Hurricane track** | NOAA NHC | ✗ NEW LAYER | Hurricane season Jun–Nov is the dominant feature. Show current storm tracks + 5-day cone overlay. |

**Predicted-vis model — needs rethinking**:

For tropical water, vis is driven by:
1. **Sahara dust** (aerosol → sky haze → optical clarity proxy + occasional fine sediment fallout)
2. **Hurricane stirring** (mixed layer deepening, sediment resuspension for 3–7 days post-storm)
3. **River plumes** (Mississippi flowing east in Gulf Loop Current, Orinoco creating Caribbean visibility band)
4. **Local rainfall** (terrestrial runoff at reef boundaries)
5. **Tidal exchange in bays** (Florida Bay, Biscayne Bay)
6. **Coral spawning** (briefly, late summer — small effect)

Chl is a poor predictor here. Propose a different `tropical_vis_model` that takes:

```python
secchi_m = base_vis  -  swell_term  -  plume_term  -  dust_term  -  rain_term  -  hurricane_term
```

where `base_vis` is a high baseline (~25 m / 80 ft) and each term is subtractive, similar in spirit to the existing `apply_turbidity_corrections` but flipped from "predict-then-penalize chl-derived Secchi" to "start clear, subtract for known degradation drivers." Per-zone tuning still applies — Bonaire baseline higher than Keys baseline.

**This is the biggest single piece of new work in the whole expansion.** Probably 2–3 PRs on its own.

**Spot pins (initial set, ~30)**:

TX: Flower Garden Banks (East + West).
FL Gulf: Crystal River, Bayport reefs, Tampa shipwrecks.
FL Keys: Looe Key, Sombrero Reef, Molasses Reef, Eagle wreck, Vandenberg, Spiegel Grove.
FL east: Blue Heron Bridge, Jupiter ledge, Breakers Reef, Boynton Ledge, Lauderdale wrecks (Mercedes, Tracy).
Bahamas: Stuart Cove (Nassau), Tiger Beach, Andros Wall, Bimini.
Cayman: Bloody Bay Wall (Little Cayman), Stingray City.
Cozumel: Palancar, Santa Rosa.
Bonaire: 1000 Steps, Hilma Hooker.
Roatan: Mary's Place, El Aguila wreck.
USVI: Cane Bay, Salt River Canyon.
PR: La Parguera, Mona Island.
Belize: Blue Hole, Half Moon Caye.

**Risks specific to tropical**:
1. New vis model is a real research project, not a port. Plan for 2 calibration cycles before trusting the output.
2. Saharan dust forecast integration is novel — no obvious dive-product precedent. Probably means designing the UX as you go.
3. Hurricane overlay is high-stakes — must be clearly distinguished from regular layers, and you must NOT predict vis during an active storm advisory (just show "Active hurricane warning — do not dive").
4. International MPA coverage is patchy. Probably start with WDPA + NOAA NMS, accept some islands have no MPA data.
5. Caribbean spot-pin curation is its own research project (regional dive operator partnerships would accelerate this).

**Estimated PR count for tropical v1**: 10–14 PRs (about 2× PNW).

---

## 4. Risks and unknowns shared across both expansions

- **Data volume**: 3× per-region pipelines means data refreshes go from ~85 KB/day to ~250 KB/day in git history. Still fine for Cloudflare Pages free tier and GitHub Actions free tier (well under 2,000 minute/month limit), but worth monitoring.
- **CI runtime**: Three matrix jobs of the full pipeline could push past the 6-hour Actions timeout if any one region has slow data fetches. Run each region in parallel jobs, not sequential.
- **Validation effort**: each new region needs its own observation-source checklist (similar to `norcal-vis-sources-checklist.md`). Plan for ~20–40 ground-truth observations per region before promoting to default.
- **Branding / regional positioning**: "ShouldIDive" works for any region, but the current site copy is CA-coded ("California coast conditions"). Update the tagline to support multi-region.
- **Mobile RN app**: same architectural concerns — region switcher needs to be wired into the RN app too. Don't ship the web-app region switcher without a parallel RN plan.

---

## 5. Suggested sequencing

Both regions in parallel, but PNW can ship faster:

**Weeks 1–2** (architecture, both regions):
- PR-X-1: Region-aware pipeline scaffold (config layer, per-region data dirs)
- PR-X-2: Region switcher in frontend
- PR-X-3: CI matrix for multi-region refresh

**Weeks 3–4** (PNW ramp):
- PR-PNW-1: PNW bbox + zone family + spot pins
- PR-PNW-2: SSCOFS fetcher for Salish Sea currents
- PR-PNW-3: `pnw_inland` viz model variant
- PR-PNW-4: Olympic Coast NMS + WA/OR MPAs

**Weeks 3–6** (tropical, longer track):
- PR-TROP-1: Two sub-region bboxes + zone family + spot pins
- PR-TROP-2: HYCOM + Global RTOFS currents fetcher
- PR-TROP-3: NASA GEOS-FP Saharan dust fetcher + UI chip
- PR-TROP-4: NOAA NHC hurricane track overlay
- PR-TROP-5: New `tropical_vis_model` (the big one)
- PR-TROP-6: International MPA polygons (WDPA + NMS)
- PR-TROP-7: Spot-pin curation pass

**Weeks 7–8**: Validation, beta disclaimers, gradual rollout.

Realistically: **PNW ships in ~4 weeks, tropical in ~8 weeks** if both are worked simultaneously by a single engineer. Halve those if you parallelize with a second contributor.

---

## 6. Open questions for you before coding starts

1. **Region menu wording** — "PNW" or "OR + WA"? "Caribbean" or "FL + Caribbean"? Naming affects discoverability.
2. **Default region** — geo-IP detected, or last-used, or always CA? Default-CA is safest but cheapens the expansion for non-CA users.
3. **Saharan dust as a chip vs. a feature-only input** — show it as a 7th map layer (carousel still shows 6 chips, dust slides in only in tropical region), or only consume it as a model input without a user-facing layer?
4. **Hurricane advisory mode** — soft warning banner during active NHC advisories, or hard cutoff (model returns no vis prediction)?
5. **Caribbean operator partnerships** — outreach to charter operators in Cozumel/Bonaire/Cayman to validate vis predictions in exchange for free embed/widget? Could be a force multiplier on calibration.
6. **Springs back-burner** — confirming we're not designing for it in v1 but leaving room (config layer must allow a future `freshwater` sibling).
7. **Branding update** — "Should I Dive?" everywhere, or per-region taglines ("Should I Dive the Keys?", "Should I Dive Hood Canal?") for SEO?

---

## Sources

- [NOAA IOOS HF Radar](https://ioos.noaa.gov/project/hf-radar/) — confirms sparse PNW HFR coverage
- [NOAA Salish Sea + Columbia River OFS (SSCOFS)](https://tidesandcurrents.noaa.gov/ofs/sscofs/sscofs.html) — Salish Sea currents
- [HYCOM consortium data](https://www.hycom.org/) — Gulf + Caribbean ocean model
- [NOAA Global RTOFS on ERDDAP](https://www.ncei.noaa.gov/erddap/griddap/Hycom_sfc_3d.graph) — global ocean model surface fields
- [NRL HYCOM Gulf of Mexico on ERDDAP](https://coastwatch.pfeg.noaa.gov/erddap/griddap/hycom_gom310D.html) — high-res GoM
- [NASA GEOS aerosol forecast](https://gmao.gsfc.nasa.gov/research/science_snapshots/2020/Saharan_dust_2020.php) — Saharan dust 10-day forecast
- [NASA SVS GEOS aerosols visualization](https://svs.gsfc.nasa.gov/5572/) — reference imagery
