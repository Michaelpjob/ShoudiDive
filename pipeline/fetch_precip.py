"""Fetch 7-day cumulative precipitation over the bbox via OPeNDAP.

Source: NOAA CPC US Unified Daily Precipitation V1.0 (real-time), 0.25° CONUS,
1-day latency. Hosted on PSL's THREDDS server — OPeNDAP slicing means we only
download the last 7 days × bbox subset (~10 KB), not the whole annual file.

Encoded output: public/data/precip_7d.png — 8-bit grayscale, 0=NaN, 1..255 maps
to 0..200 mm linear. 200 mm = 7.9 inches, a major-storm 7-day total.

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

BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)
GRID_W, GRID_H = 140, 110

PRECIP_RANGE_MM = (0.0, 200.0)

# CPC stores longitude in 0..360. Convert our bbox once.
CPC_LON_MIN = (BBOX["lng_min"] + 360.0) % 360.0
CPC_LON_MAX = (BBOX["lng_max"] + 360.0) % 360.0

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public" / "data"


def latest_url() -> str:
    """Pick the right annual file. RT yearly file, V1.0 versioning."""
    year = datetime.now(timezone.utc).year
    return f"https://psl.noaa.gov/thredds/dodsC/Datasets/cpc_us_precip/RT/precip.V1.0.{year}.nc"


def fetch_7day_sum() -> tuple[np.ndarray, np.ndarray, np.ndarray, list[date]]:
    """Open the CPC dataset over OPeNDAP, slice the last 7 days × bbox,
    return (sum_mm[H, W], lats, lngs, day_list)."""
    url = latest_url()
    print(f"OPeNDAP {url}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = xr.open_dataset(url)

    # Spatial subset
    sub = ds.sel(
        lat=slice(BBOX["lat_min"] - 0.3, BBOX["lat_max"] + 0.3),
        lon=slice(CPC_LON_MIN - 0.3, CPC_LON_MAX + 0.3),
    )
    # Temporal subset: last 7 valid days
    sub = sub.isel(time=slice(-7, None))
    days = [pdt(t) for t in sub.time.values]
    print(f"  using days: {days[0]} -> {days[-1]} ({len(days)} days)")

    arr = sub["precip"].values  # shape (T, lat, lon)
    arr = np.where(np.isfinite(arr), arr, 0.0)  # treat NaN as 0 (no rain)
    summed = arr.sum(axis=0)
    lats = sub.lat.values
    lngs = ((sub.lon.values + 180.0) % 360.0) - 180.0
    ds.close()

    # CPC stores lat ascending; we want row 0 = lat_max for PNG.
    if lats[0] < lats[-1]:
        lats = lats[::-1]
        summed = summed[::-1, :]
    # Sort lng ascending
    order = np.argsort(lngs)
    lngs = lngs[order]
    summed = summed[:, order]
    return summed, lats, lngs, days


def pdt(t):
    """numpy datetime64 → date."""
    return date(*[int(x) for x in str(t)[:10].split("-")])


def regrid_to_bbox(src_arr, src_lats, src_lngs):
    """Bilinear sample src_arr (lats top→bottom = lat_max→lat_min, lngs ascending)
    onto the bbox grid."""
    lat_out = np.linspace(BBOX["lat_max"], BBOX["lat_min"], GRID_H)
    lng_out = np.linspace(BBOX["lng_min"], BBOX["lng_max"], GRID_W)
    grid_lng, grid_lat = np.meshgrid(lng_out, lat_out)

    # Build per-axis fractional indices into src.
    # src_lats is descending (lat_max at row 0), src_lngs ascending.
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
    print(f"  precip 7-day: {np.nanmin(grid):.1f}–{np.nanmax(grid):.1f} mm, mean {np.nanmean(grid):.1f}")
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
        "source": "NOAA CPC US Unified Daily Precip (7-day cumulative)",
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
