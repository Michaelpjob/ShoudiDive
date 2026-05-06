"""SstSource registry — every data source the SST predictor can use.

This file is DATA, not implementation. Each source is declared as an
``SstSource`` dataclass with the metadata the blender + forecast +
validation layers need (URL, lag, resolution, license, auth scheme,
trust priority). Fetcher functions are stubs that raise
``NotImplementedError``; phase-2 implementation literally copies the
patterns from ``chl_blend.py`` (NASA OB.DAAC) and
``fetch_wind_5day.py`` (NOMADS byte-range) that already work.

To add a new source:
  1. Append an SstSource entry below.
  2. Implement its fetcher in this same file (next to the registry,
     matching the pattern of the others when phase-2 lands).
  3. Pick the priority by trust — lower number = higher trust.
  4. Register it in the corresponding registry list (SAT_SOURCES,
     MODEL_SOURCES, FORCING_SOURCES, OBS_SOURCES) at the bottom.

To remove a source: delete the entry. The blender + forecast + score
modules iterate the registry, so they pick up changes for free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import numpy as np


# ---- Categories ---------------------------------------------------------

CATEGORY_SAT      = "satellite"   # raster SST observations
CATEGORY_MODEL    = "model"       # ocean-model SST forecasts
CATEGORY_FORCING  = "forcing"     # atmospheric drivers (heat flux inputs)
CATEGORY_OBS      = "point_obs"   # ground-truth point observations


# ---- Source dataclass ---------------------------------------------------

@dataclass
class SstSource:
    """One data source the SST predictor can pull from.

    The ``fetcher`` callable signature varies by category:

      satellite:  (date) -> np.ndarray|None     # 71×87 °C grid for that day
      model:      (date) -> np.ndarray|None     # (lead_days, 71, 87) °C
      forcing:    (date) -> dict|None           # named arrays (T2m, U10, V10, …)
      point_obs:  (date_from, date_to) -> list[dict]  # buoy time-series rows

    ``None``/empty return = "no data for this date, fall back to next
    source by priority". Errors mid-fetch should be caught and logged
    by the fetcher itself; raising propagates up and aborts the run.
    """
    id:                    str
    category:              str
    label:                 str
    priority:              int               # lower = higher trust
    spatial_res_km:        Optional[float]   # None for point obs
    typical_lag_hours:     Optional[float]   # None for point obs (real-time)
    forecast_horizon_h:    int = 0           # 0 for nowcast/obs, >0 for models
    license:               str = "public"
    auth_env_var:          Optional[str] = None  # e.g. "EARTHDATA_TOKEN"
    homepage:              str = ""
    notes:                 str = ""
    fetcher:               Optional[Callable] = field(default=None, repr=False)


# =========================================================================
# SATELLITE SST (raster) — phase 2 minimum-viable subset starts here
# =========================================================================

# ----- MUR L4 (existing in fetch.py) ------------------------------------
#
# Already pulled by pipeline/fetch.py at 1km via the PFEG ERDDAP mirror.
# Phase 2 wires the SAME cached array into sst_predict (no double fetch).

def _fetch_mur_l4(d: date) -> Optional[np.ndarray]:
    """TODO[phase-2]: read the MUR L4 array fetch.py already cached for
    this date and resample to (71, 87). Today fetch.py writes
    sst_*.png directly; we'll add a sidecar .npy or rework fetch.py
    to expose the raw array via ``cache_loader.get('mur_l4', d)``."""
    raise NotImplementedError("phase-2 stub")


SRC_MUR_L4 = SstSource(
    id="mur_l4",
    category=CATEGORY_SAT,
    label="NOAA/JPL MUR L4 (gap-filled SST)",
    priority=1,                       # highest trust
    spatial_res_km=1.0,
    typical_lag_hours=24,
    homepage="https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.html",
    notes="Already integrated via fetch.py. Phase-2 reuses the cached array.",
    fetcher=_fetch_mur_l4,
)


# ----- VIIRS S-NPP NRT --------------------------------------------------

def _fetch_viirs_snpp_nrt(d: date) -> Optional[np.ndarray]:
    """TODO[phase-2]: NOAA STAR L3 NRT @ 750m. Daily granules.
    Pattern: same NASA OB.DAAC + Earthdata bearer used by chl_blend.py."""
    raise NotImplementedError("phase-2 stub")


SRC_VIIRS_SNPP_NRT = SstSource(
    id="viirs_snpp_nrt",
    category=CATEGORY_SAT,
    label="VIIRS SNPP NRT SST (NOAA STAR)",
    priority=2,
    spatial_res_km=0.75,
    typical_lag_hours=6,
    auth_env_var="EARTHDATA_TOKEN",
    homepage="https://www.star.nesdis.noaa.gov/socd/sst/",
    notes="Cloud-gappy single-pass; pair with N20 for coverage.",
    fetcher=_fetch_viirs_snpp_nrt,
)


# ----- VIIRS NOAA-20 NRT ------------------------------------------------

def _fetch_viirs_n20_nrt(d: date) -> Optional[np.ndarray]:
    """TODO[phase-2]: same as SNPP, different platform; ~50% offset
    overpass time → fills SNPP coverage gaps."""
    raise NotImplementedError("phase-2 stub")


SRC_VIIRS_N20_NRT = SstSource(
    id="viirs_n20_nrt",
    category=CATEGORY_SAT,
    label="VIIRS NOAA-20 NRT SST",
    priority=2,
    spatial_res_km=0.75,
    typical_lag_hours=6,
    auth_env_var="EARTHDATA_TOKEN",
    homepage="https://www.star.nesdis.noaa.gov/socd/sst/",
    notes="Fills SNPP cloud gaps; identical sensor.",
    fetcher=_fetch_viirs_n20_nrt,
)


# ----- GOES-18 ABI L2 SST -----------------------------------------------

def _fetch_goes18_abi(d: date) -> Optional[np.ndarray]:
    """TODO[phase-3]: hourly SST @ 2km, sub-3h lag. NetCDF on
    NOAA's Open Data on AWS S3 (free egress, no auth)."""
    raise NotImplementedError("phase-3 stub")


SRC_GOES18_ABI = SstSource(
    id="goes18_abi",
    category=CATEGORY_SAT,
    label="GOES-18 ABI L2 SST (hourly)",
    priority=3,
    spatial_res_km=2.0,
    typical_lag_hours=3,
    homepage="https://noaa-goes18.s3.amazonaws.com/index.html",
    notes="Hourly cadence — captures diurnal cycle MUR/VIIRS miss.",
    fetcher=_fetch_goes18_abi,
)


# ----- NOAA Geo-Polar Blended SST ---------------------------------------

def _fetch_geopolar_blend(d: date) -> Optional[np.ndarray]:
    """TODO[phase-3]: 5km blend of LEO + GEO satellites. NOAA
    CoastWatch ERDDAP (no auth). Use as cross-check vs MUR — large
    disagreements are a red flag in QC."""
    raise NotImplementedError("phase-3 stub")


SRC_GEOPOLAR_BLEND = SstSource(
    id="geopolar_blend",
    category=CATEGORY_SAT,
    label="NOAA Geo-Polar Blended SST",
    priority=4,
    spatial_res_km=5.0,
    typical_lag_hours=24,
    homepage="https://coastwatch.pfeg.noaa.gov/erddap/griddap/nesdisGeoPolarSSTN5SQNRT.html",
    notes="Cross-check vs MUR; large differences indicate satellite-vs-blend disagreement.",
    fetcher=_fetch_geopolar_blend,
)


# ----- MODIS Aqua / Terra L3m daily -------------------------------------

def _fetch_modis_aqua(d: date) -> Optional[np.ndarray]:
    """TODO[phase-3]: NASA OB.DAAC L3m daily 4km SST.
    Pattern: same as chl_blend.py's MODIS fetcher."""
    raise NotImplementedError("phase-3 stub")


SRC_MODIS_AQUA = SstSource(
    id="modis_aqua",
    category=CATEGORY_SAT,
    label="MODIS Aqua L3m daily SST",
    priority=5,
    spatial_res_km=4.0,
    typical_lag_hours=24,
    auth_env_var="EARTHDATA_TOKEN",
    homepage="https://oceancolor.gsfc.nasa.gov/data/aqua/",
    notes="Long-baseline backup; Aqua is end-of-life.",
    fetcher=_fetch_modis_aqua,
)


def _fetch_modis_terra(d: date) -> Optional[np.ndarray]:
    """TODO[phase-3]: pair with Aqua; different overpass time."""
    raise NotImplementedError("phase-3 stub")


SRC_MODIS_TERRA = SstSource(
    id="modis_terra",
    category=CATEGORY_SAT,
    label="MODIS Terra L3m daily SST",
    priority=5,
    spatial_res_km=4.0,
    typical_lag_hours=24,
    auth_env_var="EARTHDATA_TOKEN",
    homepage="https://oceancolor.gsfc.nasa.gov/data/terra/",
    notes="Backup; pairs with Aqua for diurnal cycle.",
    fetcher=_fetch_modis_terra,
)


# ----- Sentinel-3 SLSTR (Copernicus) ------------------------------------

def _fetch_sentinel3_slstr(d: date) -> Optional[np.ndarray]:
    """TODO[phase-3]: dual-view sensor → atmospheric correction is
    sharper than single-view sensors. Copernicus Marine; requires
    free CMEMS account with CMEMS_USER + CMEMS_PASS env vars."""
    raise NotImplementedError("phase-3 stub")


SRC_SENTINEL3_SLSTR = SstSource(
    id="sentinel3_slstr",
    category=CATEGORY_SAT,
    label="Sentinel-3 SLSTR L2 SST",
    priority=4,
    spatial_res_km=1.0,
    typical_lag_hours=12,
    auth_env_var="CMEMS_PASS",        # requires CMEMS_USER too
    homepage="https://marine.copernicus.eu/",
    notes="Dual-view atmospheric correction; tightly registered.",
    fetcher=_fetch_sentinel3_slstr,
)


# ----- MUR climatology (already integrated) -----------------------------

def _fetch_mur_climo(d: date) -> Optional[np.ndarray]:
    """TODO[phase-2]: read pipeline/fetch_climatology.py output.
    Climatology is the LAST-RESORT prior — when every satellite source
    failed for >MAX_BACK days, fall through to climatological mean.
    """
    raise NotImplementedError("phase-2 stub")


SRC_MUR_CLIMO = SstSource(
    id="mur_climo",
    category=CATEGORY_SAT,
    label="MUR L4 climatology (long-term mean)",
    priority=99,                      # last resort
    spatial_res_km=1.0,
    typical_lag_hours=None,           # static
    homepage="(internal: fetch_climatology.py)",
    notes="Fall-through when every observation source is exhausted.",
    fetcher=_fetch_mur_climo,
)


# =========================================================================
# OCEAN MODEL FORECASTS — phase 3 unlocks day +1 .. +7 forecasts
# =========================================================================

def _fetch_rtofs_global(d: date) -> Optional[np.ndarray]:
    """TODO[phase-3]: NOMADS RTOFS Global daily run. 0.08° (~9km).
    Subset to bbox via NOMADS NetCDF Subset Service. Returns
    (lead_h, 71, 87) at hourly resolution out to 192 h."""
    raise NotImplementedError("phase-3 stub")


SRC_RTOFS = SstSource(
    id="rtofs_global",
    category=CATEGORY_MODEL,
    label="NOAA RTOFS Global (HYCOM-based)",
    priority=2,
    spatial_res_km=9.0,
    typical_lag_hours=12,
    forecast_horizon_h=192,
    homepage="https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/",
    notes="Daily run, 192 h hourly. Best balance of resolution + horizon.",
    fetcher=_fetch_rtofs_global,
)


def _fetch_wcofs(d: date) -> Optional[np.ndarray]:
    """TODO[phase-3]: NOAA West Coast Ocean Forecast System.
    4 km regional, 72 h hourly. Best regional skill day 0-3 — this is
    the forecast input for nearshore CA, by far."""
    raise NotImplementedError("phase-3 stub")


SRC_WCOFS = SstSource(
    id="wcofs",
    category=CATEGORY_MODEL,
    label="NOAA West Coast Ocean Forecast System",
    priority=1,                       # highest trust for our region day 0-3
    spatial_res_km=4.0,
    typical_lag_hours=12,
    forecast_horizon_h=72,
    homepage="https://nomads.ncep.noaa.gov/pub/data/nccf/com/nos/prod/",
    notes="Regional, hi-res. Day 0-3 anchor; falls back to RTOFS at 72h+.",
    fetcher=_fetch_wcofs,
)


def _fetch_hycom_global(d: date) -> Optional[np.ndarray]:
    """TODO[phase-3]: HYCOM Global 0.08°, 180 h. Cross-check vs RTOFS
    (RTOFS is HYCOM-based but with different boundary conditions).
    Disagreement at lead time t informs the model-spread term in
    ``ensemble.py``."""
    raise NotImplementedError("phase-3 stub")


SRC_HYCOM_GLOBAL = SstSource(
    id="hycom_global",
    category=CATEGORY_MODEL,
    label="HYCOM Global GLBy0.08",
    priority=3,
    spatial_res_km=9.0,
    typical_lag_hours=12,
    forecast_horizon_h=180,
    homepage="https://www.hycom.org/dataserver/gofs-3pt1/analysis",
    notes="RTOFS sibling; spread = ensemble uncertainty signal.",
    fetcher=_fetch_hycom_global,
)


def _fetch_cfsv2(d: date) -> Optional[np.ndarray]:
    """TODO[phase-4]: NCEP CFSv2 long-range coupled ocean-atmos. 0.5°,
    9-month horizon. Used for SEASONAL CONTEXT only — informs whether
    the upcoming month is on track to be warm/cool vs climatology.
    Not pixel-level forecast input."""
    raise NotImplementedError("phase-4 stub")


SRC_CFSV2 = SstSource(
    id="cfsv2",
    category=CATEGORY_MODEL,
    label="NCEP CFSv2 long-range coupled",
    priority=10,                      # not for daily forecast
    spatial_res_km=55.0,
    typical_lag_hours=24,
    forecast_horizon_h=9 * 30 * 24,   # 9 months
    homepage="https://nomads.ncep.noaa.gov/pub/data/nccf/com/cfs/prod/",
    notes="Seasonal context only; too coarse for diving-relevant pixel resolution.",
    fetcher=_fetch_cfsv2,
)


# =========================================================================
# ATMOSPHERIC FORCING — heat flux inputs for forecast.py
# =========================================================================
# These are already pulled by fetch_wind*.py for the wind layer. The
# SST forecaster reads the cached arrays rather than re-fetch. Listed
# here so the source registry is complete and the validation pipeline
# can flag forcing-driven prediction errors when HRRR/GFS goes red.

def _fetch_hrrr_forcing(d: date) -> Optional[dict]:
    """TODO[phase-3]: read T2m, U10, V10, dswrf (downward shortwave),
    cloud cover from the existing HRRR cache. Returns dict with named
    arrays. forecast.py runs COARE 3.0 bulk flux from these."""
    raise NotImplementedError("phase-3 stub")


SRC_HRRR_FORCING = SstSource(
    id="hrrr_forcing",
    category=CATEGORY_FORCING,
    label="HRRR atmospheric forcing (already cached)",
    priority=1,
    spatial_res_km=3.0,
    typical_lag_hours=2,
    forecast_horizon_h=48,
    homepage="(internal: fetch_wind_5day.py cache)",
    notes="Reused from wind pipeline; no extra NOMADS fetch.",
    fetcher=_fetch_hrrr_forcing,
)


def _fetch_gfs_forcing(d: date) -> Optional[dict]:
    """TODO[phase-3]: GFS T2m + 10m winds + heat flux for lead 49+ h."""
    raise NotImplementedError("phase-3 stub")


SRC_GFS_FORCING = SstSource(
    id="gfs_forcing",
    category=CATEGORY_FORCING,
    label="GFS atmospheric forcing (already cached)",
    priority=2,
    spatial_res_km=25.0,
    typical_lag_hours=4,
    forecast_horizon_h=168,
    homepage="(internal: fetch_wind_5day.py cache)",
    notes="Reused from wind pipeline.",
    fetcher=_fetch_gfs_forcing,
)


def _fetch_ceres_insol(d: date) -> Optional[np.ndarray]:
    """TODO[phase-4]: CERES SYN1deg surface SW insolation. Daily mean.
    Improves heat-flux fidelity over HRRR's clear-sky default
    (HRRR underestimates marine-layer solar attenuation in summer)."""
    raise NotImplementedError("phase-4 stub")


SRC_CERES_INSOL = SstSource(
    id="ceres_insol",
    category=CATEGORY_FORCING,
    label="NASA CERES SYN1deg SW insolation",
    priority=3,
    spatial_res_km=110.0,
    typical_lag_hours=72,
    homepage="https://ceres.larc.nasa.gov/data/",
    notes="Improves marine-layer solar attenuation in summer fog.",
    fetcher=_fetch_ceres_insol,
)


def _fetch_cfs_heat_flux(d: date) -> Optional[dict]:
    """TODO[phase-4]: CFS net heat flux components (SW, LW, SH, LH).
    Daily mean. Used for hindcast bias correction; live use is overkill."""
    raise NotImplementedError("phase-4 stub")


SRC_CFS_HEAT_FLUX = SstSource(
    id="cfs_heat_flux",
    category=CATEGORY_FORCING,
    label="NCEP CFS net heat flux",
    priority=4,
    spatial_res_km=55.0,
    typical_lag_hours=24,
    homepage="https://nomads.ncep.noaa.gov/pub/data/nccf/com/cfs/prod/",
    notes="Components for bulk-flux validation; not in v1 forecast loop.",
    fetcher=_fetch_cfs_heat_flux,
)


# =========================================================================
# POINT OBS — ground truth for sst_score.py + sst_watchdog.py
# =========================================================================
# These don't enter the predictor — they only validate it.

def _fetch_ndbc_water_temp(d_from: date, d_to: date) -> list[dict]:
    """TODO[phase-2]: read existing observations.jsonl rows where
    source=='ndbc-buoy' AND observed_sst_f is not None.
    No extra fetch needed — the validation/ingest pipeline already
    has the data."""
    raise NotImplementedError("phase-2 stub")


SRC_NDBC = SstSource(
    id="ndbc_water_temp",
    category=CATEGORY_OBS,
    label="NDBC buoys — water temperature",
    priority=1,
    spatial_res_km=None,
    typical_lag_hours=None,
    homepage="https://www.ndbc.noaa.gov/",
    notes="Already ingested by validation/ingest/ndbc.py. ~16 buoys in bbox.",
    fetcher=_fetch_ndbc_water_temp,
)


def _fetch_cdip_temp(d_from: date, d_to: date) -> list[dict]:
    """TODO[phase-2]: read observations.jsonl for source='cdip-buoy'.
    CDIP measures water temp on instrumented subset of buoys."""
    raise NotImplementedError("phase-2 stub")


SRC_CDIP = SstSource(
    id="cdip_temp",
    category=CATEGORY_OBS,
    label="CDIP buoys — sea surface temperature",
    priority=1,
    spatial_res_km=None,
    typical_lag_hours=None,
    homepage="https://cdip.ucsd.edu/",
    notes="Already ingested via validation/ingest/cdip.py.",
    fetcher=_fetch_cdip_temp,
)


def _fetch_coops_water_temp(d_from: date, d_to: date) -> list[dict]:
    """TODO[phase-3]: NOAA CO-OPS coastal water temp stations.
    Pier-mounted, biased toward shallow + diurnal-warming. Treat as
    lower-trust point obs."""
    raise NotImplementedError("phase-3 stub")


SRC_COOPS = SstSource(
    id="coops_water_temp",
    category=CATEGORY_OBS,
    label="NOAA CO-OPS coastal water temperature",
    priority=2,
    spatial_res_km=None,
    typical_lag_hours=None,
    homepage="https://tidesandcurrents.noaa.gov/",
    notes="Pier-mounted; expect ~+1°F bias vs offshore SST.",
    fetcher=_fetch_coops_water_temp,
)


def _fetch_argo_profiles(d_from: date, d_to: date) -> list[dict]:
    """TODO[phase-4]: NOAA / IFREMER Argo GDAC profiles. Used for
    climatology calibration only — Argo cycle is 10 days so it
    can't validate the daily forecast directly."""
    raise NotImplementedError("phase-4 stub")


SRC_ARGO = SstSource(
    id="argo_profiles",
    category=CATEGORY_OBS,
    label="Argo float profiles",
    priority=3,
    spatial_res_km=None,
    typical_lag_hours=None,
    homepage="https://argo.ucsd.edu/",
    notes="10-day cycle; used to calibrate the climatology fallback prior.",
    fetcher=_fetch_argo_profiles,
)


def _fetch_dive_log_sst(d_from: date, d_to: date) -> list[dict]:
    """TODO[phase-2]: filter observations.jsonl for rows with
    observed_sst_f set from sources reddit-r-scuba / justgetwet /
    diveviz / bdoutdoors. High noise but covers nearshore diving
    spots that buoys miss (kelp forest interiors etc)."""
    raise NotImplementedError("phase-2 stub")


SRC_DIVE_LOG = SstSource(
    id="dive_log_sst",
    category=CATEGORY_OBS,
    label="Dive log SST observations (Reddit/blog)",
    priority=4,
    spatial_res_km=None,
    typical_lag_hours=None,
    homepage="(internal: validation/ingest/*)",
    notes="High noise (±2°F typical); informative for nearshore where buoys are sparse.",
    fetcher=_fetch_dive_log_sst,
)


# =========================================================================
# REGISTRIES — iteration entry points for the rest of the module
# =========================================================================

SAT_SOURCES: list[SstSource] = [
    SRC_MUR_L4,
    SRC_VIIRS_SNPP_NRT,
    SRC_VIIRS_N20_NRT,
    SRC_GOES18_ABI,
    SRC_GEOPOLAR_BLEND,
    SRC_MODIS_AQUA,
    SRC_MODIS_TERRA,
    SRC_SENTINEL3_SLSTR,
    SRC_MUR_CLIMO,
]

MODEL_SOURCES: list[SstSource] = [
    SRC_WCOFS,
    SRC_RTOFS,
    SRC_HYCOM_GLOBAL,
    SRC_CFSV2,
]

FORCING_SOURCES: list[SstSource] = [
    SRC_HRRR_FORCING,
    SRC_GFS_FORCING,
    SRC_CERES_INSOL,
    SRC_CFS_HEAT_FLUX,
]

OBS_SOURCES: list[SstSource] = [
    SRC_NDBC,
    SRC_CDIP,
    SRC_COOPS,
    SRC_ARGO,
    SRC_DIVE_LOG,
]

ALL_SOURCES: list[SstSource] = (
    SAT_SOURCES + MODEL_SOURCES + FORCING_SOURCES + OBS_SOURCES
)


def by_id(source_id: str) -> Optional[SstSource]:
    """Lookup helper for code that holds a string id from manifest output."""
    for s in ALL_SOURCES:
        if s.id == source_id:
            return s
    return None
