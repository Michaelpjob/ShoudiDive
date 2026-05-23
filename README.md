# ShouldIDive

Live ocean conditions for divers, freedivers, surfers, and anglers — Sea
surface temperature, water clarity (chlorophyll + Kd490 + visibility
model), wind, swell, currents, RTOFS forecast, and 5-day forecasts for
all of them.

Live at [shouldidive.com](https://shouldidive.com). Repo name is
`ShoudiDive` (typo we inherited); the public-facing brand is
**ShouldIDive** and the Vite package name is `ca-coast-conditions` —
those mismatches are cosmetic, not bugs.

## Regions

Each region has its own bbox + data pipeline + Cloudflare Pages
deployment target. Picked by `SHOULDIDIVE_REGION` env var on the
pipeline side and resolved by `src/lib/region.js` from the hostname on
the frontend.

| Region | Bbox | Live URL |
|---|---|---|
| **ca** (prod) | 31.8°–42.0°N, -128.5° to -116.8° (NorCal + SoCal) | [shouldidive.com](https://shouldidive.com) |
| **ca-beta** | same bbox as ca; staging surface | ca-beta.shouldidive.pages.dev |
| **pnw** | 42.0°–49.0°N, -127.0° to -122.0° (Oregon + Washington + Salish Sea) | pnw-beta.shouldidive.pages.dev |
| **tropical** | 10.0°–31.0°N, -98.0° to -60.0° (FL + Caribbean + Gulf + Bahamas) | tropical-beta.shouldidive.pages.dev |

## What's here

- `src/` — Vite + React frontend. Renders SVG basemaps + DataOverlay
  canvas with per-layer color ramps. Components include map (SeaBasemap,
  LandBasemap, MapLabels), data overlay (DataOverlay, WindParticles,
  MpaLayer, BathyLayer), and a per-layer timeline (SstTimeline,
  WindTimeline, SwellTimeline, CurrentTimeline).
- `pipeline/` — Python data fetcher + visibility predictor. Pulls SST
  (MUR L4), chlorophyll (VIIRS/MODIS via DINEOF blend), Kd_490, wind
  (HRRR + GFS), swell (NOAA WW3), currents (HFR + tide/wind blend),
  RTOFS ocean-model SST + currents, precipitation (NOAA CPC), river
  discharge (USGS), tides (NOAA CO-OPS), MPA boundaries, bathymetry
  (GMRT). Writes manifest + PNGs into `public/data/`. The `viz_predict/`
  subpackage produces the predicted-visibility layer (beta) from
  upwelling activity + SST anomaly + chl + zone coefficients.
- `mobile/` — React Native / Expo client that shares the same manifest
  + PNG data layer as the web frontend.
- `functions/` — Cloudflare Pages Functions for analytics endpoint
  (`/api/analytics/event`) and a `_middleware.js` that hard-404s
  scanner-known paths.
- `tests/` — Web frontend contract tests (`*.test.js`) +
  `tests/checkpoints/*.test.js` (data-shape, rendering-math, sst-trend,
  mobile-adaptive) + `tests/live-checkpoints/` (post-deploy probes).
- `pipeline/tests/` — Python pytest suite incl. the 42-test
  `test_data_integrity.py` that validates the post-fetch outputs.
- `.github/workflows/` — see below.

## Workflows

| Workflow | Schedule | Purpose |
|---|---|---|
| `dev-checks.yml` | push to dev, PR → main | Web build + lint + tests + smoke + visual-paint + pipeline-tests + manifest-validate + secrets-scan + workflow-lint |
| `refresh-ca-data.yml` | daily 06:00 UTC | Pull fresh CA data, commit PNGs, trigger `deploy-prod.yml` |
| `refresh-ca-wind.yml` | hourly :15 | Pull fresh CA wind, commit, trigger deploy |
| `refresh-pnw-{data,wind}.yml` + `refresh-tropical-{data,wind}.yml` | daily + hourly | Same shape per region |
| `refresh-ca-beta-{data,wind}.yml` | hourly + daily | Staging deploy of CA pipeline (with the dev branch) |
| `deploy-prod.yml` | push to main + workflow_dispatch | Code-only deploy fast path to shouldidive.com |
| `deploy-{ca-beta,pnw-beta,tropical-beta}.yml` | workflow_run + workflow_dispatch | Deploy each region's beta to its Cloudflare Pages branch |
| `deploy-verify.yml` | every 4 h + after deploys | live-cp-manifest + live-cp-render probes against shouldidive.com; opens an Issue if either fails |
| `uptime-monitor.yml` | every 5 min | Lightweight homepage + manifest reachability probe |
| `sync-dev.yml` | push to main (bot only) | Auto-merge main → dev so cron pushes don't leave open PRs DIRTY |
| `health-check.yml` + `ingest-ground-truth.yml` + `promote-baseline.yml` | various crons | Validation pipeline (feed health, scraped dive-shop observations, baseline promotion) |
| `codeql.yml` | weekly + on push | JS + Python SAST |

The branching contract that those workflows enforce lives in
[`CLAUDE.md`](CLAUDE.md) (also copied to `AGENTS.md` for OpenAI Codex
and other agents that follow that convention).

## Local dev

```bash
npm install
npm run dev          # frontend on http://127.0.0.1:5173

# Run any of the regional pipelines
cd pipeline
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

SHOULDIDIVE_REGION=ca .venv/Scripts/python.exe fetch.py            # SST + chl + kd490
SHOULDIDIVE_REGION=ca .venv/Scripts/python.exe fetch_wind.py       # wind
SHOULDIDIVE_REGION=ca .venv/Scripts/python.exe fetch_sst_5day.py   # SST forecast
SHOULDIDIVE_REGION=ca .venv/Scripts/python.exe fetch_visibility.py # viz model
# (or run refresh-ca-data.yml's full pipeline locally via that script's shell)
```

The fetcher steps write into `public/data/` (for `ca`) or
`public/data/<region>/` (for `pnw` / `tropical`). Vite serves them at
`/data/...` and `src/lib/dataSource.js` decodes them at boot.

## Data sources

All no-auth, all fetched by `pipeline/fetch_*.py`:

- **SST** — GHRSST MUR L4 1 km gap-filled (`jplMURSST41`), with optional
  buoy-correction blend and a nearshore-correction layer
- **SST climatology** — NOAA OISST v2.1 1991-2020 30-year monthly
  normal (`noaa_psl_55a2_880b_1f29` on NEFSC ERDDAP)
- **Chlorophyll** — Multi-source DINEOF blend (NRT + science-quality
  VIIRS + MODIS), written to `chl_1d.png` / `chl_2d.png` / `chl_3d.png`
  with age + source sidecars
- **Kd_490** — DINEOF gap-filled multi-sensor Kd_490
- **Wind** — NOAA HRRR (3 km) + GFS (0.25°) — HRRR for current,
  GFS for 5-day forecast
- **Swell** — NOAA WaveWatch III (gfswave)
- **Currents** — HF-radar + tide + wind inference blend
- **RTOFS** — NOAA RTOFS Global 2ds ocean model (SST + surface currents,
  daily 00z cycle)
- **Precip** — NOAA CPC global daily, 7-day rolling sum
- **Rivers** — USGS NWIS gauges
- **Tides** — NOAA CO-OPS per-region station list
- **MPAs** — California Department of Fish & Wildlife polygons
- **Bathymetry** — GMRT global multi-resolution topography

## Visibility model

`pipeline/viz_predict/` produces the (beta) predicted Secchi depth in
feet for every cell. Pipeline:

1. Per-cell zone classification (lat band × distance from shore)
2. Drivers: upwelling-activity (wind + cold-SST coupled), SST anomaly,
   SST 3-day cooling trend, seasonal chl residual, runoff (precip + dist
   to river), river discharge anomaly, swell-driven bottom stir, exposure
   index, tide index, kelp-canopy gating, substrate type, cloud fraction
3. Persistence-with-decay blend of the observed chl over the last few
   days (tau per zone)
4. Chl → Secchi via per-zone `a · chl^(−b)` coefficients
5. Optional Kd_490 blend (gated to suppress when Kd is stale relative to
   chl)
6. Frontend renders as a separate "viz" layer with a "Beta" pill

The model is region-aware (different coefficients per lat band) and is
the main thing being actively tuned — the per-zone Pearson-r regression
test in `pipeline/tests/test_data_integrity.py` is the gate that catches
when a zone's tuning regresses.

## Deployment

Cloudflare Pages, one project per region:

- `shouldidive` (prod) maps to `main` branch
- `ca-beta` branch → ca-beta.shouldidive.pages.dev
- `pnw-beta` / `tropical-beta` branches → those preview subdomains
- `dev` branch → dev.shouldidive.pages.dev

`refresh-*-data.yml` writes data and triggers a `deploy-*.yml`; data and
code are decoupled (refresh doesn't build/deploy; deploy doesn't refresh
data). `deploy-cloudflare` composite action handles the actual
`wrangler pages deploy` with a pinned wrangler version + token-strip
workaround.

## Where the system docs live

- [`CLAUDE.md`](CLAUDE.md) — agent branching + workflow contract
- [`SECURITY.md`](SECURITY.md) — security posture + CSP / HSTS rationale
- [`tests/CHECKPOINTS.md`](tests/CHECKPOINTS.md) — full taxonomy of what
  each test gate catches
- `pipeline/algorithm-design.md` + `pipeline/viz_predict/` docstrings —
  visibility model design + per-zone coefficient rationale

## Costs

Free tier of GitHub Actions, Cloudflare Pages, and the underlying NOAA /
NASA / Copernicus / USGS / CDFW endpoints. Daily PNG refreshes accumulate
git history (~50–100 MB total at time of writing); planned migration to
Cloudflare R2 will stop that bloat.
