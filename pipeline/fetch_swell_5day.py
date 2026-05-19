"""5-day hourly swell forecast → per-day × per-bucket summaries.

Mirrors fetch_wind_5day.py for waves. Pulls every published hourly step
of NOAA's gfswave (WaveWatch III packaged inside GFS) from NOMADS via
byte-range fetch against each cycle's `.idx`, so the per-hour download
is just the three variables we need (HTSGW + PERPW + DIRPW at surface)
instead of the full ~50 MB GRIB2.

Outputs in `public/data/swell/`:
  hourly/d{0..4}_h{HH}_wave.png   — RGBA Hs/Tp/Dp per Pacific-local hour
  buckets/d{0..4}_{bucket}_wave.png — per-bucket mean Hs/Tp/Dp (RGBA)
  summary.json                    — per-bucket bbox stats + day shells

Buckets (Pacific Time, DST-aware) — match the wind layer:
  pre-dawn  04–06   morning   06–10   midday    10–14
  afternoon 14–19   evening   19–21   (21–04 collapsed: "Calm overnight")

Run: python pipeline/fetch_swell_5day.py
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests
import xarray as xr
from PIL import Image
from scipy.ndimage import distance_transform_edt, gaussian_filter
from scipy.spatial import cKDTree

# Bbox via pipeline/regions/ (PR-X-1). CA / PNW / tropical switch on
# SHOULDIDIVE_REGION; default `ca` preserves today's behavior.
try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

BBOX = active_region().bbox

NOMADS_GFS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"

GRID_W, GRID_H = 140, 110

# Encoding ranges — match fetch_waves.py so the frontend can use the same
# wave PNG decoder regardless of which fetcher produced the file.
HEIGHT_RANGE_M = (0.0, 12.0)   # significant wave height (Hs)
PERIOD_RANGE_S = (0.0, 25.0)   # peak period (Tp)
# Direction (Dp): 0..360° → 0..255 byte (linear).

PT = ZoneInfo("America/Los_Angeles")

BUCKETS: list[tuple[str, int, int]] = [
    ("predawn",   4,  6),
    ("morning",   6,  10),
    ("midday",    10, 14),
    ("afternoon", 14, 19),
    ("evening",   19, 21),
]
DAY_LABELS_REL = ["Today", "+1", "+2", "+3", "+4"]
# gfswave covers the full 5-day window at a consistent ~18 km grid, so
# we don't have the HRRR→GFS confidence drop the wind layer has. Still
# stamp days so the frontend can show forecast-skill cues if useful.
CONFIDENCE_BY_DAY = ["high", "high", "high", "medium", "medium"]

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = active_region().data_output_dir(ROOT) / "swell"
HOURLY_DIR  = OUT_DIR / "hourly"
BUCKETS_DIR = OUT_DIR / "buckets"
CACHE_DIR   = ROOT / "pipeline" / ".cache"

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "*/*",
    "User-Agent": "shouldidive/0.1 (+github.com/Michaelpjob/ShoudiDive)",
})


def _head_ok(url: str) -> bool:
    """True if `url` returns 200. Tries HEAD then falls back to a
    range-limited GET so we recover from NOMADS' HEAD-throttling on
    busy days (GitHub runners get rate-limited from time to time
    even when straight curl works fine)."""
    for attempt in range(3):
        try:
            r = SESSION.head(url, timeout=30, allow_redirects=True)
            if r.status_code == 200:
                return True
            if r.status_code == 404:
                return False
        except requests.RequestException:
            pass
        # HEAD didn't decisively succeed/fail — try a 1-byte range GET.
        try:
            r = SESSION.get(url, headers={"Range": "bytes=0-0"},
                            timeout=30, allow_redirects=True)
            if r.status_code in (200, 206):
                return True
            if r.status_code == 404:
                return False
        except requests.RequestException:
            pass
    return False


# ---- Region-aware gfswave subset selection ---------------------------------
#
# NOAA publishes gfswave in geographically-narrow subsets so consumers
# don't have to download the global grid. CA + PNW use `wcoast.0p16`
# (US West Coast box). Tropical (Gulf + Caribbean + East FL) needs
# `atlocn.0p16` (Atlantic + Gulf). Without this branch, fetch_swell_5day
# silently produced empty bucket arrays for tropical — the user-visible
# symptom was a grey swell layer with no data.
def _gfswave_subset() -> str:
    try:
        from pipeline.regions import active_region
    except ModuleNotFoundError:
        from regions import active_region
    name = active_region().name
    if name == "tropical":
        return "atlocn.0p16"
    # CA + PNW (and any future West Coast region) use wcoast.
    return "wcoast.0p16"


# ---- Run discovery ----------------------------------------------------------

def _idx_url(run_date: date, run_hour: int, fhour: int) -> str:
    return (
        f"{NOMADS_GFS}/gfs.{run_date.strftime('%Y%m%d')}/{run_hour:02d}/wave/gridded/"
        f"gfswave.t{run_hour:02d}z.{_gfswave_subset()}.f{fhour:03d}.grib2.idx"
    )


def find_latest_gfswave_run_with_horizon(hours: int = 120) -> tuple[date, int]:
    """Latest gfswave cycle (00/06/12/18z) whose f{hours:03d} is published."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle = (now.hour // 6) * 6
    candidate = now.replace(hour=cycle)
    for _ in range(8):
        if _head_ok(_idx_url(candidate.date(), candidate.hour, hours)):
            return candidate.date(), candidate.hour
        print(f"  miss: gfswave {candidate.strftime('%Y-%m-%d %H')}z f{hours:03d} not yet published")
        candidate -= timedelta(hours=6)
    raise RuntimeError(f"No gfswave cycle with f{hours:03d} found in last 48 hours")


# ---- Byte-range slice fetch -------------------------------------------------

def fetch_wave_slice(run_date: date, run_hour: int, fhour: int) -> Path:
    """Pull HTSGW + PERPW + DIRPW (surface level) for a single forecast hour."""
    subset = _gfswave_subset()
    slug = f"gfswave_{subset.replace('.', '_')}_{run_date.strftime('%Y%m%d')}_t{run_hour:02d}z_f{fhour:03d}"
    grib_path = CACHE_DIR / f"{slug}.grib2"
    if grib_path.exists():
        return grib_path

    base = (
        f"{NOMADS_GFS}/gfs.{run_date.strftime('%Y%m%d')}/{run_hour:02d}/wave/gridded/"
        f"gfswave.t{run_hour:02d}z.{subset}.f{fhour:03d}.grib2"
    )
    idx = SESSION.get(base + ".idx", timeout=60).text
    lines = idx.strip().split("\n")

    starts: dict[str, tuple[int, int]] = {}
    for i, line in enumerate(lines):
        parts = line.split(":")
        if len(parts) < 5:
            continue
        var, lvl = parts[3], parts[4]
        if lvl != "surface":
            continue
        if var in ("HTSGW", "PERPW", "DIRPW"):
            starts[var] = (int(parts[1]), i)

    needed = ("HTSGW", "PERPW", "DIRPW")
    missing = [v for v in needed if v not in starts]
    if missing:
        raise RuntimeError(f"Missing variables in idx: {missing}")

    earliest_start = min(starts[v][0] for v in needed)
    latest_idx = max(starts[v][1] for v in needed)
    if latest_idx + 1 < len(lines):
        end = int(lines[latest_idx + 1].split(":")[1]) - 1
        rng = f"bytes={earliest_start}-{end}"
    else:
        rng = f"bytes={earliest_start}-"

    r = SESSION.get(base, headers={"Range": rng}, timeout=180)
    r.raise_for_status()
    grib_path.parent.mkdir(parents=True, exist_ok=True)
    grib_path.write_bytes(r.content)
    return grib_path


# ---- Open + regrid ----------------------------------------------------------

def open_wave(grib_path: Path):
    """Open one wgrib2 byte-range slice → (lat2d, lng2d, height, period, direction).
    wcoast 0.16° is regular lat/lng (1D coords)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = xr.open_dataset(
            grib_path,
            engine="cfgrib",
            backend_kwargs={
                "indexpath": "",
                "filter_by_keys": {"typeOfLevel": "surface"},
            },
        )

    height = period = direction = None
    for k in ds.data_vars:
        v = ds[k]
        sn = v.attrs.get("GRIB_shortName", "").lower()
        if sn in ("swh", "htsgw") or k.lower() in ("swh", "htsgw"):
            height = np.asarray(v.values).squeeze()
        elif sn in ("perpw", "ppt") or k.lower() in ("perpw",):
            period = np.asarray(v.values).squeeze()
        elif sn in ("dirpw", "ppd") or k.lower() in ("dirpw",):
            direction = np.asarray(v.values).squeeze()
    if height is None or period is None or direction is None:
        keys = list(ds.data_vars)
        height = np.asarray(ds[keys[0]].values).squeeze() if height is None else height
        period = np.asarray(ds[keys[1]].values).squeeze() if period is None else period
        direction = np.asarray(ds[keys[2]].values).squeeze() if direction is None else direction

    lat = np.asarray(ds["latitude"].values)
    lng = np.asarray(ds["longitude"].values)
    if lat.ndim == 1 and lng.ndim == 1:
        lng2d, lat2d = np.meshgrid(lng, lat)
    else:
        lat2d, lng2d = lat, lng
    lng2d = ((lng2d + 180.0) % 360.0) - 180.0
    return lat2d, lng2d, height, period, direction


def _fill_max_cells_for_region():
    """Per-region cap on how far fill_nearest propagates.

    CA needs the default ~40 cells (~310 km on its grid) to bridge the
    broader gfswave masked zones around Pt Conception + Channel Islands
    lee + SB/SD bays. For regions with enclosed-sea geography (Baja's
    Sea of Cortez, surrounded by a peninsula on one side + mainland on
    the other), the wide fill is actively wrong: WW3 masks the Cortez
    interior because shallow + enclosed, and a 40-cell fill bridges
    ACROSS the peninsula and paints Pacific swell values onto Cortez
    cells. User reported "swell data looks off in Cortez" — that's
    why. Drop to 5 cells (~45 km) for baja so inner-shelf gaps still
    fill but the peninsula blocks the bleed.
    """
    try:
        from pipeline.regions import active_region
    except ModuleNotFoundError:
        from regions import active_region
    name = active_region().name
    if name == "baja":
        return 5
    return 40


def fill_nearest(arr, max_cells: int | None = None):
    """Fill NaN cells with their nearest valid neighbour's value.

    gfswave wcoast 0.16° masks shallow / nearshore cells where bathymetry
    interferes with the model — the inner-shelf cells along the CA coast
    come back as NaN. Without this fill the heatmap shows a band of "no
    data" hatching all along the shore, even though the open-water cells
    a few km offshore have perfectly good values.

    `max_cells` caps how far the fill can propagate (in grid cells, ~5 km
    each). Defaults to _fill_max_cells_for_region() — see that function
    for region rationale.
    """
    if max_cells is None:
        max_cells = _fill_max_cells_for_region()
    valid = np.isfinite(arr)
    if not valid.any():
        return arr
    distances, indices = distance_transform_edt(
        ~valid, return_distances=True, return_indices=True,
    )
    filled = arr[tuple(indices)]
    if max_cells is not None:
        filled = np.where(distances > max_cells, np.nan, filled)
    return filled.astype(np.float32)


def regrid_to_bbox(lat2d, lng2d, *fields, threshold_deg=0.4):
    """Nearest-neighbor regrid of each input field to our common bbox grid."""
    pad = 0.5
    in_bbox = (
        (lat2d >= BBOX["lat_min"] - pad) & (lat2d <= BBOX["lat_max"] + pad) &
        (lng2d >= BBOX["lng_min"] - pad) & (lng2d <= BBOX["lng_max"] + pad)
    )
    pts_lat = lat2d[in_bbox].ravel()
    pts_lng = lng2d[in_bbox].ravel()
    if pts_lat.size == 0:
        raise RuntimeError("WW3: no points within bbox padding")

    tree = cKDTree(np.column_stack([pts_lng, pts_lat]))
    lat_out = np.linspace(BBOX["lat_max"], BBOX["lat_min"], GRID_H)
    lng_out = np.linspace(BBOX["lng_min"], BBOX["lng_max"], GRID_W)
    grid_lng, grid_lat = np.meshgrid(lng_out, lat_out)
    flat_pts = np.column_stack([grid_lng.ravel(), grid_lat.ravel()])
    dists, idxs = tree.query(flat_pts, k=1)
    too_far = dists.reshape(grid_lat.shape) > threshold_deg

    out = []
    for f in fields:
        pts_v = f[in_bbox].ravel()
        v_grid = pts_v[idxs].reshape(grid_lat.shape).astype(np.float32)
        v_grid[too_far] = np.nan
        out.append(v_grid)
    return tuple(out)


# ---- Wind-chop fallback for cells WW3 doesn't model ------------------------
#
# gfswave (WW3) masks shallow + enclosed bodies of water (Sea of Cortez,
# Salish Sea, harbours, …). For those cells the answer "no swell data"
# is technically right — there isn't ocean swell propagating through
# them — but operationally users still want to know "what's the local
# wind chop?". We compute a fetch-limited wave height from the HRRR/GFS
# wind already fetched by fetch_wind_5day.py earlier in the same
# workflow, using the standard SMB (Sverdrup-Munk-Bretschneider)
# fetch-limited formula:
#
#   gHs/U² = 0.283 · tanh(0.0125 · (gF/U²)^0.42)
#   gTp/U  = 7.54  · tanh(0.077  · (gF/U²)^0.25)
#
# Where U = wind speed at 10 m, F = fetch in metres, g = 9.81 m/s².
# Fetch is fixed at 50 km — Sea of Cortez is ~100 km wide so this is a
# reasonable middle-ground for inner-gulf cells. The result is a
# "wind sea" estimate (not swell), but it's what's physically there.

WIND_HOURLY_DIR = active_region().data_output_dir(ROOT) / "wind" / "hourly"
SMB_FETCH_M = 50_000.0
WIND_UV_RANGE = (-30.0, 30.0)  # must match fetch_wind_5day.py's UV_RANGE


def smb_fetch_limited(u_speed_ms: np.ndarray, fetch_m: float = SMB_FETCH_M):
    """Return (Hs in m, Tp in s) for a 2D wind-speed grid using SMB.

    Cells with very low wind (<1 m/s) return Hs=0 to avoid divide-by-zero
    and the unrealistic >0 wave height the formula would otherwise give.
    """
    g = 9.81
    u = np.maximum(u_speed_ms, 1.0)  # guard against /U² blowup
    gf_over_u2 = g * fetch_m / (u * u)
    hs = (u * u / g) * 0.283 * np.tanh(0.0125 * np.power(gf_over_u2, 0.42))
    tp = (u / g) * 7.54 * np.tanh(0.077 * np.power(gf_over_u2, 0.25))
    # Drop wind chop where the source wind is itself NaN or below the
    # noise floor — keeps land/no-data cells transparent.
    mask = (u_speed_ms < 1.0) | ~np.isfinite(u_speed_ms)
    hs = np.where(mask, np.nan, hs)
    tp = np.where(mask, np.nan, tp)
    return hs, tp


def decode_wind_uv_png(path: Path):
    """Inverse of fetch_wind_5day.encode_uv_png. Returns (U, V) in m/s
    with NaN where the source PNG marked alpha=0.

    Returns (None, None) if the wind PNG isn't on disk — caller should
    skip the wind-chop fallback in that case (gfswave-only cells stay
    NaN, same as today's behaviour).
    """
    if not path.exists():
        return None, None
    img = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float32)
    a = img[..., 3]
    valid = a > 0
    lo, hi = WIND_UV_RANGE
    u = lo + (img[..., 0] / 255.0) * (hi - lo)
    v = lo + (img[..., 1] / 255.0) * (hi - lo)
    u = np.where(valid, u, np.nan)
    v = np.where(valid, v, np.nan)
    return u, v


def _path_land_fraction(iy, ix, is_land, n_samples: int = 32):
    """For every cell (y, x), return the fraction (0..1) of samples
    along the Euclidean line from (y, x) to (iy[y, x], ix[y, x]) that
    land in a land cell.

    Used by `_blend_swell_chop` to scale the swell decay weight: full
    weight when the path is all-water, zero when it's all-land,
    smoothly interpolated in between. Prevents the binary "blocked /
    unblocked" v4 behaviour from producing its own cliff at cells
    where the geodesic path just-barely starts/stops clipping a
    peninsula tip.
    """
    h, w = is_land.shape
    yy, xx = np.indices((h, w))
    count = np.zeros((h, w), dtype=np.float32)
    steps = max(n_samples - 1, 1)
    for s in range(1, n_samples):
        f = s / n_samples
        py = (yy + (iy - yy) * f).round().astype(np.int32).clip(0, h - 1)
        px = (xx + (ix - xx) * f).round().astype(np.int32).clip(0, w - 1)
        count += is_land[py, px].astype(np.float32)
    return count / steps


def _blend_swell_chop(h_grid, p_grid, d_grid, u, v, is_land=None):
    """Pure-function blend of WW3 swell + SMB wind-sea on a 2D grid.

    Separated from `fill_with_wind_chop` (which handles the wind PNG
    I/O) so it's unit-testable on synthetic inputs. See
    pipeline/tests/test_swell_chop_blend.py.

    Algorithm:
      1. Compute SMB fetch-limited wind-sea Hs/Tp from wind UV.
      2. Compute swell-decay weight = exp(-d/decay_cells), where d is
         the grid-cell distance to the nearest WW3-valid cell.
      3. Backfill h/p/d at WW3-invalid cells from the nearest valid
         neighbour (via the indices distance_transform_edt already
         returns) — so the decay actually has something to decay,
         instead of multiplying weight×0 and producing a cliff.
      4. Zero weight at cells whose straight-line path to that nearest
         source crosses land (Baja peninsula) — keeps the Sea of
         Cortez wind-chop-dominated even with the smoother decay.
      5. Hs_combined = sqrt( (backfilled_swell * weight)^2 + windsea^2 )
      6. Period/direction: backfilled swell where weight > 0.3, else
         wind-sea.

    All three return arrays are valid (finite) wherever EITHER source
    has data — no NaN islands at the WW3 edge.
    """
    speed = np.sqrt(u * u + v * v)
    chop_hs, chop_tp = smb_fetch_limited(speed)
    chop_dir = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0

    # v3 (2026-05-18): keep decay_cells = 20 (~180 km on Baja's
    # 0.082°/cell grid) AND backfill h_swell from the nearest valid
    # WW3 cell instead of zeroing it. The previous v2 had the decay
    # comment right but the implementation wrong: `h_swell` was
    # `where(valid, h_grid, 0.0)` so beyond the boundary it was 0,
    # and `0 * weight = 0` produced a single-cell cliff regardless of
    # `decay_cells`. The user saw it as "hard line where swell goes
    # from 9 ft to 1 ft over one cell" on Vizcaíno.
    #
    # v4 (2026-05-18): also block decay across the peninsula via
    # `_paths_cross_land`. Without this, the smoother decay would
    # bleed Pacific groundswell straight through the Vizcaíno peninsula
    # into mid-Cortez (decay at 10 cells = exp(-0.5) = 0.6 → 5 m source
    # = 3 m apparent in Cortez, which is physically wrong — peninsula
    # blocks ocean swell). Southern Cabo Falso wrap still works because
    # the line-of-sight path from a south-Cortez cell to a Pacific cell
    # near the tip stays in ocean — only paths *across* land get zeroed.
    swell_valid = np.isfinite(h_grid)
    if not swell_valid.any():
        # gfswave failed everywhere — fall back to pure wind chop.
        chop_only_mask = np.isfinite(chop_hs)
        h_out = np.where(chop_only_mask, chop_hs, np.nan).astype(np.float32)
        p_out = np.where(chop_only_mask, chop_tp, np.nan).astype(np.float32)
        d_out = np.where(chop_only_mask, chop_dir, np.nan).astype(np.float32)
        return h_out, p_out, d_out

    distances, indices = distance_transform_edt(
        ~swell_valid, return_distances=True, return_indices=True,
    )
    decay_cells = 20.0
    swell_weight = np.exp(-distances / decay_cells).astype(np.float32)

    # Backfill: at each cell, use the value at the nearest WW3-valid
    # cell. Inside valid cells that's a self-map (h[indices] = h[self]).
    h_swell_nn = h_grid[tuple(indices)].astype(np.float32)
    h_swell_nn = np.where(np.isfinite(h_swell_nn), h_swell_nn, 0.0)
    p_swell_nn = p_grid[tuple(indices)].astype(np.float32)
    p_swell_nn = np.where(np.isfinite(p_swell_nn), p_swell_nn, 0.0)
    d_swell_nn = d_grid[tuple(indices)].astype(np.float32)
    d_swell_nn = np.where(np.isfinite(d_swell_nn), d_swell_nn, 0.0)

    # v5 (2026-05-18): fractional land-blocking. Previous v4 used a
    # binary cross-land mask which produced its own 1-cell cliff at
    # cells where the geodesic path just-barely clips/clears a
    # peninsula tip. Soft scaling by the land-fraction along the path
    # gives a smooth taper of swell into deep Cortez.
    if is_land is not None and is_land.shape == h_grid.shape:
        land_frac = _path_land_fraction(indices[0], indices[1], is_land)
        swell_weight = (swell_weight * (1.0 - land_frac)).astype(np.float32)

    h_wind = np.where(np.isfinite(chop_hs), chop_hs, 0.0).astype(np.float32)
    h_total = np.sqrt(
        (h_swell_nn * swell_weight) ** 2 + h_wind ** 2,
    ).astype(np.float32)

    has_any = swell_valid | np.isfinite(chop_hs)
    h_out = np.where(has_any, h_total, np.nan).astype(np.float32)

    # v5/v6: final spatial smooth over the valid Hs field. Softens
    # 1-cell-wide colormap-band crossings that read as "hard lines"
    # in the rendered heatmap, without changing the large-scale
    # gradient. NaN-aware via mask renormalisation — gaussian_filter
    # would otherwise spread NaN over the whole field.
    #
    # v6 (2026-05-18): bumped sigma 1.0 → 2.0 (~9 km → ~18 km blur)
    # after forecast-day d1/d2 buckets still showed a 2.7 ft cliff at
    # the cell just east of Cedros where the path-land-fraction
    # flipped between adjacent cells. sigma=2 smears Voronoi-boundary
    # discontinuities across enough cells that the colormap reads as
    # a continuous gradient.
    finite_mask = np.isfinite(h_out)
    if finite_mask.any():
        h_filled = np.where(finite_mask, h_out, 0.0).astype(np.float32)
        weight = finite_mask.astype(np.float32)
        h_smoothed = gaussian_filter(h_filled, sigma=2.0)
        w_smoothed = gaussian_filter(weight,  sigma=2.0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            h_renorm = np.where(w_smoothed > 0, h_smoothed / w_smoothed, np.nan)
        h_out = np.where(finite_mask, h_renorm, np.nan).astype(np.float32)

    # Period/direction: backfilled swell where weight > 0.3 (swell
    # still meaningful at ~24 cells out), else wind-sea. Old threshold
    # was 0.5 which paired with the cliff bug — at 0.5 the swell zone
    # was only ~4 cells wide, making the cliff worse.
    swell_dom = swell_weight > 0.3
    p_out = np.where(swell_dom, p_swell_nn, chop_tp).astype(np.float32)
    d_out = np.where(swell_dom, d_swell_nn, chop_dir).astype(np.float32)
    p_out = np.where(has_any, p_out, np.nan).astype(np.float32)
    d_out = np.where(has_any, d_out, np.nan).astype(np.float32)
    return h_out, p_out, d_out


def load_land_mask_for_swell_grid():
    """Resamples the region's bathy.png to the swell grid and returns a
    boolean land mask (True = land, shape (GRID_H, GRID_W)).

    bathy.png encodes ocean depth in the L channel with 0 = NaN/land,
    1..255 = linear 0..6000 m. We nearest-neighbour sample down to
    the swell grid (140×110) and treat L==0 as land.

    Returns None if bathy.png isn't on disk — caller skips the land-
    blocking step in that case (same behaviour as pre-v4).
    """
    bathy_path = active_region().data_output_dir(ROOT) / "bathy.png"
    if not bathy_path.exists():
        return None
    img = np.asarray(Image.open(bathy_path).convert("L"), dtype=np.uint8)
    src_h, src_w = img.shape
    yy, xx = np.indices((GRID_H, GRID_W))
    sy = (yy * src_h / GRID_H).astype(np.int32).clip(0, src_h - 1)
    sx = (xx * src_w / GRID_W).astype(np.int32).clip(0, src_w - 1)
    return img[sy, sx] == 0


def fill_with_wind_chop(h_grid, p_grid, d_grid, day_offset: int, hour_pt: int, is_land=None):
    """Loads the matching hourly wind PNG and delegates to _blend_swell_chop.

    If the wind PNG isn't on disk yet (refresh-wind hasn't run), return
    the input grids unchanged so the heatmap still shows native WW3 with
    no fallback — same behaviour as before the wind-chop fallback.

    `is_land`: optional (GRID_H, GRID_W) bool mask. If provided, swell
    decay is blocked across land — see `_paths_cross_land`.
    """
    wind_path = WIND_HOURLY_DIR / f"d{day_offset}_h{hour_pt:02d}_uv.png"
    u, v = decode_wind_uv_png(wind_path)
    if u is None:
        return h_grid, p_grid, d_grid
    return _blend_swell_chop(h_grid, p_grid, d_grid, u, v, is_land=is_land)


# ---- Encoding ---------------------------------------------------------------

def encode_wave_png(height_m, period_s, direction_deg, out_path: Path) -> None:
    """RGBA: R=Hs (0..12 m), G=Tp (0..25 s), B=Dp (0..360°), A=valid."""
    valid = np.isfinite(height_m) & np.isfinite(period_s) & np.isfinite(direction_deg)
    h_lo, h_hi = HEIGHT_RANGE_M
    p_lo, p_hi = PERIOD_RANGE_S

    h_byte = np.clip((height_m - h_lo) / (h_hi - h_lo), 0, 1) * 255.0
    p_byte = np.clip((period_s - p_lo) / (p_hi - p_lo), 0, 1) * 255.0
    d_byte = (np.mod(np.where(np.isfinite(direction_deg), direction_deg, 0.0), 360.0) / 360.0) * 255.0

    h, w = height_m.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0] = np.where(valid, h_byte, 0).astype(np.uint8)
    rgba[..., 1] = np.where(valid, p_byte, 0).astype(np.uint8)
    rgba[..., 2] = np.where(valid, d_byte, 0).astype(np.uint8)
    rgba[..., 3] = (valid * 255).astype(np.uint8)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(out_path, optimize=True)


# ---- Direction math (vector mean) -------------------------------------------

def vector_mean_direction(dirs_deg: np.ndarray, axis=None) -> np.ndarray:
    """Mean of direction values done by averaging unit vectors. Returns NaN
    where every input along the axis is NaN. Input/output in compass degrees
    (0=N, 90=E)."""
    rad = np.deg2rad(dirs_deg)
    sin_v = np.sin(rad)
    cos_v = np.cos(rad)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        s = np.nanmean(sin_v, axis=axis)
        c = np.nanmean(cos_v, axis=axis)
    out = (np.rad2deg(np.arctan2(s, c)) + 360.0) % 360.0
    return out


# ---- Orchestrator -----------------------------------------------------------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HOURLY_DIR.mkdir(parents=True, exist_ok=True)
    BUCKETS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 1) Latest published cycle that already has the f120 file. gfswave's
    #    cadence is loose so this might roll back to the previous cycle.
    run_d, run_h = find_latest_gfswave_run_with_horizon(120)
    cycle = datetime(run_d.year, run_d.month, run_d.day, run_h, tzinfo=timezone.utc)
    print(f"gfswave cycle: {cycle.isoformat()}  (f000..f120, wcoast 0.16°)")

    # Anchor to the *current* Pacific date, not the cycle's PT date —
    # see the matching comment in fetch_wind_5day.py for full reasoning.
    # tl;dr: cycle-anchored labels lag by ~12 h, so day 0's "Today" label
    # points at yesterday for most of the day. Filtering past hours is
    # already handled by the day_offset < 0 check below.
    anchor_pt_date = datetime.now(PT).date()
    print(f"Day anchor: {anchor_pt_date} (Pacific, today)")

    # Load region land mask once — fed into every fill_with_wind_chop
    # call to block Pacific swell from bleeding across the Baja
    # peninsula into the Sea of Cortez. Missing bathy.png is OK; the
    # blend silently skips land-blocking in that case.
    is_land = load_land_mask_for_swell_grid()
    if is_land is None:
        print("land mask: bathy.png not found — skipping land-blocked decay")
    else:
        print(f"land mask: {int(is_land.sum())} of {is_land.size} cells flagged land")

    # 2) Pull each hourly step. gfswave wcoast is published at 1-hour
    #    spacing through f120, so we just iterate. Failures are logged
    #    and the affected (day, hour) slot stays empty — bucket
    #    aggregation skips empty slots gracefully.
    hourly: dict[tuple[int, int], dict] = {}
    fetched = failed = 0
    for fhour in range(0, 121):
        valid_at_utc = cycle + timedelta(hours=fhour)
        valid_pt = valid_at_utc.astimezone(PT)
        day_offset = (valid_pt.date() - anchor_pt_date).days
        if day_offset < 0 or day_offset > 4:
            continue
        try:
            grib = fetch_wave_slice(run_d, run_h, fhour)
            la2d, ln2d, h_native, p_native, d_native = open_wave(grib)
            h_grid, p_grid, d_grid = regrid_to_bbox(la2d, ln2d, h_native, p_native, d_native)
            # Fill nearshore gaps so the heatmap reads continuous up to the
            # coast. WW3 masks the inner-shelf cells; we extrapolate from
            # the nearest valid offshore neighbour. Capped at ~60 km so we
            # don't bleed into bays / harbours where the answer is bogus.
            h_grid = fill_nearest(h_grid)
            p_grid = fill_nearest(p_grid)
            d_grid = fill_nearest(d_grid)
            # WW3 masks enclosed/shallow water (Sea of Cortez, Salish Sea).
            # Fill those cells with SMB wind-chop computed from the HRRR/GFS
            # wind PNG fetched by fetch_wind_5day.py earlier in the same
            # workflow. Better than "no data" for users actually planning a
            # day in the Cortez — what's there IS local wind chop, not swell.
            h_grid, p_grid, d_grid = fill_with_wind_chop(
                h_grid, p_grid, d_grid, day_offset, valid_pt.hour,
                is_land=is_land,
            )
            hourly[(day_offset, valid_pt.hour)] = {
                "h": h_grid, "p": p_grid, "d": d_grid,
                "valid_at": valid_at_utc,
            }
            fetched += 1
        except Exception as e:
            failed += 1
            print(f"  gfswave f{fhour:03d}: {e!s}")
    print(f"fetched {fetched}/121 forecast hours ({failed} failed); "
          f"{len(hourly)} unique (day, hour) slots stored")

    # 3) Per-hour PNGs (RGBA H/T/D).
    for (day_offset, hour_pt), entry in hourly.items():
        encode_wave_png(
            entry["h"], entry["p"], entry["d"],
            HOURLY_DIR / f"d{day_offset}_h{hour_pt:02d}_wave.png",
        )

    # 4) Aggregate to 5×5 buckets.
    bucket_summaries: list[dict] = []
    for day_offset in range(5):
        for bucket_name, h0, h1 in BUCKETS:
            hs_stack, tp_stack, dp_stack = [], [], []
            for h in range(h0, h1):
                e = hourly.get((day_offset, h))
                if e is None:
                    continue
                hs_stack.append(e["h"])
                tp_stack.append(e["p"])
                dp_stack.append(e["d"])
            if not hs_stack:
                continue
            hs_t = np.stack(hs_stack, axis=0)  # (T, H, W) m
            tp_t = np.stack(tp_stack, axis=0)  # (T, H, W) s
            dp_t = np.stack(dp_stack, axis=0)  # (T, H, W) deg

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                hs_mean_xy = np.nanmean(hs_t, axis=0)         # (H, W)
                tp_mean_xy = np.nanmean(tp_t, axis=0)
            dp_mean_xy = vector_mean_direction(dp_t, axis=0)  # (H, W) compass deg

            # bbox-aggregate stats for the day card.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                hs_per_hour = np.nanmean(hs_t, axis=(1, 2))    # (T,) m
            hs_per_hour_ft = hs_per_hour * 3.28084
            mean_hs_m = (
                float(np.nanmean(hs_per_hour))
                if np.isfinite(hs_per_hour).any() else None
            )
            min_hs_m = (
                float(np.nanmin(hs_per_hour))
                if np.isfinite(hs_per_hour).any() else None
            )
            max_hs_m = (
                float(np.nanmax(hs_per_hour))
                if np.isfinite(hs_per_hour).any() else None
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                mean_tp = (
                    float(np.nanmean(tp_t)) if np.isfinite(tp_t).any() else None
                )
            mean_dp = float(vector_mean_direction(dp_t)) if np.isfinite(dp_t).any() else None

            encode_wave_png(
                hs_mean_xy, tp_mean_xy, dp_mean_xy,
                BUCKETS_DIR / f"d{day_offset}_{bucket_name}_wave.png",
            )

            def _round(v, ndigits):
                return None if v is None else round(v, ndigits)

            bucket_summaries.append({
                "day":          day_offset,
                "bucket":       bucket_name,
                "hours":        [h for h in range(h0, h1) if (day_offset, h) in hourly],
                "mean_hs_m":    _round(mean_hs_m, 2),
                "mean_hs_ft":   _round(mean_hs_m * 3.28084 if mean_hs_m is not None else None, 1),
                "min_hs_ft":    _round(min_hs_m  * 3.28084 if min_hs_m  is not None else None, 1),
                "max_hs_ft":    _round(max_hs_m  * 3.28084 if max_hs_m  is not None else None, 1),
                "mean_tp_s":    _round(mean_tp, 1),
                "mean_dp_deg":  _round(mean_dp, 0),
                "wave_url":     f"/data/swell/buckets/d{day_offset}_{bucket_name}_wave.png",
            })
            print(
                f"  d{day_offset} {bucket_name:>9}: "
                f"Hs {mean_hs_m * 3.28084:.1f} ft  Tp {mean_tp:.1f} s  Dp {mean_dp:.0f}°  "
                f"({len(hs_stack)}/{h1 - h0} hrs)"
                if (mean_hs_m is not None and mean_tp is not None and mean_dp is not None)
                else f"  d{day_offset} {bucket_name:>9}: no data"
            )

    # 5) Per-day shells with confidence + Pacific date label.
    days = []
    for d in range(5):
        date_pt = anchor_pt_date + timedelta(days=d)
        days.append({
            "day":          d,
            "date":         date_pt.isoformat(),
            "label":        DAY_LABELS_REL[d],
            "weekday":      date_pt.strftime("%A"),
            "confidence":   CONFIDENCE_BY_DAY[d],
            "buckets":      [b for b in bucket_summaries if b["day"] == d],
            "hourly_url_template": f"/data/swell/hourly/d{d}_h{{HH}}_wave.png",
        })

    summary = {
        "generated_at": (
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        ),
        "tz":              "America/Los_Angeles",
        "anchor_date":     anchor_pt_date.isoformat(),
        "gfswave_cycle":   cycle.isoformat().replace("+00:00", "Z"),
        "buckets_def": [
            {"name": name, "start_hour_local": h0, "end_hour_local": h1}
            for name, h0, h1 in BUCKETS
        ],
        "days":            days,
        "height_range_m":  list(HEIGHT_RANGE_M),
        "period_range_s":  list(PERIOD_RANGE_S),
        "grid":            {"width": GRID_W, "height": GRID_H},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"wrote {OUT_DIR / 'summary.json'}")

    # 6) Patch top-level manifest so the frontend can discover the layer.
    manifest_path = active_region().data_output_dir(ROOT) / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {
            "bbox": [BBOX["lng_min"], BBOX["lat_min"], BBOX["lng_max"], BBOX["lat_max"]],
            "layers": {},
        }
    manifest.setdefault("layers", {})["swell5d"] = {
        "summary_url":    "/data/swell/summary.json",
        "grid":           {"width": GRID_W, "height": GRID_H},
        "height_range_m": list(HEIGHT_RANGE_M),
        "period_range_s": list(PERIOD_RANGE_S),
        "tz":             "America/Los_Angeles",
        "generated_at":   summary["generated_at"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"patched {manifest_path}")


if __name__ == "__main__":
    main()
