"""Fetch 10-m wind from NOAA HRRR + GFS via NOMADS, encode speed PNG + U/V RGBA PNG.

Uses byte-range fetches against `.idx` index files so each forecast hour
downloads only ~3-12 MB of GRIB2 instead of the full file. Four slots are
written:

  now    HRRR f00 — most recent extended run (00/06/12/18z)
  +6h    HRRR f06 — same run
  +24h   HRRR f24 — same run
  +72h   GFS  f72 — most recent GFS run with f072 published

HRRR caps at f48 so +72h has to come from GFS (0.25° / ~25 km).

Encoded outputs in `public/data/`:
  wind_speed_{slot}.png  — 8-bit grayscale; 0=NaN, 1..255 = 0..50 knots linear
  wind_uv_{slot}.png     — RGBA; R=U, G=V scaled from -30..30 m/s; A=0 means NaN

Run:  python pipeline/fetch_wind.py
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
import xarray as xr
from PIL import Image
from scipy.spatial import cKDTree

# Bbox via pipeline/regions/ (PR-X-1). CA / PNW / tropical switch on
# SHOULDIDIVE_REGION; default `ca` preserves today's behavior.
try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

# Shared HTTP + encoder helpers (Stage 6 of the pipeline refactor).
# Same dual-import pattern as `regions` above.
try:
    from pipeline.lib.http import SESSION as _SHARED_SESSION
    from pipeline.lib.http import http_get
    from pipeline.lib.encode import encode_linear_png
except ModuleNotFoundError:
    from lib.http import SESSION as _SHARED_SESSION
    from lib.http import http_get
    from lib.encode import encode_linear_png

BBOX = active_region().bbox

NOMADS_HRRR = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod"
NOMADS_GFS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"

# Per-slot config: (source, fcst_hour). HRRR for short-range; GFS for +72h
# since HRRR maxes out at f48.
#
# 2026-05-12 — region-aware fallback. HRRR's CONUS domain ends at
# ~21°N, so the Caribbean half of the tropical bbox (10-22°N) is
# outside it. Tropical refreshes that requested HRRR got a CONUS-only
# wind PNG with the Caribbean cells NaN — visible to the user as
# "wind only over US waters." For tropical, use GFS (global 0.25°)
# for every slot instead. Lower resolution than HRRR but covers the
# full bbox uniformly. CA + PNW keep HRRR for short-range.
def _slot_sources_for_active_region():
    try:
        from pipeline.regions import active_region
    except ModuleNotFoundError:
        from regions import active_region
    if active_region().name == "tropical":
        return {"now": "gfs", "p6h": "gfs", "p24h": "gfs", "p72h": "gfs"}
    # CA + PNW are inside HRRR's CONUS domain — keep the high-res mix.
    return {"now": "hrrr", "p6h": "hrrr", "p24h": "hrrr", "p72h": "gfs"}


SLOT_SOURCES = _slot_sources_for_active_region()

# Hours from the CURRENT hour that each window targets. The forecast hour is
# derived per-run in main() (offset + age-of-run), not hardcoded, so "now"
# is valid at the current hour. Before 2026-06-16 the slots used fixed
# forecast hours (now=f00, p6h=f06, ...), which pinned "now" to the model
# cycle (00/06/12/18z) and left it lagging the real hour by up to ~8 h —
# the app showed the early-morning analysis as "now".
SLOT_OFFSET_H = {"now": 0, "p6h": 6, "p24h": 24, "p72h": 72}

# HRRR only carries f00..f48 on the extended (00/06/12/18z) runs; if a
# window's derived forecast hour exceeds this, that slot falls back to GFS.
HRRR_MAX_FHOUR = 48

# History slots: prior daily GFS f000 analyses, used by the visibility model
# to compute upwelling anomalies (5-day along-shore wind mean). HRRR isn't
# retained on NOMADS for >2 days, so we use GFS for history regardless. d-0
# is already covered by `now` above.
HISTORY_SLOTS = {
    "d-1": {"days_ago": 1},
    "d-2": {"days_ago": 2},
    "d-3": {"days_ago": 3},
    "d-4": {"days_ago": 4},
}

# Output grid (regular lat/lng over bbox). 5 km cells ≈ 144x115.
GRID_W, GRID_H = 140, 110

# Encoding ranges
SPEED_RANGE = (0.0, 50.0)   # knots
UV_RANGE = (-30.0, 30.0)    # m/s

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = active_region().data_output_dir(ROOT)
CACHE_DIR = ROOT / "pipeline" / ".cache"

# Stage 6 — was a local `requests.Session()` with a private
# "shouldidive/0.1" User-Agent. Now points at the shared
# `pipeline.lib.http.SESSION`, which carries the canonical UA
# ("shouldidive-data-pipeline/1.0 +https://shouldidive.com"). Same
# transport adapters underneath. NOMADS' HTTP/1.1 quirk is a server-
# side limitation that doesn't care which client UA we send.
SESSION = _SHARED_SESSION


def _get(url, **kwargs):
    """Byte-range / .idx fetcher used by `_fetch_uv_slice`.

    Stage 6 — was a thin wrapper around `SESSION.get(url, timeout=180,
    **kwargs)`. Now routes through `pipeline.lib.http.http_get` which
    adds exponential-backoff retries on transient transport failures
    and 5xx / 429 responses. `raise_on_failure=True` matches the
    legacy "caller will eventually `.raise_for_status()`" pattern --
    a permanent failure raises, transient ones are retried up to the
    backoff schedule.
    """
    return http_get(url, timeout=180, raise_on_failure=True, **kwargs)


# ---- HRRR run discovery -----------------------------------------------------

def find_latest_hrrr_extended_run() -> tuple[date, int]:
    """Latest HRRR run at 00/06/12/18z that has f48 published. Looks back 36 h."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle = (now.hour // 6) * 6
    candidate = now.replace(hour=cycle)
    for _ in range(7):
        url = (
            f"{NOMADS_HRRR}/hrrr.{candidate.strftime('%Y%m%d')}/conus/"
            f"hrrr.t{candidate.hour:02d}z.wrfsfcf48.grib2.idx"
        )
        if SESSION.head(url, timeout=30, allow_redirects=True).status_code == 200:
            return candidate.date(), candidate.hour
        print(f"  miss: HRRR {candidate.strftime('%Y-%m-%d %H')}z f48 not yet published")
        candidate -= timedelta(hours=6)
    raise RuntimeError("No HRRR extended run with f48 found in last 36 hours")


# ---- GFS run discovery ------------------------------------------------------

def find_latest_gfs_run_with_f072() -> tuple[date, int]:
    """Latest GFS run at 00/06/12/18z that has f072 published. Looks back 24 h.

    GFS f072 typically lands ~5 hours after the cycle starts.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle = (now.hour // 6) * 6
    candidate = now.replace(hour=cycle)
    for _ in range(5):
        url = (
            f"{NOMADS_GFS}/gfs.{candidate.strftime('%Y%m%d')}/{candidate.hour:02d}/atmos/"
            f"gfs.t{candidate.hour:02d}z.pgrb2.0p25.f072.idx"
        )
        if SESSION.head(url, timeout=30, allow_redirects=True).status_code == 200:
            return candidate.date(), candidate.hour
        print(f"  miss: GFS {candidate.strftime('%Y-%m-%d %H')}z f072 not yet published")
        candidate -= timedelta(hours=6)
    raise RuntimeError("No GFS run with f072 found in last 24 hours")


def find_gfs_f000_around(target: datetime, search_hours: int = 24) -> tuple[date, int] | None:
    """Find a published GFS run with f000 around `target`. Walks back in 6h
    increments up to `search_hours`. Returns None if nothing is published
    (NOMADS keeps roughly the last 10 days, so prior days should be fine)."""
    cycle = (target.hour // 6) * 6
    cand = target.replace(hour=cycle, minute=0, second=0, microsecond=0)
    for _ in range(search_hours // 6 + 1):
        url = (
            f"{NOMADS_GFS}/gfs.{cand.strftime('%Y%m%d')}/{cand.hour:02d}/atmos/"
            f"gfs.t{cand.hour:02d}z.pgrb2.0p25.f000.idx"
        )
        if SESSION.head(url, timeout=30, allow_redirects=True).status_code == 200:
            return cand.date(), cand.hour
        cand -= timedelta(hours=6)
    return None


# ---- Generic byte-range fetch via .idx --------------------------------------

def _fetch_uv_slice(
    source: str,
    base_url: str,
    cache_path: Path,
) -> Path:
    """Common byte-range fetch logic — works for both HRRR and GFS .idx files
    since both use the same wgrib2 index format."""
    if cache_path.exists():
        return cache_path

    print(f"  GET {base_url}.idx", flush=True)
    idx = _get(base_url + ".idx").text
    lines = idx.strip().split("\n")

    u_start = u_idx = v_start = v_idx = None
    for i, line in enumerate(lines):
        parts = line.split(":")
        if len(parts) < 5:
            continue
        var, lvl = parts[3], parts[4]
        if lvl != "10 m above ground":
            continue
        if var == "UGRD":
            u_start, u_idx = int(parts[1]), i
        elif var == "VGRD":
            v_start, v_idx = int(parts[1]), i
    if u_start is None or v_start is None:
        raise RuntimeError(f"UGRD/VGRD at 10 m not found in {base_url}.idx")

    last_idx = max(u_idx, v_idx)
    if last_idx + 1 < len(lines):
        end = int(lines[last_idx + 1].split(":")[1]) - 1
        range_header = f"bytes={min(u_start, v_start)}-{end}"
    else:
        range_header = f"bytes={min(u_start, v_start)}-"
    print(f"  range fetch {source} {range_header}", flush=True)
    r = _get(base_url, headers={"Range": range_header})
    r.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(r.content)
    return cache_path


def fetch_hrrr_slice(run_date: date, run_hour: int, fcst_hour: int) -> Path:
    slug = f"hrrr_{run_date.strftime('%Y%m%d')}_t{run_hour:02d}z_f{fcst_hour:02d}"
    base = (
        f"{NOMADS_HRRR}/hrrr.{run_date.strftime('%Y%m%d')}/conus/"
        f"hrrr.t{run_hour:02d}z.wrfsfcf{fcst_hour:02d}.grib2"
    )
    return _fetch_uv_slice("hrrr", base, CACHE_DIR / f"{slug}.grib2")


def fetch_gfs_slice(run_date: date, run_hour: int, fcst_hour: int) -> Path:
    slug = f"gfs_{run_date.strftime('%Y%m%d')}_t{run_hour:02d}z_f{fcst_hour:03d}"
    base = (
        f"{NOMADS_GFS}/gfs.{run_date.strftime('%Y%m%d')}/{run_hour:02d}/atmos/"
        f"gfs.t{run_hour:02d}z.pgrb2.0p25.f{fcst_hour:03d}"
    )
    return _fetch_uv_slice("gfs", base, CACHE_DIR / f"{slug}.grib2")


# ---- Open + regrid ----------------------------------------------------------

def open_uv(grib_path: Path):
    """Open the GRIB2 slice with cfgrib. Handles both HRRR (LCC, 2D coords)
    and GFS (regular lat/lng, 1D coords) — returns 2D lat/lng arrays either way."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = xr.open_dataset(
            grib_path,
            engine="cfgrib",
            backend_kwargs={
                "indexpath": "",
                "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10},
            },
        )
    u = np.asarray(ds["u10"].values).squeeze()
    v = np.asarray(ds["v10"].values).squeeze()
    lat = np.asarray(ds["latitude"].values)
    lng = np.asarray(ds["longitude"].values)
    if lat.ndim == 1 and lng.ndim == 1:
        # GFS: regular global grid, 1D coords. Mesh up.
        lng2d, lat2d = np.meshgrid(lng, lat)
    else:
        lat2d, lng2d = lat, lng
    # Normalize lng to -180..180
    lng2d = ((lng2d + 180.0) % 360.0) - 180.0
    return lat2d, lng2d, u, v


def regrid_to_bbox(lat2d, lng2d, u, v, source: str):
    """Nearest-neighbor regrid to our common bbox grid. Different sources
    have different cell sizes, so the 'too far' threshold is per-source."""
    pad = 0.5
    in_bbox = (
        (lat2d >= BBOX["lat_min"] - pad) & (lat2d <= BBOX["lat_max"] + pad) &
        (lng2d >= BBOX["lng_min"] - pad) & (lng2d <= BBOX["lng_max"] + pad)
    )
    pts_lat = lat2d[in_bbox].ravel()
    pts_lng = lng2d[in_bbox].ravel()
    pts_u = u[in_bbox].ravel()
    pts_v = v[in_bbox].ravel()

    if pts_lat.size == 0:
        raise RuntimeError(f"{source}: no points within bbox padding")

    tree = cKDTree(np.column_stack([pts_lng, pts_lat]))
    lat_out = np.linspace(BBOX["lat_max"], BBOX["lat_min"], GRID_H)
    lng_out = np.linspace(BBOX["lng_min"], BBOX["lng_max"], GRID_W)
    grid_lng, grid_lat = np.meshgrid(lng_out, lat_out)
    flat_pts = np.column_stack([grid_lng.ravel(), grid_lat.ravel()])
    dists, idxs = tree.query(flat_pts, k=1)
    u_grid = pts_u[idxs].reshape(grid_lat.shape).astype(np.float32)
    v_grid = pts_v[idxs].reshape(grid_lat.shape).astype(np.float32)
    # Out-of-domain threshold: HRRR is 3 km (~0.03°), GFS is 0.25°.
    threshold = 0.4 if source == "gfs" else 0.1
    too_far = dists.reshape(grid_lat.shape) > threshold
    u_grid[too_far] = np.nan
    v_grid[too_far] = np.nan
    return u_grid, v_grid


# ---- Land mask --------------------------------------------------------------
#
# HRRR / GFS publish 10 m wind over LAND too — with terrain-friction-reduced
# magnitudes and orographic distortion. If we encode those land values into
# wind_uv_*.png, they're indistinguishable from over-water wind in the PNG
# (alpha=255 either way), so:
#   1. The frontend's WindParticles bilinear sampler near the coast pulls a
#      mix of land + ocean cells → particle velocity vectors get smeared
#      landward.
#   2. The geojson land mask in WindParticles eventually respawns those
#      particles, but only AFTER they've drifted a few frames inland —
#      visible as jittery / wrong-direction streamlines at the shoreline.
#
# Fix (2026-05-14): consume the bathy.png alpha channel as a land mask and
# NaN-out u/v over land cells before encoding. bathy.py guarantees `0 = land`
# (line 18 of fetch_bathy.py). With u/v NaN over land, encode_uv_png writes
# alpha=0 there, the frontend bilinear sampler returns NaN, and the
# "!Number.isFinite(u)" respawn fires on the very first step instead of
# hunting through the coarser geojson mask.
#
# Graceful degradation: if bathy.png is missing (first-run on a region
# before fetch_bathy.py has fired) the mask returns None and wind encoding
# falls back to today's behavior. Hourly wind continues to work; the land
# crispness improves once bathy lands.

def load_land_mask(out_dir: Path, grid_w: int, grid_h: int,
                   land_threshold: float = 0.9) -> np.ndarray | None:
    """Read bathy.png and downsample to a wind-grid-resolution land mask.

    Returns a boolean numpy array where True = land, False = ocean.
    None if bathy.png is missing.

    `land_threshold` controls how aggressively coastal cells get flagged:
      0.3 = a cell is "land" if >30% of its area is land (most aggressive)
      0.5 = a cell is "land" if >50% of its area is land
      0.7 = a cell is "land" only if >70% of its area is land
      0.9 = a cell is "land" only if >90% of its area is land (default —
            essentially the pipeline mask only fires for cells fully on
            land. Coastal cells with ANY meaningful ocean fraction keep
            wind data. The visible "halo" the user saw at 0.7 was the
            colorfill going transparent for cells that were ~70%+ land
            (e.g. tight against a small island). The frontend pixel-
            resolution geojson mask + the SVG LandBasemap occlude wind
            over actual land anyway — the pipeline mask only needs to
            zero out cells that are FULLY on land so the encoded PNG
            doesn't carry meaningless HRRR-over-Sierra-Nevada values.

    The bathy.png it reads classifies pixels as land (==0) vs ocean
    (depth 1..255). Box-averaging that boolean over the (src_h/grid_h)
    × (src_w/grid_w) tile that each wind cell covers gives us the
    fraction of land per wind cell.

    2026-05-14 — first attempt at this used nearest-neighbor sampling
    (a single bathy pixel near each wind cell's center decided land vs
    ocean). With 140-wide × 11.7° CA bbox each wind cell spans ~7 km;
    every coastal cell straddles the actual coastline. NN would land
    on whichever side of the coast happened to be at the cell center,
    randomly flagging mostly-ocean coastal cells as land. The visible
    effect was a uniform ~10 km gap of "no wind data" tracing the
    entire coast — user saw wind appearing "pushed out to the left."

    Box-averaging the fraction of land per cell + thresholding gives
    physically meaningful behavior: cells that are MAJORITY ocean
    (which is what coastal nearshore cells almost always are) keep
    valid wind data. Wind streamlines now extend right up to the
    shoreline before respawning.
    """
    bathy_path = out_dir / "bathy.png"
    if not bathy_path.exists():
        return None
    try:
        img = Image.open(bathy_path).convert("L")
    except Exception as e:
        print(f"  [land-mask] open failed for {bathy_path}: {e!s} — skipping mask",
              flush=True)
        return None
    arr = np.asarray(img)
    if arr.ndim != 2:
        return None
    src_h, src_w = arr.shape
    # bathy.png encoding: pixel 0 = land (NaN), 1..255 = depth.
    src_land_bool = arr == 0
    # Compute land-area fraction for each (grid_h, grid_w) cell by
    # box-averaging the source bathy land mask. Each wind cell maps to
    # a (src_h/grid_h) x (src_w/grid_w) tile of bathy pixels; the
    # cell's land fraction is the mean of the boolean mask over that
    # tile. Falls back to NN behavior if the bathy is somehow lower
    # res than the wind grid (shouldn't happen — bathy is 1 km, wind
    # grid is 7-10 km).
    is_land = np.zeros((grid_h, grid_w), dtype=bool)
    if src_h < grid_h or src_w < grid_w:
        # Degenerate case: bathy lower-res than wind grid. Just NN.
        yi = np.linspace(0, src_h - 1, grid_h).round().astype(int)
        xi = np.linspace(0, src_w - 1, grid_w).round().astype(int)
        return src_land_bool[yi[:, None], xi[None, :]]
    for i in range(grid_h):
        y0 = i * src_h // grid_h
        y1 = max(y0 + 1, (i + 1) * src_h // grid_h)
        for j in range(grid_w):
            x0 = j * src_w // grid_w
            x1 = max(x0 + 1, (j + 1) * src_w // grid_w)
            cell = src_land_bool[y0:y1, x0:x1]
            land_frac = cell.mean() if cell.size else 0.0
            is_land[i, j] = land_frac > land_threshold
    return is_land


def apply_land_mask(u: np.ndarray, v: np.ndarray,
                    land_mask: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """Return (u, v) with land cells NaN'd out. No-op if mask is None."""
    if land_mask is None:
        return u, v
    if land_mask.shape != u.shape:
        print(f"  [land-mask] shape mismatch (mask {land_mask.shape} vs "
              f"u {u.shape}) — skipping mask", flush=True)
        return u, v
    u = np.where(land_mask, np.nan, u)
    v = np.where(land_mask, np.nan, v)
    return u, v


# ---- Encoders ---------------------------------------------------------------

def encode_speed_png(u: np.ndarray, v: np.ndarray, out_path: Path) -> None:
    """Encode wind speed (computed from u,v vectors) as a grayscale PNG.

    Stage 6 — the linear-scale PNG encoding logic moved to
    :func:`pipeline.lib.encode.encode_linear_png`. The unit conversion
    (m/s -> knots via 1.94384) and the u/v magnitude calculation stay
    in this file because they're wind-specific. Output is byte-
    identical to the prior implementation.
    """
    speed_kt = np.sqrt(u * u + v * v) * 1.94384
    encode_linear_png(speed_kt, SPEED_RANGE[0], SPEED_RANGE[1], out_path)


def encode_uv_png(u: np.ndarray, v: np.ndarray, out_path: Path) -> None:
    valid = np.isfinite(u) & np.isfinite(v)
    lo, hi = UV_RANGE
    u_clip = np.clip((u - lo) / (hi - lo), 0.0, 1.0) * 255.0
    v_clip = np.clip((v - lo) / (hi - lo), 0.0, 1.0) * 255.0
    h, w = u.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0] = u_clip.astype(np.uint8)
    rgba[..., 1] = v_clip.astype(np.uint8)
    rgba[..., 2] = 0
    rgba[..., 3] = (valid * 255).astype(np.uint8)
    Image.fromarray(rgba, mode="RGBA").save(out_path, optimize=True)


# ---- Orchestrator -----------------------------------------------------------

# ---- ECMWF IFS open-data (0.25°) — blended into HRRR/GFS to match Windy ----
#
# Windy's default layer is ECMWF; our HRRR+GFS sources run ~1-3 kt hotter at
# light coastal winds (verified 2026-06-16 vs Windy + NDBC buoys). We fetch
# ECMWF 10 m wind from ECMWF Open Data and average it per-cell with the NOAA
# source — pulling magnitudes toward ECMWF's calibration while keeping HRRR's
# coastal structure. Entirely fail-safe: any ECMWF hiccup (run not yet
# published, missing step, decode error) logs + falls back to NOAA-only.

ECMWF_BASE = "https://data.ecmwf.int/forecasts"


def find_latest_ecmwf_run() -> tuple[date, int] | None:
    """Latest ECMWF IFS 0p25 oper run (00/06/12/18z) with data published.
    Open data lags ~7-9 h behind the cycle; look back 24 h. Returns None when
    nothing is published yet — caller then ships NOAA-only."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cand = now.replace(hour=(now.hour // 6) * 6)
    for _ in range(5):
        # Probe a late step (48 h), not an early one: ECMWF open data
        # publishes steps progressively, so a run can have 6 h up but not yet
        # 24 h. Requiring 48 h means the chosen run covers our now/p6h/p24h
        # slots; if the newest cycle is still landing we fall back to the
        # previous, fully-published one.
        idx = (f"{ECMWF_BASE}/{cand:%Y%m%d}/{cand.hour:02d}z/ifs/0p25/oper/"
               f"{cand:%Y%m%d}{cand.hour:02d}0000-48h-oper-fc.index")
        try:
            if SESSION.head(idx, timeout=30, allow_redirects=True).status_code == 200:
                return cand.date(), cand.hour
        except Exception:
            pass
        cand -= timedelta(hours=6)
    return None


def _snap_ecmwf_step(step: int) -> int:
    """ECMWF open-data oper publishes 3-hourly steps to 144 h, then 6-hourly.
    Snap a requested forecast hour onto the nearest available step (≤1.5 h off
    the target — fine for a blend)."""
    step = max(0, step)
    if step <= 144:
        return int(round(step / 3.0) * 3)
    return int(round(step / 6.0) * 6)


def fetch_ecmwf_slice(run_date: date, run_hour: int, step: int) -> Path:
    """Byte-range fetch ECMWF 10u + 10v for `step` via the JSON .index
    sidecar. ECMWF's index is JSON-lines with _offset/_length (unlike the
    NOMADS wgrib2 .idx text format), so it needs its own parser."""
    slug = f"ecmwf_{run_date:%Y%m%d}_t{run_hour:02d}z_f{step:03d}"
    cache_path = CACHE_DIR / f"{slug}.grib2"
    if cache_path.exists():
        return cache_path
    stem = (f"{ECMWF_BASE}/{run_date:%Y%m%d}/{run_hour:02d}z/ifs/0p25/oper/"
            f"{run_date:%Y%m%d}{run_hour:02d}0000-{step}h-oper-fc")
    u_o = u_l = v_o = v_l = None
    for line in _get(stem + ".index").text.strip().split("\n"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("levtype") == "sfc" and e.get("param") == "10u":
            u_o, u_l = int(e["_offset"]), int(e["_length"])
        elif e.get("levtype") == "sfc" and e.get("param") == "10v":
            v_o, v_l = int(e["_offset"]), int(e["_length"])
    if u_o is None or v_o is None:
        raise RuntimeError(f"ECMWF 10u/10v not in index for step {step}h")
    # One contiguous range covering both messages (cheaper than two requests;
    # any params in the gap are complete GRIB messages cfgrib's level filter
    # ignores). ~3 MB vs the 148 MB full file.
    lo = min(u_o, v_o)
    hi = max(u_o + u_l, v_o + v_l) - 1
    r = _get(stem + ".grib2", headers={"Range": f"bytes={lo}-{hi}"})
    r.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(r.content)
    return cache_path


def open_ecmwf_uv(grib_path: Path):
    """Open an ECMWF 10 m wind slice. cfgrib may name the vars u10/v10 or
    10u/10v depending on the eccodes version, so accept either."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = xr.open_dataset(
            grib_path, engine="cfgrib",
            backend_kwargs={"indexpath": "",
                            "filter_by_keys": {"typeOfLevel": "heightAboveGround",
                                               "level": 10}},
        )
    uname = next((n for n in ("u10", "10u") if n in ds), None)
    vname = next((n for n in ("v10", "10v") if n in ds), None)
    if uname is None or vname is None:
        raise RuntimeError(f"ECMWF: no 10 m u/v in {list(ds.data_vars)}")
    u = np.asarray(ds[uname].values).squeeze()
    v = np.asarray(ds[vname].values).squeeze()
    lat = np.asarray(ds["latitude"].values)
    lng = np.asarray(ds["longitude"].values)
    if lat.ndim == 1 and lng.ndim == 1:
        lng2d, lat2d = np.meshgrid(lng, lat)
    else:
        lat2d, lng2d = lat, lng
    lng2d = ((lng2d + 180.0) % 360.0) - 180.0
    return lat2d, lng2d, u, v


def fetch_ecmwf_grid(run, target_dt):
    """ECMWF wind regridded to the bbox for `target_dt`, or (None, None) on
    any failure. `run` is (date, hour) or None."""
    if run is None:
        return None, None
    run_date, run_hour = run
    run_dt = datetime(run_date.year, run_date.month, run_date.day, run_hour,
                      tzinfo=timezone.utc)
    step = _snap_ecmwf_step(round((target_dt - run_dt).total_seconds() / 3600))
    try:
        path = fetch_ecmwf_slice(run_date, run_hour, step)
        lat2d, lng2d, u_n, v_n = open_ecmwf_uv(path)
        # ECMWF 0.25° ≈ GFS resolution; reuse the GFS out-of-domain threshold.
        return regrid_to_bbox(lat2d, lng2d, u_n, v_n, "gfs")
    except Exception as e:
        print(f"    ECMWF step {step}h: {e!s}", flush=True)
        return None, None


def _blend_uv(a, b):
    """Per-cell mean of two regridded fields; where one is NaN take the other;
    NaN only where both are NaN."""
    both = np.isfinite(a) & np.isfinite(b)
    out = np.where(both, (a + b) / 2.0, np.where(np.isfinite(a), a, b))
    return out.astype(np.float32)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Find runs lazily — only call HRRR/GFS discovery if at least one slot needs it.
    sources_needed = set(SLOT_SOURCES.values())
    if "hrrr" in sources_needed:
        sources_needed.add("gfs")  # also need GFS for the >f48 fallback below
    runs: dict[str, tuple[date, int]] = {}
    if "hrrr" in sources_needed:
        d, h = find_latest_hrrr_extended_run()
        runs["hrrr"] = (d, h)
        print(f"HRRR run: {d} t{h:02d}z")
    if "gfs" in sources_needed:
        d, h = find_latest_gfs_run_with_f072()
        runs["gfs"] = (d, h)
        print(f"GFS run:  {d} t{h:02d}z")

    # ECMWF run for the blend (None when open data isn't published yet — then
    # every slot ships NOAA-only, exactly as before this change).
    ecmwf_run = find_latest_ecmwf_run()
    if ecmwf_run is not None:
        print(f"ECMWF run: {ecmwf_run[0]} t{ecmwf_run[1]:02d}z")
    else:
        print("ECMWF run: none published — shipping NOAA-only wind")

    wind_layer = {
        "speed_range": list(SPEED_RANGE),
        "uv_range": list(UV_RANGE),
        "unit": "kt",
        "grid": {"width": GRID_W, "height": GRID_H},
        "source": "NOAA HRRR + GFS blended with ECMWF" if ecmwf_run else "NOAA HRRR + GFS",
        "windows": {},
    }

    # Load once per run; reused for every slot. None if bathy.png missing.
    land_mask = load_land_mask(OUT_DIR, GRID_W, GRID_H)
    if land_mask is not None:
        land_frac = float(land_mask.mean())
        print(f"Loaded land mask from bathy.png ({land_frac:.0%} land cells)",
              flush=True)
    else:
        print("No bathy.png yet — wind UV will include over-land HRRR/GFS "
              "values; streamlines may jitter at the coast until bathy lands",
              flush=True)

    # Buoy-anchored nowcast correction. A buoy reading is "now", so this is
    # applied to the "now" slot only (below); forecast slots keep the plain
    # blend. Fetch the buoys once here; the correction surface is built per
    # slot from that slot's blended field. Fully fail-safe: any failure
    # leaves the blend untouched.
    try:
        from pipeline.wind_buoy_correction import (
            fetch_buoy_winds, wind_correction_surface, correction_summary)
    except ModuleNotFoundError:
        from wind_buoy_correction import (
            fetch_buoy_winds, wind_correction_surface, correction_summary)
    grid_lats = np.linspace(BBOX["lat_max"], BBOX["lat_min"], GRID_H)
    grid_lngs = np.linspace(BBOX["lng_min"], BBOX["lng_max"], GRID_W)
    try:
        buoy_winds = fetch_buoy_winds()
        print(f"Buoy correction: {len(buoy_winds)} buoys reporting wind", flush=True)
    except Exception as e:
        buoy_winds = []
        print(f"Buoy correction: fetch failed ({e!s}) — staying on plain blend",
              flush=True)

    now_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for slot, source in SLOT_SOURCES.items():
        target = now_utc + timedelta(hours=SLOT_OFFSET_H[slot])
        run_date, run_hour = runs[source]
        run_dt = datetime(run_date.year, run_date.month, run_date.day, run_hour,
                          tzinfo=timezone.utc)
        fhour = max(0, round((target - run_dt).total_seconds() / 3600))
        # HRRR tops out at f48; if the derived hour runs past that (e.g. p24h
        # off an older extended run), fall back to GFS for this slot.
        if source == "hrrr" and fhour > HRRR_MAX_FHOUR:
            source = "gfs"
            run_date, run_hour = runs["gfs"]
            run_dt = datetime(run_date.year, run_date.month, run_date.day, run_hour,
                              tzinfo=timezone.utc)
            fhour = max(0, round((target - run_dt).total_seconds() / 3600))

        try:
            if source == "hrrr":
                grib_path = fetch_hrrr_slice(run_date, run_hour, fhour)
            else:
                grib_path = fetch_gfs_slice(run_date, run_hour, fhour)
            lat2d, lng2d, u_native, v_native = open_uv(grib_path)
            u, v = regrid_to_bbox(lat2d, lng2d, u_native, v_native, source)
        except Exception as e:
            # Per the correctness principle: skip the slot rather than fake it.
            print(f"  {slot}: failed — {e!s}", flush=True)
            continue

        # Blend with ECMWF (Windy's default model) where available — pulls
        # the NOAA wind toward ECMWF's calibration. Fail-safe: (None, None)
        # leaves the NOAA field untouched, so a missing/late ECMWF run just
        # reverts this slot to NOAA-only.
        slot_source = source.upper()
        e_u, e_v = fetch_ecmwf_grid(ecmwf_run, target)
        if e_u is not None:
            u = _blend_uv(u, e_u)
            v = _blend_uv(v, e_v)
            slot_source = f"{source.upper()}+ECMWF"

        # Buoy-anchor the NOWCAST: pull the blended "now" field onto the live
        # buoy obs via a kriged residual. Forecast slots keep the plain blend
        # (a buoy can't speak to a future hour). Fail-safe — any error leaves
        # the field as the blend.
        if slot == "now" and buoy_winds:
            try:
                du, dv, anchors = wind_correction_surface(
                    u_grid=u, v_grid=v, lats=grid_lats, lngs=grid_lngs,
                    buoys=buoy_winds)
                u = u + du
                v = v + dv
                wind_layer["buoy_correction"] = correction_summary(anchors)
                slot_source += "+buoy"
                print(f"  now: buoy-corrected "
                      f"({wind_layer['buoy_correction']['n_anchors_active']} anchors, "
                      f"peak |du|={float(np.abs(du).max()):.1f} m/s)", flush=True)
            except Exception as e:
                print(f"  now: buoy correction skipped — {e!s}", flush=True)

        # Mask over-land cells BEFORE encoding so the published PNG has
        # alpha=0 over land. WindParticles' "!Number.isFinite(u)" respawn
        # then fires on the very first frame a particle samples a land
        # neighbour, instead of relying on the coarser geojson mask which
        # only catches it a few frames later. Net effect: streamlines stop
        # crisply at the coast.
        u, v = apply_land_mask(u, v, land_mask)

        speed_path = OUT_DIR / f"wind_speed_{slot}.png"
        uv_path = OUT_DIR / f"wind_uv_{slot}.png"
        encode_speed_png(u, v, speed_path)
        encode_uv_png(u, v, uv_path)
        valid_at = run_dt + timedelta(hours=fhour)
        wind_layer["windows"][slot] = {
            "speed_url": f"/data/wind_speed_{slot}.png",
            "uv_url": f"/data/wind_uv_{slot}.png",
            "valid_at": valid_at.isoformat().replace("+00:00", "Z"),
            "fcst_hour": fhour,
            "source": slot_source,
        }
        print(f"  wrote wind_{slot}  source={slot_source} f{fhour:02d} "
              f"valid {valid_at.isoformat().replace('+00:00', 'Z')}")

    # ---- Daily history (prior 4 days of GFS f000) ----------------------------
    # Used by the visibility model's upwelling-anomaly feature. We use t12z as
    # the reference cycle of each prior day. Past-day analyses don't change
    # once published, so on hourly runs we skip slots whose existing PNG
    # already targets today's expected calendar date.
    print("Fetching wind history (4 prior days, GFS f000)...")
    today_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    history_layer: dict[str, dict] = {}

    existing_history: dict[str, str] = {}
    if (OUT_DIR / "manifest.json").exists():
        m = json.loads((OUT_DIR / "manifest.json").read_text())
        existing_history = (m.get("layers", {}).get("wind", {}).get("history", {}) or {})

    for slot, cfg in HISTORY_SLOTS.items():
        target = today_utc.replace(hour=12) - timedelta(days=cfg["days_ago"])
        target_date_iso = target.date().isoformat()

        # Skip if the cached slot already covers the target calendar day.
        cached = existing_history.get(slot, {}).get("valid_at", "")
        cached_path = OUT_DIR / f"wind_uv_{slot}.png"
        if cached.startswith(target_date_iso) and cached_path.exists():
            history_layer[slot] = existing_history[slot]
            print(f"  {slot}: cached for {target_date_iso}, skipping fetch")
            continue

        run = find_gfs_f000_around(target)
        if run is None:
            print(f"  {slot}: no GFS run found near {target.strftime('%Y-%m-%d %H')}z — skipping")
            continue
        run_date, run_hour = run
        try:
            grib_path = fetch_gfs_slice(run_date, run_hour, 0)
            lat2d, lng2d, u_native, v_native = open_uv(grib_path)
            u, v = regrid_to_bbox(lat2d, lng2d, u_native, v_native, "gfs")
        except Exception as e:
            print(f"  {slot}: failed — {e!s}")
            continue

        # Same land mask as the forward-looking slots above. The viz
        # model only uses these history frames to compute upwelling
        # anomalies (along-shore wind average), which is fine with
        # NaN over land — its sampler is over-water-only too.
        u, v = apply_land_mask(u, v, land_mask)

        encode_uv_png(u, v, cached_path)
        valid_at = datetime(run_date.year, run_date.month, run_date.day, run_hour, tzinfo=timezone.utc)
        history_layer[slot] = {
            "uv_url": f"/data/wind_uv_{slot}.png",
            "valid_at": valid_at.isoformat().replace("+00:00", "Z"),
            "source": "GFS",
        }
        print(f"  wrote wind_uv_{slot}  ({run_date} t{run_hour:02d}z)")
    if history_layer:
        wind_layer.setdefault("history", {}).update(history_layer)

    # Merge into existing manifest, preserving sst/chl entries.
    manifest_path = OUT_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {
            "bbox": [BBOX["lng_min"], BBOX["lat_min"], BBOX["lng_max"], BBOX["lat_max"]],
            "layers": {},
        }
    wind_layer["generated_at"] = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    manifest.setdefault("layers", {})["wind"] = wind_layer
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json")


if __name__ == "__main__":
    main()
