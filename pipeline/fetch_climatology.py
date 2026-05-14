"""Fetch monthly climatology baselines for SST + chl.

The visibility model wants a "what's typical for this time of year" baseline
for both SST and chl-a so it can compute anomalies. Sources:

  * sst_climo.png         — NOAA OISST v2.1 1991-2020 monthly long-term
                            mean (TRUE 30-year normal). Single ERDDAP call
                            against NEFSC COMET, ~50KB per region.
                            Replaced the prior-year-sample hack on
                            2026-05-13 — see git log fetch_climatology.py.
  * chl_climo.png         — MODIS Aqua (erdMWchla1day) prior-year same-month
                            mean. No 30-year-normal product exists for chl,
                            so the prior-year approximation persists.
  * chl_climo_annual.png  — Mean of one chl slice per quarter from prior
                            year, giving a year-round baseline for
                            log-anomaly use.

The fetcher is idempotent and self-throttling: it stamps a tiny cache file
and only re-fetches if the stamp's calendar month differs from "now" — so
running it daily from the cron costs ~one HTTP HEAD on most days.

Output: public/data/{sst_climo,chl_climo,chl_climo_annual}.png + climo_meta.json

Run: python pipeline/fetch_climatology.py [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import requests
import xarray as xr
from PIL import Image

# Bbox via pipeline/regions/ (PR-X-1). CA / PNW / tropical switch on
# SHOULDIDIVE_REGION; default `ca` preserves today's behavior.
try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

BBOX = active_region().bbox

# 2026-05-13 — `coastwatch.pfeg.noaa.gov` started returning 403 / network-
# unreachable from GitHub Actions egress IPs. fetch.py already migrated
# off PFEG onto `coastwatch.noaa.gov` (NCEI's primary CoastWatch ERDDAP)
# which still works. chl + annual chl on this fetcher still use that
# host (the W-US MODIS Aqua archive is mirrored there); SST climo now
# uses the NEFSC COMET ERDDAP for the 1991-2020 OISST normal — see
# OISST_CLIMO_* below.
ERDDAP_BASE = "https://coastwatch.noaa.gov/erddap/griddap"

# NOAA OISST v2.1 1991-2020 monthly climatology, hosted on NEFSC
# COMET ERDDAP. Single precomputed 12-month grid (1/4° global), free,
# anonymous. Replaces the prior "average 3 prior-year-May days" hack
# that baked the 2024-2025 marine heatwave into the baseline.
#
# Dataset metadata (verified 2026-05-13 against .das):
#   climo_period:  "1991/01/01 - 2020/12/31"
#   variable:      sst (Float32 °C, "Long Term Mean Monthly Mean SST")
#   time:          12 monthly grids, index 0=Jan, 11=Dec
#   lat:           720 cells, -89.875 .. 89.875 (south→north, 0.25°)
#   lng:           1440 cells, 0.125 .. 359.875 (0-360 convention!)
OISST_CLIMO_HOST = "https://comet.nefsc.noaa.gov/erddap/griddap"
OISST_CLIMO_DATASET = "noaa_psl_55a2_880b_1f29"

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = active_region().data_output_dir(ROOT)
CACHE_DIR = ROOT / "pipeline" / ".cache"

# Match the daily fetcher's encoding so the visibility orchestrator can
# decode climo PNGs the same way it decodes today's PNGs.
#
# 2026-05-13 — region-aware override. Until today SST_RANGE was hardcoded
# (9, 25) for CA. Tropical SST_RANGE is (20, 32), so a Caribbean climo
# pixel of 28°C got CLIPPED to 25 during encode, saturating pixel 255,
# and then fetch_sst_5day.py decoded that 255 with tropical's (20, 32)
# range → 32°C. Every tropical climatology cell came out ~7°C too hot,
# the nowcast anomaly read ~-4.4°C (artifact), and the 5-day forecast
# decayed toward the false-ceiling climatology — painting 85–89°F across
# the Caribbean in May. Reading the encoding range from the active region
# closes the encode/decode mismatch.
_sst_overrides = active_region().layer_range_overrides
SST_RANGE = tuple(_sst_overrides.get("sst", (9.0, 25.0)))   # linear °C
CHL_RANGE = (0.05, 20.0)            # log10 mg/m³

# Sample days per month: we average these slices for the monthly climo.
SAMPLE_DAYS = (10, 15, 20)

# Sample dates for the chl annual mean — one mid-month per quarter.
ANNUAL_SAMPLE_MMDD = ((2, 15), (5, 15), (8, 15), (11, 15))

SESSION = requests.Session()
SESSION.headers.update({"Accept": "*/*", "User-Agent": "shouldidive/0.1"})


def erddap_nc(dataset: str, variable: str, d: date, stride: int, pre_xy: str,
              lng_360: bool = False) -> Path:
    """Download (and cache) a single ERDDAP daily slice as netCDF.

    Some datasets (the W-US MODIS archive) store longitude in 0..360°. Pass
    `lng_360=True` for those so we offset the bbox bounds before requesting.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    nc_path = CACHE_DIR / f"climo_{dataset}_{d.isoformat()}_s{stride}.nc"
    if nc_path.exists():
        return nc_path
    if lng_360:
        lng_min = (BBOX["lng_min"] + 360.0) % 360.0
        lng_max = (BBOX["lng_max"] + 360.0) % 360.0
    else:
        lng_min, lng_max = BBOX["lng_min"], BBOX["lng_max"]
    url = (
        f"{ERDDAP_BASE}/{dataset}.nc"
        f"?{variable}"
        f"[({d}T00:00:00Z):1:({d}T23:59:59Z)]"
        f"{pre_xy}"
        f"[({BBOX['lat_min']}):{stride}:({BBOX['lat_max']})]"
        f"[({lng_min}):{stride}:({lng_max})]"
    )
    print(f"  GET {dataset} {d}", flush=True)
    r = SESSION.get(url, timeout=180)
    r.raise_for_status()
    nc_path.write_bytes(r.content)
    return nc_path


def open_first_array(nc_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Open an ERDDAP netCDF and return (arr_2d, lats, lngs). None on failure."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ds = xr.open_dataset(nc_path)
    except Exception as e:
        print(f"  open failed for {nc_path.name}: {e!s}")
        return None
    # First non-coord variable wins.
    var = None
    for k in ds.data_vars:
        var = k
        break
    if var is None:
        ds.close()
        return None
    arr = np.asarray(ds[var].values).squeeze()
    if arr.ndim == 0 or not np.isfinite(arr).any():
        ds.close()
        return None
    # ERDDAP sometimes returns multiple leading axes (e.g. (T, lat, lon) when
    # MUR has 2 daily slices in the window, or (1, 1, lat, lon) for VIIRS).
    # Collapse anything before the (lat, lon) trailing pair by nan-mean.
    while arr.ndim > 2:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            arr = np.nanmean(arr, axis=0)
    if arr.ndim != 2:
        print(f"  unexpected shape {arr.shape} for {nc_path.name}, skipping")
        ds.close()
        return None
    lat = np.asarray(ds["latitude"].values) if "latitude" in ds else np.asarray(ds["lat"].values)
    lng = np.asarray(ds["longitude"].values) if "longitude" in ds else np.asarray(ds["lon"].values)
    ds.close()
    if lat.ndim != 1 or lng.ndim != 1:
        print(f"  unexpected coord shape lat={lat.shape} lng={lng.shape}, skipping")
        return None
    # Want row 0 = lat_max (south-down) for PNG row order.
    if lat[0] < lat[-1]:
        lat = lat[::-1]
        arr = arr[::-1, :]
    return arr.astype(np.float32), lat, lng


def mean_stack(samples: list[date], dataset: str, variable: str, stride: int, pre_xy: str,
               lng_360: bool = False):
    """Pull ERDDAP slices for the given dates, stack, return per-pixel mean.
    Skips dates that fail to fetch or open."""
    stacks = []
    lat_ref = lng_ref = None
    for d in samples:
        try:
            nc_path = erddap_nc(dataset, variable, d, stride, pre_xy, lng_360=lng_360)
        except Exception as e:
            print(f"  skip {d}: fetch failed — {e!s}")
            continue
        result = open_first_array(nc_path)
        if result is None:
            continue
        arr, lat, lng = result
        if lat_ref is None:
            lat_ref, lng_ref = lat, lng
            stacks.append(arr)
        elif arr.shape == stacks[0].shape:
            stacks.append(arr)
    if not stacks:
        raise RuntimeError(f"no usable {dataset} slices in {samples}")
    stack = np.stack(stacks, axis=0)
    # nan-aware mean: pixels NaN in all slices stay NaN.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        mean = np.nanmean(stack, axis=0)
    return mean, lat_ref, lng_ref


def encode_linear(arr, lo, hi, out_path):
    valid = np.isfinite(arr)
    scaled = (arr - lo) / (hi - lo)
    px = np.zeros(arr.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def encode_log10(arr, lo, hi, out_path):
    valid = np.isfinite(arr) & (arr > 0)
    log_lo, log_hi = np.log10(lo), np.log10(hi)
    px = np.zeros(arr.shape, dtype=np.uint8)
    if valid.any():
        scaled = (np.log10(arr[valid]) - log_lo) / (log_hi - log_lo)
        px[valid] = np.clip(np.round(scaled * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def kelvin_to_c(arr):
    """MUR sometimes returns Kelvin even though the variable is named SST.
    Apply only if values look like K (>100)."""
    return arr - 273.15 if np.nanmean(arr) > 100 else arr


def fetch_oisst_monthly_climo(month: int):
    """Fetch the OISST 1991-2020 long-term mean for one month, subset to bbox.

    Returns (sst_2d, lat_1d, lng_1d) — all numpy arrays. SST is °C.

    The dataset stores longitude in 0-360°; our bbox uses -180/180°.
    For bboxes that don't cross the dateline (CA/PNW/tropical all
    sit west of 0° meridian) the conversion is a simple ``+ 360``.

    Cache key: month-based. The 30-year mean never changes within
    a calendar month, so we only re-fetch when the month rolls over.
    """
    if not (1 <= month <= 12):
        raise ValueError(f"month must be 1..12, got {month}")
    time_idx = month - 1  # 0=Jan, 11=Dec

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    nc_path = CACHE_DIR / f"climo_oisst_1991-2020_m{month:02d}.nc"

    # Convert -180/180 → 0/360 for the request. Handles negative-lng
    # bboxes; dateline-crossing bboxes would need two requests stitched,
    # which none of our regions need today.
    lng_min_0360 = (BBOX["lng_min"] + 360.0) % 360.0
    lng_max_0360 = (BBOX["lng_max"] + 360.0) % 360.0
    if lng_min_0360 > lng_max_0360:
        raise NotImplementedError(
            "OISST climo: dateline-crossing bbox not yet supported "
            f"(lng_min_0360={lng_min_0360}, lng_max_0360={lng_max_0360})"
        )

    if not nc_path.exists():
        url = (
            f"{OISST_CLIMO_HOST}/{OISST_CLIMO_DATASET}.nc"
            f"?sst[{time_idx}:1:{time_idx}]"
            f"[({BBOX['lat_min']}):1:({BBOX['lat_max']})]"
            f"[({lng_min_0360}):1:({lng_max_0360})]"
        )
        print(f"  GET OISST climo month={month:02d}", flush=True)
        r = SESSION.get(url, timeout=180)
        r.raise_for_status()
        nc_path.write_bytes(r.content)

    result = open_first_array(nc_path)
    if result is None:
        raise RuntimeError(f"OISST climo netCDF unreadable: {nc_path}")
    arr, lat, lng = result
    # `open_first_array` already orients lat as south-down (row 0 = lat_max).
    # Convert longitude back to -180/180 for downstream consistency. Since
    # our bbox is fully in 0-360 western hemisphere, this is a simple shift.
    if lng[0] > 180:
        lng = lng - 360.0
    return arr, lat, lng


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="ignore the cache stamp")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    meta_path = OUT_DIR / "climo_meta.json"
    current_bbox = [BBOX["lng_min"], BBOX["lat_min"],
                    BBOX["lng_max"], BBOX["lat_max"]]
    if not args.force and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            meta = {}
        month_ok = meta.get("month") == now.month
        year_ok = meta.get("year_built") == now.year
        # 2026-05-14: also gate on bbox. Without this, a region's
        # cached climatology stays in place even after the bbox is
        # bumped (e.g. NorCal expansion), causing the same geographic
        # misregistration we just fixed in fetch_bathy.py — the
        # climo PNG covers a smaller area than the current bbox,
        # downstream consumers (fetch_visibility, fetch_sst_5day)
        # apply it as if it covered the full new bbox.
        bbox_ok = meta.get("bbox") == current_bbox
        if month_ok and year_ok and bbox_ok:
            print(f"climo already current for {now.year}-{now.month:02d} "
                  f"at bbox {current_bbox}, nothing to do")
            return
        if not bbox_ok:
            print(f"  bbox changed since last climo run — regenerating")
            print(f"    cached: {meta.get('bbox')}")
            print(f"    current: {current_bbox}")

    # Build the monthly sample set: pull SAMPLE_DAYS from prior year, same month.
    sample_year = now.year - 1
    monthly_samples = []
    last_day_of_month = monthrange(sample_year, now.month)[1]
    for d in SAMPLE_DAYS:
        if d <= last_day_of_month:
            monthly_samples.append(date(sample_year, now.month, d))

    # SST climatology: NOAA OISST v2.1, 1991-2020 30-year normal.
    # Pre-2026-05-13 this section averaged 3 prior-year same-month
    # MUR L4 daily slices. That made "climatology" track last year's
    # anomaly — exactly wrong for an anomaly baseline, and during the
    # 2024-2025 marine heatwave it spiked the tropical baseline to
    # 31.9°C in May (vs ~27°C real normal). OISST gives the proper
    # 30-year normal in a single ERDDAP call.
    sst_climo_method = "oisst_1991-2020_monthly"
    print(f"SST climo for month {now.month:02d}: OISST 1991-2020 normal")
    try:
        sst_mean, sst_lat, sst_lng = fetch_oisst_monthly_climo(now.month)
        print(f"  SST climo: {np.nanmin(sst_mean):.2f}–{np.nanmax(sst_mean):.2f} °C "
              f"(shape {sst_mean.shape})")
        encode_linear(sst_mean, *SST_RANGE, OUT_DIR / "sst_climo.png")
    except Exception as e:
        print(f"  SST climo (OISST) failed — {e!s}")
        sst_climo_method = "oisst_1991-2020_monthly_FAILED_existing_png_preserved"

    # Note: VIIRS NRT (the daily-fetcher dataset) only retains a few weeks of
    # history, so prior-year dates 404 there. For climatology we switch to
    # MODIS Aqua's long-archive product (erdMH1chla1day, 2003-present).
    # Use the W-US MODIS Aqua archive (erdMWchla1day, 2002-present, 0.0125°
    # native, longitude stored in 0..360°). VIIRS NRT only retains a few
    # weeks so prior-year dates 404 there.
    print(f"chl climo for {now.year}-{now.month:02d}: averaging {monthly_samples}")
    try:
        chl_mean, _, _ = mean_stack(
            monthly_samples,
            "erdMWchla1day", "chlorophyll",
            stride=1, pre_xy="[0]",
            lng_360=True,
        )
        print(f"  chl climo: {np.nanmin(chl_mean):.3f}–{np.nanmax(chl_mean):.3f} mg/m³")
        encode_log10(chl_mean, *CHL_RANGE, OUT_DIR / "chl_climo.png")
    except Exception as e:
        print(f"  chl climo failed — {e!s}")

    annual_samples = [date(sample_year, m, d) for m, d in ANNUAL_SAMPLE_MMDD]
    print(f"chl annual mean: averaging {annual_samples}")
    try:
        chl_annual, _, _ = mean_stack(
            annual_samples,
            "erdMWchla1day", "chlorophyll",
            stride=1, pre_xy="[0]",
            lng_360=True,
        )
        print(f"  chl annual: {np.nanmin(chl_annual):.3f}–{np.nanmax(chl_annual):.3f} mg/m³")
        encode_log10(chl_annual, *CHL_RANGE, OUT_DIR / "chl_climo_annual.png")
    except Exception as e:
        print(f"  annual mean failed — {e!s} (visibility model will fall back)")

    meta_path.write_text(json.dumps({
        "year_built": now.year,
        "month": now.month,
        # bbox: forces a regen when the region's bbox changes
        # mid-month. Downstream consumers (fetch_visibility,
        # fetch_sst_5day) assume climo covers the same bbox they
        # render over, so a bbox mismatch silently produces
        # geographic misregistration.
        "bbox": current_bbox,
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sst_climo_method": sst_climo_method,
        "sst_climo_source": "NOAA OISST v2.1 monthly LTM, 1991-2020 baseline",
        "sst_climo_dataset_id": OISST_CLIMO_DATASET,
        "chl_monthly_samples": [d.isoformat() for d in monthly_samples],
        "chl_annual_samples": [d.isoformat() for d in annual_samples],
        # Legacy keys retained for any tooling that still reads them.
        "monthly_samples": [d.isoformat() for d in monthly_samples],
        "annual_samples": [d.isoformat() for d in annual_samples],
        "note": "SST climo: NOAA OISST 1991-2020 30-year normal (monthly). "
                "chl climo: prior-year same-month MODIS Aqua mean. "
                "Refreshed when calendar month OR bbox changes.",
    }, indent=2))
    print("wrote climo_meta.json")


if __name__ == "__main__":
    main()
