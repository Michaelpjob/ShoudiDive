"""Fetch 10-m wind from NOAA HRRR via NOMADS, encode speed PNG + U/V RGBA PNG.

Uses byte-range fetches against HRRR's `.idx` index files so each forecast hour
downloads ~3-5 MB of GRIB2 instead of the full ~150 MB. Three slots are written:
now (f0), +6h (f6), +24h (f24). All come from the same extended run (00/06/12/18z)
so the data is internally consistent. Cron runs hourly to pick up the freshest run.

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

# Match the existing app's bbox.
BBOX = dict(lat_min=32.4, lat_max=37.6, lng_min=-124.0, lng_max=-117.0)

NOMADS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod"
SLOTS = {"now": 0, "p6h": 6, "p24h": 24}

# Output grid (regular lat/lng over bbox). 5 km cells ≈ 144x115.
GRID_W, GRID_H = 140, 110

# Encoding ranges
SPEED_RANGE = (0.0, 50.0)   # knots
UV_RANGE = (-30.0, 30.0)    # m/s

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public" / "data"
CACHE_DIR = ROOT / "pipeline" / ".cache"

# NOMADS HTTP/2 implementation is broken; force HTTP/1.1.
SESSION = requests.Session()
SESSION.headers.update({"Accept": "*/*", "User-Agent": "shouldidive/0.1 (+github.com/Michaelpjob/ShoudiDive)"})


def _get(url, **kwargs):
    """requests.get pinned to HTTP/1.1."""
    # urllib3's HTTP/1.1 fallback works fine; just ensure we don't ask for h2.
    return SESSION.get(url, timeout=180, **kwargs)


def find_latest_extended_run() -> tuple[date, int]:
    """Latest HRRR run at 00/06/12/18z that has f48 published.

    Walks back hourly from "now" rounded down to a 6h cycle, checks if the
    f48 .idx exists. Stops at first hit. Looks back up to 36 h.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # Round down to nearest 00/06/12/18 cycle.
    cycle = (now.hour // 6) * 6
    candidate = now.replace(hour=cycle)

    for _ in range(7):  # try latest, then prev 6 cycles back (36 h)
        url = (
            f"{NOMADS}/hrrr.{candidate.strftime('%Y%m%d')}/conus/"
            f"hrrr.t{candidate.hour:02d}z.wrfsfcf48.grib2.idx"
        )
        r = SESSION.head(url, timeout=30, allow_redirects=True)
        if r.status_code == 200:
            return candidate.date(), candidate.hour
        print(f"  miss: {candidate.strftime('%Y-%m-%d %H')}z f48 not yet published")
        candidate -= timedelta(hours=6)
    raise RuntimeError("No HRRR extended run with f48 found in last 36 hours")


def fetch_wind_slice(run_date: date, run_hour: int, fcst_hour: int) -> Path:
    """Byte-range fetch UGRD+VGRD at 10 m for one HRRR forecast hour."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = f"hrrr_{run_date.strftime('%Y%m%d')}_t{run_hour:02d}z_f{fcst_hour:02d}"
    grib_path = CACHE_DIR / f"{slug}.grib2"
    if grib_path.exists():
        return grib_path

    base = (
        f"{NOMADS}/hrrr.{run_date.strftime('%Y%m%d')}/conus/"
        f"hrrr.t{run_hour:02d}z.wrfsfcf{fcst_hour:02d}.grib2"
    )
    print(f"  GET {base}.idx", flush=True)
    idx = _get(base + ".idx").text
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
        raise RuntimeError(f"UGRD/VGRD at 10 m not found in {base}.idx")

    last_idx = max(u_idx, v_idx)
    if last_idx + 1 < len(lines):
        end = int(lines[last_idx + 1].split(":")[1]) - 1
        range_header = f"bytes={min(u_start, v_start)}-{end}"
    else:
        range_header = f"bytes={min(u_start, v_start)}-"
    print(f"  range fetch {range_header}", flush=True)
    r = _get(base, headers={"Range": range_header})
    r.raise_for_status()
    grib_path.write_bytes(r.content)
    return grib_path


def open_uv(grib_path: Path):
    """Open the GRIB2 slice with cfgrib, return (lat2d, lng2d, u, v) in HRRR's native LCC grid."""
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
    u = np.asarray(ds["u10"].values)
    v = np.asarray(ds["v10"].values)
    lat2d = np.asarray(ds["latitude"].values)
    lng2d = np.asarray(ds["longitude"].values)
    # Normalize lng to -180..180
    lng2d = ((lng2d + 180.0) % 360.0) - 180.0
    return lat2d, lng2d, u, v


def regrid_to_bbox(lat2d, lng2d, u, v):
    """Nearest-neighbor regrid from HRRR's LCC 2D coords to a regular bbox grid."""
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
        raise RuntimeError("HRRR has no points within bbox padding")

    tree = cKDTree(np.column_stack([pts_lng, pts_lat]))
    lat_out = np.linspace(BBOX["lat_max"], BBOX["lat_min"], GRID_H)  # row 0 = top = lat_max
    lng_out = np.linspace(BBOX["lng_min"], BBOX["lng_max"], GRID_W)
    grid_lng, grid_lat = np.meshgrid(lng_out, lat_out)
    flat_pts = np.column_stack([grid_lng.ravel(), grid_lat.ravel()])
    dists, idxs = tree.query(flat_pts, k=1)
    u_grid = pts_u[idxs].reshape(grid_lat.shape).astype(np.float32)
    v_grid = pts_v[idxs].reshape(grid_lat.shape).astype(np.float32)
    # Mark cells whose nearest HRRR point is too far (>0.1°) as NaN — out-of-domain.
    too_far = dists.reshape(grid_lat.shape) > 0.1
    u_grid[too_far] = np.nan
    v_grid[too_far] = np.nan
    return u_grid, v_grid


def encode_speed_png(u: np.ndarray, v: np.ndarray, out_path: Path) -> None:
    speed_kt = np.sqrt(u * u + v * v) * 1.94384
    valid = np.isfinite(speed_kt)
    lo, hi = SPEED_RANGE
    scaled = (speed_kt - lo) / (hi - lo)
    px = np.zeros(speed_kt.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def encode_uv_png(u: np.ndarray, v: np.ndarray, out_path: Path) -> None:
    """RGBA: R=U byte, G=V byte (both scaled to UV_RANGE), B=0, A=valid."""
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


def main() -> None:
    run_date, run_hour = find_latest_extended_run()
    print(f"HRRR run: {run_date} t{run_hour:02d}z")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    wind_layer = {
        "speed_range": list(SPEED_RANGE),
        "uv_range": list(UV_RANGE),
        "unit": "kt",
        "grid": {"width": GRID_W, "height": GRID_H},
        "source": "NOAA HRRR",
        "windows": {},
    }

    for slot, fcst in SLOTS.items():
        grib_path = fetch_wind_slice(run_date, run_hour, fcst)
        lat2d, lng2d, u_lcc, v_lcc = open_uv(grib_path)
        u, v = regrid_to_bbox(lat2d, lng2d, u_lcc, v_lcc)
        speed_path = OUT_DIR / f"wind_speed_{slot}.png"
        uv_path = OUT_DIR / f"wind_uv_{slot}.png"
        encode_speed_png(u, v, speed_path)
        encode_uv_png(u, v, uv_path)
        valid_at = (
            datetime(run_date.year, run_date.month, run_date.day, run_hour, tzinfo=timezone.utc)
            + timedelta(hours=fcst)
        )
        wind_layer["windows"][slot] = {
            "speed_url": f"/data/wind_speed_{slot}.png",
            "uv_url": f"/data/wind_uv_{slot}.png",
            "valid_at": valid_at.isoformat().replace("+00:00", "Z"),
            "fcst_hour": fcst,
        }
        print(f"  wrote wind_{slot}  ({GRID_H}x{GRID_W})")

    # Merge into existing manifest, preserving sst/chl entries.
    manifest_path = OUT_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {
            "bbox": [BBOX["lng_min"], BBOX["lat_min"], BBOX["lng_max"], BBOX["lat_max"]],
            "layers": {},
        }
    manifest["generated_at"] = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    manifest.setdefault("layers", {})["wind"] = wind_layer
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json")


if __name__ == "__main__":
    main()
