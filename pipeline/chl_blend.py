"""Multi-source chlorophyll-a blender.

Blends chl from multiple satellite missions to maximize freshness +
spatial coverage. Per-cell freshest-wins composite tracked with a
provenance sidecar.

Sources, priority order (lower = higher trust + earlier in tie-break):
  1. AQUA_MODIS NRT 4km     — NASA OB.DAAC, ~1 day lag
  2. SNPP_VIIRS NRT 4km     — NASA OB.DAAC, ~1 day lag (different orbital pass)
  3. S3A_OLCI ERR-NRT 4km   — NASA OB.DAAC, ~2 day lag (Copernicus mirror)
  4. NOAA DINEOF NRT 4km    — gap-filled multi-sensor, ~4 day lag (current
                               single-source baseline)
  5. NOAA DINEOF SCI 2km    — gap-filled science-quality multi-sensor,
                               ~12 day lag (last-resort + sharper detail)

Per-cell algorithm (in build_blended_chl):
  for each source in priority order:
    walk back from end_date for max_back days, fetch each day, regrid
      to the canonical 71×87 bbox grid, take the FIRST non-NaN frame
      per source (= the per-source freshest valid day for that cell)
  merge per-cell across sources: take the value with the lowest age;
    tie-break by source priority (lower priority wins ties).

Output files (same names + dims as the legacy single-source pipeline,
backward-compatible with all React + RN + viz_predict consumers):
  public/data/chl_1d.png             — blended freshest single value
  public/data/chl_2d.png             — 2-frame nanmean smoothing per source, then blend
  public/data/chl_3d.png             — 3-frame nanmean smoothing per source, then blend
  public/data/chl_1d_age_days.png    — per-cell age sidecar (existing)
  public/data/chl_1d_source.png      — per-cell source-id sidecar (NEW)

Auth: NASA OB.DAAC sources require EARTHDATA_TOKEN env var (Bearer token
from https://urs.earthdata.nasa.gov/profile). If unset, NASA sources are
silently skipped and we fall back to a NOAA-only blend (~4 day lag floor).
"""
from __future__ import annotations

import json
import os
import re
import time
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import requests
import xarray as xr
from PIL import Image


# ---- Common bbox + grid (sourced from pipeline/regions/) -----------------

# Bbox via the regions/ scaffold (PR-X-1). CA region snapshot matches
# fetch.py bit-for-bit; SHOULDIDIVE_REGION env var switches PNW /
# tropical when those regions are wired in PR-X-3.
try:
    from pipeline.regions import active_region
    from pipeline.lib.http import http_get
except ModuleNotFoundError:
    from regions import active_region
    from lib.http import http_get

BBOX = active_region().bbox

# Output grid — kept at the legacy ERDDAP stride-1 dims for backward compat
# with manifest.json consumers (React MapCanvas, RN MapScreen, viz_predict).
# Higher-res grid is a separate v2 task that requires updating those readers.
#
# NOTE on coastal resolution: at the current 71×87 over the CA bbox
# (11.7° × 10.2°), each cell is ~18×13 km. Coastal cells span ocean + land,
# and DINEOF gap-fill can leak synthetic chl values past the coastline. The
# land mask below (loaded from bathy.png) NaN's land-dominant cells before
# encoding so the visible heatmap stops at the shoreline. A future v2 grid
# bump (142×174 or finer) would also sharpen the nearshore signal — that's
# a separate change requiring frontend / RN / viz_predict updates first.
OUT_W, OUT_H = 71, 87

# Each canonical cell anchored at the bbox edges; lat descends top→bottom
# to match PNG row order (row 0 = lat_max).
TARGET_LATS = np.linspace(BBOX["lat_max"], BBOX["lat_min"], OUT_H)
TARGET_LNGS = np.linspace(BBOX["lng_min"], BBOX["lng_max"], OUT_W)

# Encoding (must match the chl entry in fetch.py LAYERS for downstream parity)
CHL_RANGE = (0.05, 20.0)
CHL_SCALE = "log10"
CHL_UNIT = "mg/m^3"


# ---- Storage -----------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = active_region().data_output_dir(ROOT)
CACHE_DIR = ROOT / "pipeline" / ".cache"

# How far back each NASA source will walk if today's data is missing. NASA
# NRT files are typically published within ~24h; 5 days is generous but
# matches the ~5d marine-layer cloud-cycle so we usually find a clear pass.
NASA_MAX_BACK = 7
# NOAA SCI DINEOF 2km publishes ~11 days behind today; widen so the SCI
# fallback can reach back further than the others.
NOAA_SCI_MAX_BACK = 18
# NOAA NRT DINEOF lag tracks the ~4 day floor; 7 day walk is plenty.
NOAA_NRT_MAX_BACK = 7


# ---- HTTP --------------------------------------------------------------

UA = "ShoudiDive-pipeline/0.1 (+github.com/Michaelpjob/ShoudiDive)"
HTTP_TIMEOUT = 240

# NASA OB.DAAC endpoints. file_search returns matching filenames (one per
# line when results_as_file=1); ob/getfile downloads by filename. Both
# follow redirects to urs.earthdata.nasa.gov for the OAuth Bearer dance.
NASA_OBDAAC_FILE_SEARCH = "https://oceandata.sci.gsfc.nasa.gov/api/file_search"
NASA_OBDAAC_GETFILE = "https://oceandata.sci.gsfc.nasa.gov/ob/getfile"


def _earthdata_session() -> requests.Session | None:
    """Build a requests.Session pre-armed with the EARTHDATA_TOKEN bearer.
    Returns None if the env var is unset — caller should skip NASA sources
    in that case."""
    token = os.environ.get("EARTHDATA_TOKEN")
    if not token:
        return None
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Authorization": f"Bearer {token}",
        "Accept": "*/*",
    })
    return s


def _noaa_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*"})
    return s


# ---- Source descriptor -------------------------------------------------

@dataclass
class ChlSource:
    """A single chl source. `fetcher(d) -> np.ndarray | None` returns a
    71×87 array on the canonical grid (already flipped so row 0 = lat_max)
    or None if no data for that date."""
    id: str
    label: str
    priority: int       # lower = higher trust + earlier tie-break
    max_back: int
    fetcher: Callable[[date], Optional[np.ndarray]]
    requires_earthdata: bool = False
    # A last-resort, lower-quality source (e.g. raw single-sensor vs the
    # gap-filled primaries). When the blend ends up dominated by a fallback
    # source, the manifest flags source_fallback so the UI can say "live but
    # on a backup source" instead of implying primary-quality data.
    fallback: bool = False
    # Needs Copernicus Marine creds (COPERNICUSMARINE_SERVICE_USERNAME/PASSWORD).
    # Skipped upstream when unset, so the source lies dormant until the secrets
    # exist — then it activates with no further code change.
    requires_cmems: bool = False


# ---- NASA OB.DAAC L3m fetcher ------------------------------------------

# L3m daily filename pattern, e.g. AQUA_MODIS.20260502.L3m.DAY.CHL.chlor_a.4km.NRT.nc
_NASA_FILENAME_RE = re.compile(
    r"^[A-Z][A-Z0-9_]+\.\d{8}\.L3m\.DAY\.CHL\.chlor_a\.[0-9]+km\.[A-Z]+\.nc$",
    re.IGNORECASE,
)


def _nasa_search_files(session: requests.Session, sensor: str, d: date) -> list[str]:
    """Find the L3m daily 4km NRT chl file(s) for this sensor + date.
    Returns 0 or 1 filenames (the search matches a single date)."""
    # OB.DAAC migrated its file_search API (2026): the old `subType=1` +
    # loose-wildcard `search` now 422s, silently killing all 3 NASA chl
    # primaries (verified DEAD via the feed-health probe; chl fell back to a
    # single NOAA host). The current contract wants `dtype=L3m` + a search
    # glob matching the dotted filename. Bare filenames are still returned
    # (no addurl) so _NASA_FILENAME_RE parses them unchanged.
    params = {
        "search": f"{sensor}*L3m.DAY.CHL.chlor_a.4km.NRT*",
        "sdate": d.isoformat(),
        "edate": d.isoformat(),
        "dtype": "L3m",
        "results_as_file": 1,
    }
    # Stage 6a (2026-05-24): http_get adds retries on the EARTHDATA
    # session — previously a transient 503 dropped the daily file.
    r = http_get(NASA_OBDAAC_FILE_SEARCH, params=params,
                 timeout=HTTP_TIMEOUT, session=session)
    if r is None:
        print(f"  nasa-search {sensor} {d}: all retries failed", flush=True)
        return []
    if r.status_code != 200:
        return []
    files = []
    for line in r.text.strip().split("\n"):
        line = line.strip()
        if _NASA_FILENAME_RE.match(line):
            files.append(line)
    return files


def _nasa_download(session: requests.Session, filename: str) -> Optional[Path]:
    """Download an OB.DAAC file to the local cache. Returns the local path
    on success, None on failure. Caches by filename so repeat runs no-op."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / filename
    if cache_path.exists():
        return cache_path
    url = f"{NASA_OBDAAC_GETFILE}/{filename}"
    r = http_get(url, timeout=HTTP_TIMEOUT, session=session, allow_redirects=True)
    if r is None:
        print(f"  nasa-dl {filename}: all retries failed", flush=True)
        return None
    if r.status_code != 200 or len(r.content) < 50_000:
        # < 50 KB is almost certainly the Earthdata HTML login page or a
        # JSON error wrapper, not a real netCDF.
        print(
            f"  nasa-dl {filename}: HTTP {r.status_code}, "
            f"{len(r.content)} bytes (probably auth-failed)",
            flush=True,
        )
        return None
    cache_path.write_bytes(r.content)
    return cache_path


def _open_nasa_l3m(nc_path: Path) -> Optional[np.ndarray]:
    """Open an OB.DAAC L3m daily netCDF, subset to bbox, regrid to the
    canonical 71×87 grid via bilinear interpolation. Returns array
    flipped so row 0 == lat_max (matches PNG row order). None on failure."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ds = xr.open_dataset(nc_path, engine="netcdf4")
    except Exception as exc:  # noqa: BLE001
        print(f"  nasa-open {nc_path.name}: {exc.__class__.__name__}: {exc}", flush=True)
        return None

    try:
        var = ds["chlor_a"]
    except KeyError:
        print(f"  nasa-open {nc_path.name}: no chlor_a var", flush=True)
        ds.close()
        return None

    # NASA L3m uses (lat, lon) dims with lat descending (90 → -90).
    # xarray.interp wants the source coords monotonic — flip if needed.
    lat_dim = "lat" if "lat" in var.dims else ("latitude" if "latitude" in var.dims else None)
    lon_dim = "lon" if "lon" in var.dims else ("longitude" if "longitude" in var.dims else None)
    if lat_dim is None or lon_dim is None:
        print(f"  nasa-open {nc_path.name}: unexpected dims {var.dims}", flush=True)
        ds.close()
        return None

    # Sort by ascending lat so xarray.interp accepts it.
    if var[lat_dim].values[0] > var[lat_dim].values[-1]:
        var = var.sortby(lat_dim)

    # Subset before interp — much cheaper than interpolating the global
    # grid every time.
    pad = 0.5
    var_sub = var.sel(
        {
            lat_dim: slice(BBOX["lat_min"] - pad, BBOX["lat_max"] + pad),
            lon_dim: slice(BBOX["lng_min"] - pad, BBOX["lng_max"] + pad),
        }
    )
    try:
        # TARGET_LATS is descending; xarray accepts either direction here.
        regridded = var_sub.interp(
            {lat_dim: TARGET_LATS, lon_dim: TARGET_LNGS},
            method="linear",
            kwargs={"fill_value": np.nan},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  nasa-regrid {nc_path.name}: {exc.__class__.__name__}: {exc}", flush=True)
        ds.close()
        return None

    arr = np.asarray(regridded.values).squeeze()
    ds.close()
    if arr.shape != (OUT_H, OUT_W):
        print(f"  nasa-regrid {nc_path.name}: bad shape {arr.shape}", flush=True)
        return None
    return arr


def _make_nasa_fetcher(sensor: str):
    """Closure returning a fetcher(d) -> np.ndarray | None for one sensor."""
    def fetcher(d: date) -> Optional[np.ndarray]:
        session = _earthdata_session()
        if session is None:
            return None
        files = _nasa_search_files(session, sensor, d)
        if not files:
            return None
        nc_path = _nasa_download(session, files[0])
        if nc_path is None:
            return None
        return _open_nasa_l3m(nc_path)
    return fetcher


# ---- NOAA ERDDAP fetcher (covers DINEOF NRT + DINEOF SCI 2km) -----------

def _make_noaa_erddap_fetcher(host: str, dataset: str, has_altitude: bool = True,
                              variable: str = "chlor_a"):
    """Fetcher closure for any ERDDAP griddap dataset that serves a chl
    variable in (time, [altitude,] lat, lng) shape. `variable` defaults to
    chlor_a (the NOAA DINEOF products); pass e.g. "chla" for the pfeg raw
    VIIRS dataset."""
    def fetcher(d: date) -> Optional[np.ndarray]:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        slug = dataset.replace("/", "_")
        cache_path = CACHE_DIR / f"chlblend_{slug}_{d.isoformat()}.nc"
        if not cache_path.exists():
            alt = "[0]" if has_altitude else ""
            url = (
                f"{host}/{dataset}.nc"
                f"?{variable}"
                f"[({d}T00:00:00Z):1:({d}T23:59:59Z)]"
                f"{alt}"
                f"[({BBOX['lat_min']}):1:({BBOX['lat_max']})]"
                f"[({BBOX['lng_min']}):1:({BBOX['lng_max']})]"
            )
            session = _noaa_session()
            r = http_get(url, timeout=HTTP_TIMEOUT, session=session)
            if r is None:
                print(f"  noaa-erddap {dataset} {d}: all retries failed", flush=True)
                return None
            if r.status_code != 200:
                return None
            cache_path.write_bytes(r.content)

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with xr.open_dataset(cache_path) as ds:
                    var = ds[variable]
                    if "time" in var.dims and var.sizes["time"] > 1:
                        var = var.isel(time=-1)
                    arr = np.asarray(var.values).squeeze()
        except Exception as exc:  # noqa: BLE001
            print(f"  noaa-open {cache_path.name}: {exc.__class__.__name__}", flush=True)
            return None

        if arr.ndim != 2:
            return None
        # ERDDAP returns lat ascending; flip so row 0 = lat_max.
        arr = np.flipud(arr)
        # Resample to canonical 71×87 if needed (e.g. SCI 2km is finer).
        if arr.shape != (OUT_H, OUT_W):
            arr = _resize_nan(arr, OUT_H, OUT_W)
        return arr
    return fetcher


def _resize_nan(arr: np.ndarray, target_h: int, target_w: int) -> np.ndarray:
    """Bilinear resize that respects NaN — use scipy if needed but the
    common path is xarray.interp. We build a quick coordinate frame and
    let xarray do the work."""
    h, w = arr.shape
    src_lat = np.linspace(BBOX["lat_max"], BBOX["lat_min"], h)
    src_lng = np.linspace(BBOX["lng_min"], BBOX["lng_max"], w)
    da = xr.DataArray(
        arr,
        coords={"lat": src_lat, "lng": src_lng},
        dims=("lat", "lng"),
    )
    # interp expects monotonic ascending; sort lat
    da = da.sortby("lat")
    target_lat_asc = TARGET_LATS[::-1]
    out = da.interp(lat=target_lat_asc, lng=TARGET_LNGS,
                    method="linear", kwargs={"fill_value": np.nan})
    return np.asarray(out.values)[::-1, :]  # back to descending


# ---- Copernicus Marine (CMEMS) fetcher --------------------------------

def _make_cmems_fetcher(dataset_id: str, variable: str = "CHL"):
    """Fetcher for a Copernicus Marine gridded dataset via the
    `copernicusmarine` toolbox. CMEMS (EU / Mercator Ocean — GlobColour by
    ACRI-ST) is independent infrastructure from every NOAA/NASA source, so it
    survives a US-federal-infra outage. Auth is read from the
    COPERNICUSMARINE_SERVICE_USERNAME / _PASSWORD env vars (GitHub secrets);
    the source is skipped upstream when they're unset, so this never blocks a
    run. `open_dataset` returns a lazy xarray Dataset (subset by bbox+time);
    `.values` pulls only the SoCal window."""
    def fetcher(d: date) -> Optional[np.ndarray]:
        try:
            import copernicusmarine  # optional dep — only imported when used
        except ImportError:
            print("  cmems: copernicusmarine not installed — skipping", flush=True)
            return None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ds = copernicusmarine.open_dataset(
                    dataset_id=dataset_id,
                    variables=[variable],
                    minimum_longitude=BBOX["lng_min"],
                    maximum_longitude=BBOX["lng_max"],
                    minimum_latitude=BBOX["lat_min"],
                    maximum_latitude=BBOX["lat_max"],
                    start_datetime=f"{d.isoformat()}T00:00:00",
                    end_datetime=f"{d.isoformat()}T23:59:59",
                )
                var = ds[variable]
                if "time" in var.dims:
                    var = var.isel(time=-1)
                # Orient row 0 = lat_max, col 0 = lng_min so _resize_nan's
                # implicit BBOX frame matches (CMEMS lat is usually ascending).
                lat = next((c for c in ("latitude", "lat") if c in var.dims), None)
                lon = next((c for c in ("longitude", "lon") if c in var.dims), None)
                if lat:
                    var = var.sortby(lat, ascending=False)
                if lon:
                    var = var.sortby(lon, ascending=True)
                arr = np.asarray(var.values).squeeze()
        except Exception as exc:  # noqa: BLE001
            print(f"  cmems {dataset_id} {d}: {exc.__class__.__name__}: {exc}", flush=True)
            return None
        if arr.ndim != 2 or not np.isfinite(arr).any():
            return None
        if arr.shape != (OUT_H, OUT_W):
            arr = _resize_nan(arr, OUT_H, OUT_W)
        return arr
    return fetcher


# ---- Source roster -----------------------------------------------------

CHL_SOURCES: list[ChlSource] = [
    ChlSource(
        id="aqua_modis_nrt",
        label="AQUA_MODIS NRT 4km",
        priority=1,
        max_back=NASA_MAX_BACK,
        fetcher=_make_nasa_fetcher("AQUA_MODIS"),
        requires_earthdata=True,
    ),
    ChlSource(
        id="snpp_viirs_nrt",
        label="SNPP_VIIRS NRT 4km",
        priority=2,
        max_back=NASA_MAX_BACK,
        fetcher=_make_nasa_fetcher("SNPP_VIIRS"),
        requires_earthdata=True,
    ),
    ChlSource(
        id="s3a_olci_nrt",
        label="S3A_OLCI ERR-NRT 4km",
        priority=3,
        max_back=NASA_MAX_BACK,
        fetcher=_make_nasa_fetcher("S3A_OLCI_ERRNT"),
        requires_earthdata=True,
    ),
    ChlSource(
        id="dineof_nrt_4km",
        label="NOAA DINEOF NRT 4km",
        priority=4,
        max_back=NOAA_NRT_MAX_BACK,
        fetcher=_make_noaa_erddap_fetcher(
            "https://coastwatch.noaa.gov/erddap/griddap",
            "noaacwNPPN20VIIRSDINEOFDaily",
            has_altitude=True,
        ),
    ),
    ChlSource(
        id="dineof_sci_2km",
        label="NOAA DINEOF SCI 2km",
        priority=5,
        max_back=NOAA_SCI_MAX_BACK,
        fetcher=_make_noaa_erddap_fetcher(
            "https://coastwatch.noaa.gov/erddap/griddap",
            "noaacwNPPN20S3ASCIDINEOF2kmDaily",
            has_altitude=True,
        ),
    ),
    ChlSource(
        # Independent cross-provider backstop. Every source above is NOAA or
        # NASA — they can share a US-federal-infra outage (the 2026-06 freeze).
        # Copernicus Marine (EU / Mercator Ocean — GlobColour) is a separate
        # provider + infrastructure, and its L4 product is daily gap-free
        # (space-time interpolated), 4 km, multi-sensor. Priority 6 = first of
        # the two independent backstops (gap-free beats raw on ties); dormant
        # until the COPERNICUSMARINE_* secrets exist, then activates auto.
        id="cmems_globcolour",
        label="Copernicus Marine GlobColour L4 (EU, gap-free)",
        priority=6,
        max_back=NOAA_NRT_MAX_BACK,
        fetcher=_make_cmems_fetcher("cmems_obs-oc_glo_bgc-plankton_nrt_l4-gapfree-multi-4km_P1D"),
        requires_cmems=True,
    ),
    ChlSource(
        # Truly-last-resort, no-auth fallback on a THIRD host. NASA primaries
        # (1-3) need EARTHDATA; the DINEOF primaries (4-5) live on
        # coastwatch.noaa.gov (down for days in the 2026-06 outage). This raw
        # VIIRS-SNPP product is the original Bob-Simons CoastWatch ERDDAP at
        # upwell.pfeg.noaa.gov — a distinct host, no-auth, ~2-day lag. NOT
        # gap-filled (cloudier), so priority 7 keeps it last, behind the
        # gap-free CMEMS backstop; source_fallback flags it so the UI shows
        # lower confidence. Live-but-patchy > frozen.
        id="viirs_upwell_nrt",
        label="VIIRS-SNPP chl-a NRT (raw, upwell fallback)",
        priority=7,
        max_back=NOAA_NRT_MAX_BACK,
        fetcher=_make_noaa_erddap_fetcher(
            "https://upwell.pfeg.noaa.gov/erddap/griddap",
            "erdVHNchla1day",
            has_altitude=True,   # dims (time, altitude, lat, lon)
            variable="chla",
        ),
        fallback=True,
    ),
]


# ---- Per-source age-walk -----------------------------------------------

@dataclass
class _SourceResult:
    """Result of walking one source: a stack of up to `want` valid frames
    (newest first) and matching dates. Empty stacks are filtered before
    blending."""
    source: ChlSource
    frames: list[np.ndarray] = field(default_factory=list)  # newest first
    dates: list[date] = field(default_factory=list)         # parallel to frames


# Wall-clock budget per source's age-walk. A source that stays ALIVE but
# responds slowly (NOAA DINEOF ERDDAP can crawl) would otherwise walk its full
# max_back at HTTP_TIMEOUT each and stack toward the 75-min job limit, killing
# the whole refresh before the manifest is finalized — the http breaker only
# catches transport-DEAD hosts, not slow-but-alive ones (2026-06-24/25 SST
# outage: chl walked NOAA SCI 18 dates and timed the job out before finalize).
WALK_BUDGET_S = 300.0


def _walk_source(source: ChlSource, end: date, want: int) -> _SourceResult:
    """Walk back from `end` for up to source.max_back days, collecting up
    to `want` valid (non-all-NaN) frames. The first valid frame is the
    source's freshest; subsequent frames are used for 2d/3d nanmean
    smoothing of the same source's contribution. Bounded by WALK_BUDGET_S so a
    single slow source can't consume the whole refresh's time budget."""
    res = _SourceResult(source=source)
    walk_start = time.monotonic()
    for i in range(source.max_back):
        if len(res.frames) >= want:
            break
        if time.monotonic() - walk_start > WALK_BUDGET_S:
            print(f"  {source.id}: {WALK_BUDGET_S:.0f}s walk budget spent at day -{i}; "
                  f"moving on with {len(res.frames)} frame(s)", flush=True)
            break
        d = end - timedelta(days=i)
        try:
            arr = source.fetcher(d)
        except Exception as exc:  # noqa: BLE001
            print(f"  {source.id} {d}: {exc.__class__.__name__}: {exc}", flush=True)
            continue
        if arr is None:
            continue
        if not np.isfinite(arr).any():
            # All-NaN frame — common on 100% cloudy days for single-sensor
            # sources. Doesn't help the blend but doesn't fail either.
            continue
        res.frames.append(arr)
        res.dates.append(d)
    return res


# ---- Blending ----------------------------------------------------------

def _blend_freshest(per_source: list[_SourceResult], end: date,
                    want_frames: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Walk every source's first `want_frames` frames; per cell take the
    freshest non-NaN value. Tie-break by source priority (lower priority
    int wins ties).

    Returns:
      blended  — (OUT_H, OUT_W) float, NaN where no source had data
      ages     — (OUT_H, OUT_W) uint8: days behind `end` (255 = no-data)
      sources  — (OUT_H, OUT_W) uint8: source priority that won the cell
                                       (0 = no-data sentinel)
      stats    — per-source contribution dict for the manifest
    """
    blended = np.full((OUT_H, OUT_W), np.nan, dtype=np.float32)
    ages = np.full((OUT_H, OUT_W), 255, dtype=np.uint8)
    sources = np.zeros((OUT_H, OUT_W), dtype=np.uint8)  # 0 = unset
    stats: dict[str, dict] = {}

    # For "smoothed" composites (want_frames > 1), per-source nanmean across
    # its own most-recent N frames before the cross-source blend. That keeps
    # provenance simple ("source X contributed this cell") while letting
    # 2d/3d smooth out cloud-edge noise within each sensor.
    for ps in per_source:
        if not ps.frames:
            continue
        take = ps.frames[: want_frames]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            src_grid = np.nanmean(np.stack(take), axis=0).astype(np.float32)
        # Per-source effective age = age of the FRESHEST contributing frame
        src_age = max(0, min(254, (end - ps.dates[0]).days))

        # Blend rule: take this source's value if (a) the cell is currently
        # empty OR (b) this source has lower priority (= more trusted) AND
        # the cell's existing source has the same or older age. The age
        # check enforces "freshest wins"; the priority tie-break keeps the
        # high-trust sources owning cells they cover.
        valid = np.isfinite(src_grid)
        empty = sources == 0
        replace = empty & valid
        # Same-age cells held by lower-priority sources get upgraded.
        upgrade = (
            ~empty
            & valid
            & (src_age < ages.astype(np.int16))
        )
        take_mask = replace | upgrade

        blended[take_mask] = src_grid[take_mask]
        ages[take_mask] = src_age
        sources[take_mask] = ps.source.priority

        stats[ps.source.id] = {
            "label": ps.source.label,
            "priority": ps.source.priority,
            "freshest_date": ps.dates[0].isoformat(),
            "age_days": src_age,
            "frames_used": len(take),
            "valid_cells_in_source": int(valid.sum()),
        }

    # Walk back to fill cell counts AFTER all sources processed. Each
    # source's "cells_owned" is "this priority value present in `sources`".
    for ps in per_source:
        if ps.source.id in stats:
            owned = int((sources == ps.source.priority).sum())
            stats[ps.source.id]["cells_owned"] = owned

    return blended, ages, sources, stats


# ---- Encoding helpers (mirror fetch.py) --------------------------------

def _encode_value_png(arr: np.ndarray, out_path: Path) -> None:
    """log10-encode chl into 8-bit PNG. byte 0 = no-data, 1..255 = value."""
    lo, hi = CHL_RANGE
    with np.errstate(divide="ignore", invalid="ignore"):
        scaled = (np.log10(arr) - np.log10(lo)) / (np.log10(hi) - np.log10(lo))
    valid = np.isfinite(scaled)
    px = np.zeros(arr.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def _encode_age_png(age_arr: np.ndarray, out_path: Path) -> None:
    px = np.where(age_arr == 255, 0, np.minimum(age_arr.astype(np.int16) + 1, 255)).astype(np.uint8)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def _encode_source_png(src_arr: np.ndarray, out_path: Path) -> None:
    """Source ID per cell. byte 0 = no-data sentinel, 1..N = source priority."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(src_arr, mode="L").save(out_path, optimize=True)


# ---- Land mask ---------------------------------------------------------
#
# Same pattern as fetch_wind.py: read bathy.png's alpha channel, downsample
# to the chl grid (71×87) using a box-averaged land-area fraction, then
# threshold so a cell is flagged "land" only when >70% of its area is land.
# Encoded chl gets NaN'd over flagged cells, so the published PNG has
# alpha=0 there and the frontend's coastline render isn't fighting a chl
# blob that leaks 9-18 km inland.
#
# Why this matters specifically for chl (more than for wind):
#   1. The chl product is a BLEND of 5 sources, 4 of which honor land
#      correctly (NaN-over-land L3 satellite products). But priority 4-5
#      (DINEOF NRT 4km, DINEOF SCI 2km) are GAP-FILLED products: they
#      spatially extrapolate chl values across cloud-blocked cells, and
#      their land mask is slightly different from our basemap coastline.
#      Coastal cells classified as ocean in DINEOF but land in our basemap
#      end up with valid blended chl.
#   2. The chl grid is coarse (71×87 → ~18 km wide cells). A coastal cell
#      that's 60% land but has any DINEOF gap-fill ocean value renders the
#      entire cell as chl-colored.
#   3. The visibility model reads chl_1d.png as an input. If that chl is
#      contaminated, viz predictions degrade (the chl-anomaly signal is
#      one of the model's biggest "less viz" levers).

def _load_land_mask(out_w: int, out_h: int,
                    land_threshold: float = 0.7) -> np.ndarray | None:
    """Read bathy.png and downsample to (out_h, out_w) with box-averaging.

    Returns a boolean array, True = land. None if bathy.png missing
    (graceful degradation: chl encoding continues without masking).
    """
    bathy_path = OUT_DIR / "bathy.png"
    if not bathy_path.exists():
        return None
    try:
        img = Image.open(bathy_path).convert("L")
    except Exception as e:
        print(f"  [chl-land-mask] bathy.png unreadable ({e!s}) — skipping mask",
              flush=True)
        return None
    arr = np.asarray(img)
    if arr.ndim != 2:
        return None
    src_h, src_w = arr.shape
    src_land = arr == 0  # bathy: pixel 0 = land/NaN, 1..255 = depth
    if src_h < out_h or src_w < out_w:
        # Degenerate: bathy lower-res than chl grid. NN fallback.
        yi = np.linspace(0, src_h - 1, out_h).round().astype(int)
        xi = np.linspace(0, src_w - 1, out_w).round().astype(int)
        return src_land[yi[:, None], xi[None, :]]
    is_land = np.zeros((out_h, out_w), dtype=bool)
    for i in range(out_h):
        y0 = i * src_h // out_h
        y1 = max(y0 + 1, (i + 1) * src_h // out_h)
        for j in range(out_w):
            x0 = j * src_w // out_w
            x1 = max(x0 + 1, (j + 1) * src_w // out_w)
            cell = src_land[y0:y1, x0:x1]
            land_frac = cell.mean() if cell.size else 0.0
            is_land[i, j] = land_frac > land_threshold
    return is_land


def _apply_land_mask(arr: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
    """NaN out arr where mask is True. No-op if mask is None."""
    if mask is None or mask.shape != arr.shape:
        return arr
    return np.where(mask, np.nan, arr)


# ---- Public entry point ------------------------------------------------

def build_blended_chl(end: date) -> dict | None:
    """Build the blended chl 1d/2d/3d composites + sidecars for `end`.
    Returns a manifest_layer dict (same shape as fetch.build_layer's
    return) plus extended fields for source provenance."""
    print(f"[chl] blender ending {end} — {len(CHL_SOURCES)} sources", flush=True)
    have_token = bool(os.environ.get("EARTHDATA_TOKEN"))
    if not have_token:
        print(f"  EARTHDATA_TOKEN unset — NASA OB.DAAC sources will be skipped, "
              f"falling back to NOAA-only blend (~4 day lag floor)", flush=True)
    have_cmems = bool(os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME")
                      and os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD"))
    if not have_cmems:
        print(f"  COPERNICUSMARINE_SERVICE_* unset — Copernicus Marine source "
              f"dormant (add the secrets to activate the EU backstop)", flush=True)

    # 1) Walk every source up to its max_back to gather up to 3 frames each.
    per_source: list[_SourceResult] = []
    for source in CHL_SOURCES:
        if source.requires_earthdata and not have_token:
            print(f"  [{source.id}] skipped (requires EARTHDATA_TOKEN)", flush=True)
            continue
        if source.requires_cmems and not have_cmems:
            print(f"  [{source.id}] skipped (requires COPERNICUSMARINE_SERVICE_*)", flush=True)
            continue
        res = _walk_source(source, end, want=3)
        if not res.frames:
            print(f"  [{source.id}] no valid frames in last {source.max_back}d", flush=True)
            continue
        ages_str = ", ".join(str((end - d).days) for d in res.dates)
        print(f"  [{source.id}] {len(res.frames)} frames, ages: {ages_str}d", flush=True)
        per_source.append(res)

    if not per_source:
        print("[chl] no source returned data, skipping layer", flush=True)
        return None

    # Land mask loaded once per run; reused across all 3 windows. None when
    # bathy.png is missing (first-run on a fresh region) — encoding falls
    # through to today's behavior in that case.
    land_mask = _load_land_mask(OUT_W, OUT_H)
    if land_mask is not None:
        print(f"  loaded land mask from bathy.png "
              f"({float(land_mask.mean()):.0%} land cells)", flush=True)

    # 2) Blend per cell for each window: 1d/2d/3d differ only in how many
    # within-source frames are nanmean'd before the cross-source merge.
    windows = {}
    source_stats_1d: dict = {}  # captured from the 1d blend for provenance
    for win, n_frames in [("1d", 1), ("2d", 2), ("3d", 3)]:
        blended, ages, sources, stats = _blend_freshest(per_source, end, n_frames)
        # Mask land cells BEFORE coverage/encoding. ages + sources also get
        # masked so the sidecar PNGs (chl_1d_age_days.png, chl_1d_source.png)
        # don't show land-pixel provenance either.
        blended = _apply_land_mask(blended, land_mask)
        if land_mask is not None:
            ages = np.where(land_mask, 255, ages)      # 255 = no-data sentinel
            sources = np.where(land_mask, 0, sources)  # 0 = no-data sentinel
        valid_cells = int(np.isfinite(blended).sum())
        total = OUT_H * OUT_W
        coverage = valid_cells / total
        mean_age = (
            float(ages[ages < 255].mean()) if (ages < 255).any() else 0.0
        )
        print(
            f"  blended {win}: {valid_cells}/{total} cells "
            f"({coverage:.0%}), mean age {mean_age:.1f}d, "
            f"sources used {sorted(set(int(x) for x in np.unique(sources)) - {0})}",
            flush=True,
        )

        out_value = OUT_DIR / f"chl_{win}.png"
        _encode_value_png(blended, out_value)

        win_entry = {
            "url": f"/data/chl_{win}.png",
            "dates": sorted({d.isoformat() for ps in per_source for d in ps.dates[: n_frames]}),
            "blended": True,
            "coverage_frac": round(coverage, 3),
            "mean_age_days": round(mean_age, 2),
            "sources": stats,
        }
        if win == "1d":
            source_stats_1d = stats
            age_out = OUT_DIR / "chl_1d_age_days.png"
            src_out = OUT_DIR / "chl_1d_source.png"
            _encode_age_png(ages, age_out)
            _encode_source_png(sources, src_out)
            win_entry["age_days_url"] = "/data/chl_1d_age_days.png"
            win_entry["source_url"] = "/data/chl_1d_source.png"
            win_entry["source_legend"] = {
                str(s.priority): {"id": s.id, "label": s.label}
                for s in CHL_SOURCES
            }
            print(
                f"  wrote chl_1d_source.png "
                f"({sum(1 for s in CHL_SOURCES if s.priority in {int(x) for x in np.unique(sources) if int(x) != 0})} "
                f"sources contributed)",
                flush=True,
            )

        windows[win] = win_entry

    # Honest provenance: the source that owns the most cells in the 1d blend
    # is "the source you're mostly looking at". If that's a fallback source
    # (the primaries were unavailable), flag it so confidence.js can drop the
    # score + show "via <source>". See _make_noaa_erddap_fetcher / CHL_SOURCES.
    by_id = {s.id: s for s in CHL_SOURCES}
    manifest_layer = {
        "range": list(CHL_RANGE),
        "scale": CHL_SCALE,
        "unit": CHL_UNIT,
        "grid": {"width": OUT_W, "height": OUT_H},
        "blended": True,
        "windows": windows,
    }
    owned = {sid: st.get("cells_owned", 0) for sid, st in source_stats_1d.items()}
    if owned and max(owned.values()) > 0:
        dom_id = max(owned, key=owned.get)
        dom = by_id.get(dom_id)
        if dom is not None:
            manifest_layer["source"] = dom.label
            if dom.fallback:
                manifest_layer["source_fallback"] = True
    return manifest_layer


# ---- CLI for one-off testing ------------------------------------------

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--end-date", type=date.fromisoformat, default=None)
    args = p.parse_args()
    end = args.end_date or datetime.now(timezone.utc).date()
    result = build_blended_chl(end)
    if result is None:
        raise SystemExit(1)
    print(json.dumps(
        {k: v for k, v in result.items() if k != "windows"} |
        {"windows_summary": {
            w: {kk: vv for kk, vv in d.items() if kk in {"coverage_frac", "mean_age_days"}}
            for w, d in result["windows"].items()
        }},
        indent=2,
    ))
