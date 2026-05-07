# sst_predict — multi-source ensemble SST nowcast + 7-day forecast

**Status: full ensemble framework only.** The deployed app now has a
beta `sst5d` forecast generated in `pipeline/fetch.py` from the freshest
observed SST field plus recent trend persistence. Files in this
directory are still the scaffolds for the later multi-source ensemble
version with ocean-model advection, heat-flux correction, and scoring.
The implementation lands in phases (see "Rollout" below).

## Why this exists

Today's SST surface is a pass-through of the satellite signal: MUR L4
arrives, gets re-projected to the canonical 71×87 grid, and a PNG
ships. There's no:

- multi-source blending (MUR has clouds, gaps, ~1d lag — but VIIRS NRT
  and GOES are fresher; ocean models can fill gaps)
- forecast (the user can see today's water but can't see whether the
  Saturday dive will be 4°F warmer than today)
- uncertainty (a 0.5°F single-source pixel and a 4°F multi-source
  disagreement show up identically)
- self-validation (we have NDBC buoy reads but never compare them to
  what MUR said for the same location)
- self-tuning (MUR has a known +0.3-0.5°F warm bias in coastal CA —
  static, but never corrected)

The other predictive layers in the pipeline (`viz_predict`,
`fetch_wind_5day`, `fetch_swell_5day`) already nailed the patterns for
each of those gaps. This module mirrors them for SST.

## Architecture

```
                                  ┌──────────────────┐
       SOURCES                    │   BLEND (now)    │
       ─────────                  │  per-cell pick   │       FORECAST
                                  │  by trust+age    │       ────────
   ┌─ Satellite ───────┐          │                  │
   │  MUR L4           │ ────►    │   sst_now.png    │ ─►  +1d ─┐
   │  VIIRS NRT        │ ────►    │   sst_now_age    │     +2d  │
   │  GOES ABI         │ ────►    │   sst_now_src    │     ...  │   ENSEMBLE
   │  Geo-Polar        │ ────►    └──────────────────┘     +7d  │   ────────
   │  S3 SLSTR         │ ────►            │                     │
   │  MODIS A/T        │ ────►            ▼                     │  p10 / p50 / p90
   └───────────────────┘          ┌──────────────────┐          │  per-zone σ
                                  │ persistence      │          │  calibrated
   ┌─ Ocean models ────┐          │ + ocean model    │          │  vs hindcast
   │  RTOFS Global     │ ────►    │   advection      │ ─────────┤  residuals
   │  WCOFS regional   │ ────►    │ + bulk heat flux │          │
   │  HYCOM            │ ────►    │   (HRRR/GFS)     │          ▼
   │  CFSv2            │ ────►    │                  │      sst5d/{day}.png
   └───────────────────┘          └──────────────────┘      sst5d/summary.json
                                          ▲
                                          │
   ┌─ Atmos forcing ───┐                  │           VALIDATION
   │  HRRR T2m + winds │ ─────────────────┤           ──────────
   │  GFS  T2m + winds │ ─────────────────┤
   │  CERES insolation │ ─────────────────┘     NDBC water-temp ─┐
   │  CFS heat flux    │                        CDIP buoy temp   │
   └───────────────────┘                        Argo profiles    │
                                                Dive-log SST     ▼
                                                                 sst_score.py
                                                                       │
                                                                       ▼
                                                                  per-zone metrics
                                                                  (bias / rmse /
                                                                  calibration_pct
                                                                  / pearson_r)
                                                                       │
                                                                       ▼
                                                                 sst_watchdog.py
                                                                  R1: bias
                                                                  R2: calibration
                                                                  R3: correlation
                                                                  R4: data flow
                                                                       │
                                                                       ▼
                                                              GitHub Issue
                                                              (sst-watchdog
                                                              label) +
                                                              suggested
                                                              coefficient
                                                              adjustments
```

## Source registry — "check off as many good data sources as we can"

Every source is declared in `sources.py` with a `SstSource` dataclass
carrying URL, lag window, spatial resolution, license, auth scheme,
and a `priority` integer (lower = higher trust). The blender uses that
metadata to pick per-cell.

### Satellite SST (raster)

| ID | Source | Res | Lag | Use |
|---|---|---|---|---|
| `mur_l4` | NOAA/JPL MUR L4 (jplMURSST41) | 1 km | ~1 d | Primary anchor |
| `viirs_snpp_nrt` | NOAA CoastWatch VIIRS S-NPP NRT | 750 m | ~6 h | Fresher than MUR; cloud-gappy |
| `viirs_n20_nrt`  | VIIRS NOAA-20 NRT | 750 m | ~6 h | Pair with SNPP for coverage |
| `goes18_abi`     | GOES-18 ABI L2 SST | 2 km | ~3 h | Hourly cadence — diurnal cycle |
| `geopolar_blend` | NOAA Geo-Polar Blended | 5 km | ~1 d | Sanity cross-check vs MUR |
| `modis_aqua`     | MODIS Aqua L3m daily | 4 km | ~1 d | Long-baseline backup |
| `modis_terra`    | MODIS Terra L3m daily | 4 km | ~1 d | Long-baseline backup |
| `sentinel3_slstr`| Copernicus Sentinel-3 SLSTR | 1 km | ~12 h | EU mirror, dual-view |
| `mur_climo`      | MUR climatology (already in `fetch_climatology.py`) | 1 km | n/a | Last-resort fallback |

### Ocean model forecasts

| ID | Source | Res | Horizon | Use |
|---|---|---|---|---|
| `rtofs_global`  | NOAA RTOFS Global (NOMADS) | 0.08° (~9 km) | 192 h | Day 2-7 SST forecast |
| `wcofs`         | NOAA West Coast Ocean Forecast System | 4 km | 72 h | Best regional model day 0-3 |
| `hycom_global`  | HYCOM Global GLBy0.08 | 0.08° | 180 h | Cross-check vs RTOFS |
| `cfsv2`         | NOAA CFSv2 (long-range coupled) | 0.5° | 9 mo | Seasonal context |

### Atmospheric forcing (heat flux drivers)

These are already pulled by `fetch_wind*.py` for wind layers; the SST
predictor reuses the cached arrays rather than re-fetching.

| ID | Source | Used for |
|---|---|---|
| `hrrr_t2m_winds` | HRRR (already cached) | Bulk heat flux + wind-mixing day 0-2 |
| `gfs_t2m_winds`  | GFS  (already cached) | Same beyond HRRR's 48h |
| `ceres_insol`    | NASA CERES SYN1deg | Surface SW insolation |
| `cfs_heat_flux`  | CFS net heat flux | Daily mean SHF/LHF/LWnet/SWnet |

### Validation point obs

These DON'T feed the predictor — they only ground-truth its output.

| ID | Source | Cadence | Notes |
|---|---|---|---|
| `ndbc_water_temp` | NDBC buoys (already ingested) | hourly | Sensor depth 0.6 / 1 m, ~16 buoys in bbox |
| `cdip_temp`       | CDIP buoys (already ingested) | hourly | Surface temp on instrumented subset |
| `coops_water_temp`| NOAA CO-OPS coastal stations | 6 min | Pier-mounted, biased shallow |
| `argo_profiles`   | NOAA / IFREMER Argo GDAC | 10 d | Used for climatology calibration only |
| `dive_log_sst`    | Reddit / Just Get Wet / DiveViz / BD Outdoors (already ingested) | irregular | Diver-reported water temp; high noise |

## Blend algorithm (current state, "now")

Per cell, the blender picks the value with the **lowest age × distrust**
where:

```
age_days  = today - source_date (per-source)
distrust  = SOURCE_PRIORITY[source] + (cloud_penalty if cloudy_pixel)
score     = α·age_days + β·distrust
```

with α = 1.0 day⁻¹, β = 1.0 priority-rank. Tie-break: priority. This is
literally the chl_blend pattern, just with SST sources. See
`blend.py` for the implementation hook.

Outputs (mirrors chl pattern for manifest backwards-compat):

```
public/data/sst_now.png            blended grayscale 0..255 → temp linear
public/data/sst_now_age_days.png   per-cell age sidecar
public/data/sst_now_source.png     per-cell source-id sidecar
```

## Forecast (+1 to +7 days)

Two-stage:

1. **Persistence + ocean-model advection** — start from today's blended
   field, advect using WCOFS/RTOFS surface currents, decay anomaly
   toward climatology with τ = 14 days (typical autocorrelation
   timescale for nearshore CA SST; calibrated per-zone in `config.py`).

2. **Atmospheric correction** — apply a simple bulk heat-flux
   correction using HRRR/GFS T2m and 10 m winds (NCEP COARE 3.0 — same
   parameterization NCEP uses for RTOFS forcing). The correction is a
   per-day ΔT_skin offset, not a full 1-D mixed-layer model — that's a
   v3 enhancement.

Outputs (mirrors `fetch_wind_5day` / `fetch_swell_5day`):

```
public/data/sst5d/d0..d6_sst.png   per-day blended forecast field (RGBA?)
public/data/sst5d/summary.json     per-day stats (mean, min, max, anomaly,
                                   confidence) — same shape as wind/swell
                                   summaries, so the React + RN clients
                                   pick it up with the existing 5d UI.
```

## Ensemble / uncertainty (p10 / p50 / p90)

Mirror `viz_predict.predict.predict_all`'s p10/p50/p90 pattern. Three
sources of variance:

1. **Source disagreement** — within today's blend, the spread across the
   2-3 satellite sources that contribute non-zero weight in a given cell.
2. **Forecast lead time** — calibrated per-zone σ from the historical
   residuals (`SIGMA_SST_BY_LEAD[zone][lead_days]`), promoted from
   `sst_score.py` output.
3. **Model spread** — RTOFS vs HYCOM disagreement at the same lead.

p10 / p50 / p90 are derived assuming Gaussian errors with σ²=σ₁²+σ₂²+σ₃².
That's the same simplification viz_predict makes for chl. Real ocean
forecasts have heavier tails, but Gaussian is good enough for the v1
"narrow vs wide interval" UX without introducing a Monte Carlo loop.

## Validation + self-tuning loop

`pipeline/validation/sst_score.py`:

- Joins `observations.jsonl` (the existing ingest pipeline already
  collects `observed_sst_f` from NDBC + CDIP + dive logs) against the
  per-day archive snapshot.
- Outputs `pipeline/validation/data/sst_per_zone_metrics.json` and
  `sst_residuals.jsonl` — same schema as the visibility version.

`pipeline/validation/sst_watchdog.py`:

Same R1-R4 rule structure as the existing `watchdog.py`:

- **R1 — zone bias.** |bias_F| > 1.5°F triggers; suggests adjustment to
  per-zone `BIAS_CORRECTION[zone]` in `sst_predict/config.py`.
- **R2 — interval calibration.** % of obs in p10-p90 outside [60, 95]
  triggers; suggests adjustment to `SIGMA_SST_BY_LEAD[zone]`.
- **R3 — correlation.** Pearson r < 0.5 triggers; structural signal —
  forecast model is missing something for this zone (kelp shading?
  estuarine outflow? insufficient diurnal capture?).
- **R4 — data flow.** Same as the visibility watchdog — alarms when
  scrapers go silent OR when key satellite sources stay red across
  multiple consecutive `check_published.py` runs.

The watchdog opens / updates a rolling GitHub Issue tagged
`sst-watchdog`, separate from the existing `validation-watchdog`
(visibility) and `data-health` (infra) so the user can triage by axis.

## What "self-adjustment" looks like

The watchdog **never auto-edits coefficients**. Every finding is a
suggestion in the issue body with a concrete `BIAS_CORRECTION[zone] +=
{delta}` ready to copy-paste. That mirrors the visibility model's
proven pattern: humans-in-the-loop because zone-specific physical
explanations matter more than a closed-loop optimizer would.

For the v2 path: a `python -m pipeline.sst_predict.tune --auto-apply`
mode could promote suggested deltas after N consecutive runs all
suggesting the same direction. Not in v1 — wait for the residual
distribution to stabilize first.

## Why the framework lands first (and the impl is staged)

Three reasons:

1. **Reviewability.** The source registry, the rule thresholds, the
   forecast-stage choices are all in one place where the user can
   say "actually skip CFSv2, it's too coarse" before code is written.
2. **Cost.** RTOFS/HYCOM byte-range pulls add ~200 MB/day to the CI
   budget. WCOFS adds another ~50 MB. Worth confirming sources are
   wanted before we start downloading.
3. **Validation runway.** Even if we built the predictor today, we
   wouldn't trust its output until we'd accumulated 30+ obs/zone of
   residual signal. That's ~2 weeks of ground-truth ingest. So the
   blender + scoring layer should land first — they generate the
   residual signal that the forecast eventually has to beat.

## Rollout phases

**Phase 1 (this commit) — framework**
- Directory structure + module skeletons
- Source registry as data (every source declared, no fetchers)
- README design doc (this file)
- Unit test that the registry validates
- Nothing wired to CI

**Phase 2 — minimum-viable blender**
- Wire `mur_l4` (already fetched), `viirs_snpp_nrt`, `viirs_n20_nrt`
- Implement `blend.py` (literally chl_blend with priority weights)
- Output `sst_now*.png` to manifest
- Add `sst_score.py` joining against NDBC obs
- Wire daily run to `refresh-data.yml`

**Phase 3 — ocean-model forecast**
- Wire `rtofs_global` + `wcofs`
- Implement `forecast.py` (persistence + advection + heat-flux)
- Output `sst5d/` to manifest
- Add 5d-summary parsing to React + RN clients (mirror wind5d)

**Phase 4 — ensemble + self-tuning**
- Implement `ensemble.py` (p10/p50/p90 from source spread + lead-time
  σ + model spread)
- Calibrate `SIGMA_SST_BY_LEAD` from accumulated residuals
- Wire `sst_watchdog.py` to refresh-data.yml issue-sync
- Promote first `sst_per_zone_baseline.json`

**Phase 5 — auto-tune**
- `python -m pipeline.sst_predict.tune --auto-apply` for stable-bias
  zones (only after R1 has fired with the same sign for ≥7 consecutive
  days)
- Rolling regression guard mirroring `check_regression.py`

## File map

```
pipeline/sst_predict/
  README.md            ← this file
  __init__.py
  config.py            ← zone defs, source registry, coefficients
  sources.py           ← SstSource registry + fetcher stubs
  blend.py             ← multi-source per-cell blender
  forecast.py          ← persistence + advection + heat-flux
  ensemble.py          ← p10/p50/p90 uncertainty
  encode.py            ← PNG + manifest emit
  predict.py           ← predict_all() public entry
  fetch_sst_predict.py ← CLI orchestrator (NOT wired to CI)
  tests/
    __init__.py
    test_config.py     ← zone classifier round-trip
    test_sources.py    ← source registry validation

pipeline/validation/
  sst_score.py         ← obs↔prediction join, per-zone metrics
  sst_watchdog.py      ← R1-R4 rules for SST
```

## Hand-off

To start phase 2, the next session should:

1. Read this README + `sources.py` to confirm the source list.
2. Pick the smallest viable subset (recommend `mur_l4` + `viirs_snpp_nrt`
   + `viirs_n20_nrt` only — 3 sources, all already auth-handled by
   `chl_blend.py`'s NASA token).
3. Implement `blend.py` by literally adapting `chl_blend.py:_blend_cells()`
   — the blending logic is identical, only the source list changes.
4. Wire to `refresh-data.yml` AFTER `sst_score.py` shows residuals are
   getting collected. The validation runway has to lead the predictor.
