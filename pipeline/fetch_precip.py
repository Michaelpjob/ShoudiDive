"""Fetch 7-day cumulative precipitation over the bbox.

Source: NASA GPM IMERG Daily Late Run V07B (`GPM_3IMERGDL.07`). Replaces
the previous NOAA CPC US Unified Daily Precip product (2026-05-18) which
was CONUS-only — returned ~0 mm for any cell in baja / tropical bboxes,
silently zeroing the runoff_idx driver in viz_predict for those regions.
IMERG is 0.1° global, ~1-day latency (GPM's "Late" run), and the same
Earthdata Login token that authorizes the OB.DAAC chlorophyll fetcher
also authorizes GPM access.

Encoded output: public/data/precip_7d.png — 8-bit grayscale, 0=NaN, 1..255
maps to 0..200 mm linear. 200 mm = 7.9 inches, a major-storm 7-day total.

Auth: requires EARTHDATA_TOKEN env var. Without it, the fetcher logs and
exits 0 (preserves prior behaviour where viz_predict tolerates missing
precip). The workflow already passes EARTHDATA_TOKEN through.

Run:  python pipeline/fetch_precip.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
import xarray as xr
from PIL import Image

try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

BBOX = active_region().bbox
GRID_W, GRID_H = 140, 110

PRECIP_RANGE_MM = (0.0, 200.0)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = active_region().data_output_dir(ROOT)

# GPM IMERG Daily Late Run V07. ~1-day latency, 0.1° global resolution.
# Each daily file is a netCDF4 in (time=1, lon=3600, lat=1800) layout
# with `precipitation` variable in mm/day (daily total, already
# accumulated). Late vs Early/Final:
#   Early  ~ 4 h latency, less reliable
#   Late   ~ 1 d latency, our sweet spot
#   Final  ~ 3 mo latency, used for climatology / reprocessing only
GPM_BASE = "https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGDL.07"
GPM_FILENAME = (
    "3B-DAY-L.MS.MRG.3IMERG.{ymd}-S000000-E235959.V07B.nc4"
)
UA = "shouldidive/0.1 (+github.com/Michaelpjob/ShoudiDive)"


def _earthdata_session() -> requests.Session | None:
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


def _download_imerg_day(session: requests.Session, day: date) -> Path | None:
    """Download one daily IMERG NetCDF to a cache path, return that path
    (or None on failure). Caches per-day under pipeline/.cache/imerg so
    re-runs within the same day skip re-download."""
    cache_dir = ROOT / "pipeline" / ".cache" / "imerg"
    cache_dir.mkdir(parents=True, exist_ok=True)
    fname = GPM_FILENAME.format(ymd=day.strftime("%Y%m%d"))
    cache_path = cache_dir / fname
    if cache_path.exists() and cache_path.stat().st_size > 1_000_000:
        return cache_path
    url = f"{GPM_BASE}/{day.year}/{day.month:02d}/{fname}"
    try:
        r = session.get(url, stream=True, timeout=180, allow_redirects=True)
        if r.status_code != 200:
            print(f"  IMERG {day}: HTTP {r.status_code} ({url})")
            return None
        with open(cache_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        size_mb = cache_path.stat().st_size / 1_048_576
        print(f"  IMERG {day}: {size_mb:.1f} MB")
        return cache_path
    except Exception as e:
        print(f"  IMERG {day}: {e!s}")
        if cache_path.exists():
            cache_path.unlink()
        return None


def _open_imerg_subset(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Open one IMERG daily file, subset to bbox (+0.3° pad), return
    (precip_2d[H, W], lats[H], lngs[W]). Returns None if the file is
    unparseable."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ds = xr.open_dataset(path)
    except Exception as e:
        print(f"  IMERG open {path.name}: {e!s}")
        return None

    # IMERG V07 daily variable is `precipitation` (mm/day, daily total).
    # Older versions used `precipitationCal`; check both.
    var_name = None
    for candidate in ("precipitation", "precipitationCal"):
        if candidate in ds.variables:
            var_name = candidate
            break
    if var_name is None:
        print(f"  IMERG open {path.name}: no precipitation variable")
        ds.close()
        return None

    # Spatial subset. IMERG lat/lon coords are typically labelled `lat`/`lon`
    # in V07 daily; subset by coordinate slicing.
    try:
        sub = ds.sel(
            lat=slice(BBOX["lat_min"] - 0.3, BBOX["lat_max"] + 0.3),
            lon=slice(BBOX["lng_min"] - 0.3, BBOX["lng_max"] + 0.3),
        )
    except Exception:
        # Fallback for files that label differently (some V07 builds use
        # different conventions).
        sub = ds.sel(
            latitude=slice(BBOX["lat_min"] - 0.3, BBOX["lat_max"] + 0.3),
            longitude=slice(BBOX["lng_min"] - 0.3, BBOX["lng_max"] + 0.3),
        )

    arr = sub[var_name].values  # (time=1, lat, lon) or (time=1, lon, lat)
    arr = np.squeeze(arr)  # drop time axis
    arr = np.where(np.isfinite(arr), arr, 0.0)
    lats = sub.lat.values if "lat" in sub.coords else sub.latitude.values
    lngs = sub.lon.values if "lon" in sub.coords else sub.longitude.values
    ds.close()

    # IMERG V07 daily layout is (lon, lat) — transpose so we get (lat, lon).
    if arr.ndim == 2 and arr.shape == (len(lngs), len(lats)):
        arr = arr.T

    # Ensure row 0 = lat_max for the regrid step.
    if lats[0] < lats[-1]:
        lats = lats[::-1]
        arr = arr[::-1, :]

    # Ensure lngs ascending.
    if lngs[0] > lngs[-1]:
        lngs = lngs[::-1]
        arr = arr[:, ::-1]

    return arr.astype(np.float32), lats, lngs


def fetch_7day_sum() -> tuple[np.ndarray, np.ndarray, np.ndarray, list[date]]:
    """Download 7 daily IMERG files (last 7 valid days), sum precipitation."""
    session = _earthdata_session()
    if session is None:
        raise RuntimeError(
            "fetch_precip requires EARTHDATA_TOKEN env var — same token "
            "used for NASA OB.DAAC chlorophyll fetcher. Register at "
            "urs.earthdata.nasa.gov."
        )

    today = datetime.now(timezone.utc).date()
    sums = None
    ref_lats = None
    ref_lngs = None
    days_loaded: list[date] = []
    # GPM Late has ~1-day latency. Start from yesterday going back 7 days.
    for offset in range(1, 8):
        day = today - timedelta(days=offset)
        path = _download_imerg_day(session, day)
        if path is None:
            continue
        sub = _open_imerg_subset(path)
        if sub is None:
            continue
        precip, lats, lngs = sub
        if sums is None:
            sums = precip.copy()
            ref_lats = lats
            ref_lngs = lngs
        else:
            # All IMERG daily files should be on the same grid — just add.
            if precip.shape != sums.shape:
                print(f"  IMERG {day}: shape mismatch {precip.shape} vs {sums.shape}, skipping")
                continue
            sums = sums + precip
        days_loaded.append(day)

    if sums is None or not days_loaded:
        raise RuntimeError(
            "fetch_precip got 0 valid IMERG days — check EARTHDATA_TOKEN and "
            "GES DISC availability"
        )
    print(f"  summed {len(days_loaded)} IMERG days: {days_loaded[-1]} → {days_loaded[0]}")
    return sums, ref_lats, ref_lngs, sorted(days_loaded)


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
    try:
        summed, lats, lngs, days = fetch_7day_sum()
    except RuntimeError as e:
        print(f"fetch_precip: {e!s}", file=sys.stderr)
        # Graceful exit — viz_predict tolerates absent precip layer.
        sys.exit(0)
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
        "source": "NASA GPM IMERG Daily Late V07B (7-day cumulative)",
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
