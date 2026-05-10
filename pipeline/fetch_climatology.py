"""Fetch a small monthly climatology for SST + chl from ERDDAP.

The visibility model wants a "what's typical for this time of year" baseline
for both SST and chl-a so it can compute anomalies. A full multi-decade
climatology would be ideal but is wildly heavy to host. This fetcher
approximates it cheaply:

  * sst_climo.png       — mean of N MUR L4 daily slices from this calendar
                          month last year (and the year before, if available).
  * chl_climo.png       — same, for the VIIRS gap-filled chl product.
  * chl_climo_annual.png — mean of one chl slice per quarter from prior year,
                          giving a year-round baseline for log-anomaly use.

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

BBOX = dict(lat_min=31.8, lat_max=42.0, lng_min=-124.6, lng_max=-117.5)
ERDDAP_BASE = "https://coastwatch.pfeg.noaa.gov/erddap/griddap"

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public" / "data"
CACHE_DIR = ROOT / "pipeline" / ".cache"

# Match the daily fetcher's encoding so the visibility orchestrator can
# decode climo PNGs the same way it decodes today's PNGs.
SST_RANGE = (9.0, 25.0)             # linear °C
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="ignore the cache stamp")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    meta_path = OUT_DIR / "climo_meta.json"
    if not args.force and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta.get("month") == now.month and meta.get("year_built") == now.year:
            print(f"climo already current for {now.year}-{now.month:02d}, nothing to do")
            return

    # Build the monthly sample set: pull SAMPLE_DAYS from prior year, same month.
    sample_year = now.year - 1
    monthly_samples = []
    last_day_of_month = monthrange(sample_year, now.month)[1]
    for d in SAMPLE_DAYS:
        if d <= last_day_of_month:
            monthly_samples.append(date(sample_year, now.month, d))

    print(f"SST climo for {now.year}-{now.month:02d}: averaging {monthly_samples}")
    try:
        sst_mean, sst_lat, sst_lng = mean_stack(
            monthly_samples,
            "jplMURSST41", "analysed_sst", stride=2, pre_xy="",
        )
        sst_mean = kelvin_to_c(sst_mean)
        print(f"  SST climo: {np.nanmin(sst_mean):.2f}–{np.nanmax(sst_mean):.2f} °C")
        encode_linear(sst_mean, *SST_RANGE, OUT_DIR / "sst_climo.png")
    except Exception as e:
        print(f"  SST climo failed — {e!s}")

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
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "monthly_samples": [d.isoformat() for d in monthly_samples],
        "annual_samples": [d.isoformat() for d in annual_samples],
        "note": "Approximate monthly climo: prior-year same-month mean. Refreshed when calendar month changes.",
    }, indent=2))
    print("wrote climo_meta.json")


if __name__ == "__main__":
    main()
