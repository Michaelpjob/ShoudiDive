"""Fetch 7-day cumulative precipitation over the bbox via OPeNDAP.

Source: NOAA CPC Global Unified Daily Precipitation V1.0. Replaces the
US-only `cpc_us_precip` product (2026-05-18) which returned ~0 mm for
any cell in baja / tropical bboxes, silently zeroing the runoff_idx
driver in viz_predict for those regions. The global version covers
everywhere at 0.5° (~50 km), updated daily, hosted on the same NOAA
PSL THREDDS server — no auth needed.

Earlier (2026-05-18) attempt to use NASA GPM IMERG V07B was reverted
because GES DISC's bearer-token auth requires an explicit GES DISC
application approval in the user's Earthdata Login profile (a manual
UI step we can't automate from CI). Global CPC is coarser but works
out of the box; we'll revisit IMERG once the EDL config is sorted.

Encoded output: public/data/precip_7d.png — 8-bit grayscale, 0=NaN,
1..255 maps to 0..200 mm linear. 200 mm = 7.9 inches.

Run:  python pipeline/fetch_precip.py
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import xarray as xr
from PIL import Image

try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

BBOX = active_region().bbox
GRID_W, GRID_H = 140, 110

PRECIP_RANGE_MM = (0.0, 200.0)

# CPC stores longitude in 0..360. Convert our bbox once.
CPC_LON_MIN = (BBOX["lng_min"] + 360.0) % 360.0
CPC_LON_MAX = (BBOX["lng_max"] + 360.0) % 360.0

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = active_region().data_output_dir(ROOT)


def latest_url() -> str:
    """NOAA CPC Global Unified Precipitation Analysis V1.0 — current year file.

    Global 0.5° daily. Same THREDDS server pattern we used for the
    US-only product, just with a different dataset path. No auth.
    """
    year = datetime.now(timezone.utc).year
    return (
        f"https://psl.noaa.gov/thredds/dodsC/Datasets/"
        f"cpc_global_precip/precip.{year}.nc"
    )


def pdt(t):
    """numpy datetime64 → date."""
    return date(*[int(x) for x in str(t)[:10].split("-")])


def fetch_7day_sum() -> tuple[np.ndarray, np.ndarray, np.ndarray, list[date]]:
    """Open the CPC dataset over OPeNDAP, slice the last 7 days × bbox,
    return (sum_mm[H, W], lats, lngs, day_list)."""
    url = latest_url()
    print(f"OPeNDAP {url}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = xr.open_dataset(url)

    # 2026-05-21: sortby('lat') before slicing. The CPC global precip
    # dataset stores lats in DESCENDING order (89.75 → -89.75).
    # xarray's .sel(lat=slice(lo, hi)) requires the slice direction to
    # match the coordinate's order — with `lat_min < lat_max` against
    # a descending dim, the result is an empty slice and the downstream
    # `if lats[0] < lats[-1]` raises IndexError. Surface change in
    # late May 2026 (the dataset has worked unchanged before — likely
    # an xarray version bump tightened slice-direction tolerance).
    # Sorting first normalizes both dims so the slice always works.
    if "lat" in ds.coords:
        ds = ds.sortby("lat")
    if "lon" in ds.coords:
        ds = ds.sortby("lon")

    sub = ds.sel(
        lat=slice(BBOX["lat_min"] - 0.5, BBOX["lat_max"] + 0.5),
        lon=slice(CPC_LON_MIN - 0.5, CPC_LON_MAX + 0.5),
    )
    sub = sub.isel(time=slice(-7, None))

    # Defence-in-depth: surface a clean error if the slice still came back
    # empty (e.g. NOAA renamed coords, dataset moved). The IndexError on
    # the next line is unreadable; this is.
    if sub.lat.size == 0 or sub.lon.size == 0:
        raise RuntimeError(
            f"CPC slice empty after sortby: "
            f"lat range {ds.lat.values.min():.2f}..{ds.lat.values.max():.2f}, "
            f"lon range {ds.lon.values.min():.2f}..{ds.lon.values.max():.2f}, "
            f"asked lat {BBOX['lat_min']}..{BBOX['lat_max']} lon "
            f"{CPC_LON_MIN}..{CPC_LON_MAX}. Source URL: {url}"
        )

    days = [pdt(t) for t in sub.time.values]
    print(f"  using days: {days[0]} -> {days[-1]} ({len(days)} days)")

    arr = sub["precip"].values  # (T, lat, lon)
    arr = np.where(np.isfinite(arr), arr, 0.0)
    summed = arr.sum(axis=0)
    lats = sub.lat.values
    lngs = ((sub.lon.values + 180.0) % 360.0) - 180.0
    ds.close()

    if lats[0] < lats[-1]:
        lats = lats[::-1]
        summed = summed[::-1, :]
    order = np.argsort(lngs)
    lngs = lngs[order]
    summed = summed[:, order]
    return summed.astype(np.float32), lats, lngs, days


def regrid_to_bbox(src_arr, src_lats, src_lngs):
    lat_out = np.linspace(BBOX["lat_max"], BBOX["lat_min"], GRID_H)
    lng_out = np.linspace(BBOX["lng_min"], BBOX["lng_max"], GRID_W)
    grid_lng, grid_lat = np.meshgrid(lng_out, lat_out)

    src_lat_max = src_lats.max()
    src_lat_min = src_lats.min()
    src_lng_min = src_lngs.min()
    src_lng_max = src_lngs.max()
    nlat, nlng = src_arr.shape

    fy = (src_lat_max - grid_lat) / (src_lat_max - src_lat_min) * (nlat - 1)
    fx = (grid_lng - src_lng_min) / (src_lng_max - src_lng_min) * (nlng - 1)
    fy = np.clip(fy, 0, nlat - 1)
    fx = np.clip(fx, 0, nlng - 1)
    y0 = np.floor(fy).astype(int); y1 = np.minimum(y0 + 1, nlat - 1)
    x0 = np.floor(fx).astype(int); x1 = np.minimum(x0 + 1, nlng - 1)
    ty = fy - y0; tx = fx - x0
    v00 = src_arr[y0, x0]
    v10 = src_arr[y0, x1]
    v01 = src_arr[y1, x0]
    v11 = src_arr[y1, x1]
    return v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty) + v01 * (1 - tx) * ty + v11 * tx * ty


def encode_png(arr, lo, hi, out_path):
    valid = np.isfinite(arr) & (arr >= 0)
    scaled = (arr - lo) / (hi - lo)
    px = np.zeros(arr.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summed, lats, lngs, days = fetch_7day_sum()
    grid = regrid_to_bbox(summed, lats, lngs)
    print(f"  precip 7-day: {np.nanmin(grid):.1f}-{np.nanmax(grid):.1f} mm, mean {np.nanmean(grid):.1f}")
    out_path = OUT_DIR / "precip_7d.png"
    encode_png(grid, *PRECIP_RANGE_MM, out_path)
    print(f"  wrote {out_path.name}")

    manifest_path = OUT_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {
            "bbox": [BBOX["lng_min"], BBOX["lat_min"], BBOX["lng_max"], BBOX["lat_max"]],
            "layers": {},
        }
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    manifest.setdefault("layers", {})["precip"] = {
        "range_mm": list(PRECIP_RANGE_MM),
        "grid": {"width": GRID_W, "height": GRID_H},
        "source": "NOAA CPC Global Unified Daily Precip (7-day cumulative)",
        "generated_at": generated_at,
        "windows": {
            "now": {
                "url": "/data/precip_7d.png",
                "dates": [d.isoformat() for d in days],
            },
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json")


if __name__ == "__main__":
    main()
