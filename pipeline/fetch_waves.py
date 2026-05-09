"""Fetch NOAA WaveWatch III (gfswave) wcoast 0.16° grid via NOMADS.
Writes two PNGs: one for "now" conditions and one for the 3-day max envelope
that the visibility model uses to estimate bottom-stir.

The wcoast grid covers the eastern Pacific at ~18 km native resolution, which
is plenty for our coastal bbox. We pull just HTSGW/PERPW/DIRPW at f000 ('now')
via byte-range against the .idx file — typical fetch is <100 KB per cycle.

Encoded outputs in public/data/:
  wave_now.png   (RGBA) — latest run, f000
    R = significant wave height,   linear 0..12 m
    G = primary peak period,       linear 0..25 s
    B = primary peak direction,    linear 0..360°  (0..255 maps to 0..359°)
    A = 0 means missing (over land or out of grid); 255 = valid

  wave_max_3d.png (RGBA) — per-pixel max H/T over the latest run + 3 prior
                            t00z analyses (covers ~last 3 days of swell)
    R = Hmax (m), G = Tmax (s), B = 0 (unused), A = valid mask

Run: python pipeline/fetch_waves.py
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

BBOX = dict(lat_min=31.8, lat_max=42.0, lng_min=-124.0, lng_max=-116.8)
NOMADS_GFS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"

# Output grid (matches the wind / viz grid for consistency).
GRID_W, GRID_H = 140, 110

# Encoding ranges
HEIGHT_RANGE_M = (0.0, 12.0)
PERIOD_RANGE_S = (0.0, 25.0)
# Direction stays 0..360 mapped to 0..255 byte (linear).

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public" / "data"
CACHE_DIR = ROOT / "pipeline" / ".cache"

SESSION = requests.Session()
SESSION.headers.update({"Accept": "*/*", "User-Agent": "shouldidive/0.1 (+github.com/Michaelpjob/ShoudiDive)"})


def _get(url, **kwargs):
    return SESSION.get(url, timeout=180, **kwargs)


def _idx_url(run_date: date, run_hour: int) -> str:
    return (
        f"{NOMADS_GFS}/gfs.{run_date.strftime('%Y%m%d')}/{run_hour:02d}/wave/gridded/"
        f"gfswave.t{run_hour:02d}z.wcoast.0p16.f000.grib2.idx"
    )


def find_latest_gfswave_run() -> tuple[date, int]:
    """Latest GFS cycle (00/06/12/18z) whose gfswave wcoast f000 is published."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle = (now.hour // 6) * 6
    candidate = now.replace(hour=cycle)
    for _ in range(5):
        url = _idx_url(candidate.date(), candidate.hour)
        if SESSION.head(url, timeout=30, allow_redirects=True).status_code == 200:
            return candidate.date(), candidate.hour
        print(f"  miss: gfswave {candidate.strftime('%Y-%m-%d %H')}z f000 not yet published")
        candidate -= timedelta(hours=6)
    raise RuntimeError("No gfswave wcoast f000 found in last 24 hours")


def find_history_runs(latest_date: date, latest_hour: int, n_days: int = 4) -> list[tuple[date, int]]:
    """Return up to n_days run handles (date, hour), most recent first, that
    represent the past ~3 days of swell. Each handle is the most recent cycle
    that's still available for that calendar day going backward in 24h steps.

    NOMADS retains roughly the last 10 days; older days will simply be missing
    and we skip them. The caller decides what to do if fewer than n_days are
    available (we just take the per-pixel max over whatever we got).
    """
    out = [(latest_date, latest_hour)]
    base = datetime(latest_date.year, latest_date.month, latest_date.day, latest_hour, tzinfo=timezone.utc)
    for k in range(1, n_days):
        target = base - timedelta(hours=24 * k)
        # try the same hour first, then walk back in 6h increments up to a day.
        found = None
        for back in range(0, 24, 6):
            cand = target - timedelta(hours=back)
            url = _idx_url(cand.date(), cand.hour)
            if SESSION.head(url, timeout=30, allow_redirects=True).status_code == 200:
                found = (cand.date(), cand.hour)
                break
        if found is None:
            print(f"  history miss: gfswave run within 24h of {target.strftime('%Y-%m-%d %H')}z")
            continue
        out.append(found)
    return out


def fetch_wave_slice(run_date: date, run_hour: int) -> Path:
    """Byte-range fetch HTSGW + PERPW + DIRPW at surface for f000."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = f"gfswave_{run_date.strftime('%Y%m%d')}_t{run_hour:02d}z_f000"
    grib_path = CACHE_DIR / f"{slug}.grib2"
    if grib_path.exists():
        return grib_path

    base = (
        f"{NOMADS_GFS}/gfs.{run_date.strftime('%Y%m%d')}/{run_hour:02d}/wave/gridded/"
        f"gfswave.t{run_hour:02d}z.wcoast.0p16.f000.grib2"
    )
    print(f"  GET {base}.idx", flush=True)
    idx = _get(base + ".idx").text
    lines = idx.strip().split("\n")

    starts = {}
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

    # Range covers from earliest message start to start of message AFTER the latest.
    earliest_start = min(starts[v][0] for v in needed)
    latest_idx = max(starts[v][1] for v in needed)
    if latest_idx + 1 < len(lines):
        end = int(lines[latest_idx + 1].split(":")[1]) - 1
        range_header = f"bytes={earliest_start}-{end}"
    else:
        range_header = f"bytes={earliest_start}-"
    print(f"  range fetch {range_header}", flush=True)
    r = _get(base, headers={"Range": range_header})
    r.raise_for_status()
    grib_path.write_bytes(r.content)
    return grib_path


def open_wave(grib_path: Path):
    """Open the GRIB2 slice with cfgrib. wcoast is regular lat/lng (1D coords).
    Returns (lat2d, lng2d, height, period, direction) as numpy arrays."""
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

    # cfgrib usually exposes them with these short names; accept either.
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
        # Fall back to positional keys
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


def regrid_to_bbox(lat2d, lng2d, *fields, threshold_deg=0.4):
    """Nearest-neighbor regrid to our common bbox grid."""
    pad = 0.5
    in_bbox = (
        (lat2d >= BBOX["lat_min"] - pad) & (lat2d <= BBOX["lat_max"] + pad) &
        (lng2d >= BBOX["lng_min"] - pad) & (lng2d <= BBOX["lng_max"] + pad)
    )
    pts_lat = lat2d[in_bbox].ravel()
    pts_lng = lng2d[in_bbox].ravel()
    if pts_lat.size == 0:
        raise RuntimeError("WW3 has no points within bbox padding")

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


def encode_wave_png(height_m, period_s, direction_deg, out_path: Path):
    """RGBA PNG: R=height, G=period, B=direction, A=valid mask."""
    valid = np.isfinite(height_m) & np.isfinite(period_s) & np.isfinite(direction_deg)
    h_lo, h_hi = HEIGHT_RANGE_M
    p_lo, p_hi = PERIOD_RANGE_S

    h_byte = np.clip((height_m - h_lo) / (h_hi - h_lo), 0, 1) * 255.0
    p_byte = np.clip((period_s - p_lo) / (p_hi - p_lo), 0, 1) * 255.0
    d_byte = (np.mod(direction_deg, 360.0) / 360.0) * 255.0

    h, w = height_m.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0] = np.where(valid, h_byte, 0).astype(np.uint8)
    rgba[..., 1] = np.where(valid, p_byte, 0).astype(np.uint8)
    rgba[..., 2] = np.where(valid, d_byte, 0).astype(np.uint8)
    rgba[..., 3] = (valid * 255).astype(np.uint8)
    Image.fromarray(rgba, mode="RGBA").save(out_path, optimize=True)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_date, run_hour = find_latest_gfswave_run()
    print(f"gfswave run: {run_date} t{run_hour:02d}z (wcoast.0p16, f000)")

    grib_path = fetch_wave_slice(run_date, run_hour)
    lat2d, lng2d, height, period, direction = open_wave(grib_path)
    h_grid, p_grid, d_grid = regrid_to_bbox(lat2d, lng2d, height, period, direction)

    valid = np.isfinite(h_grid)
    if valid.any():
        print(f"  now height: {np.nanmin(h_grid):.2f}–{np.nanmax(h_grid):.2f} m, mean {np.nanmean(h_grid):.2f}")
        print(f"  now period: {np.nanmin(p_grid):.1f}–{np.nanmax(p_grid):.1f} s, mean {np.nanmean(p_grid):.1f}")
        print(f"  now dir:    {np.nanmin(d_grid):.0f}–{np.nanmax(d_grid):.0f}°")

    out_path = OUT_DIR / "wave_now.png"
    encode_wave_png(h_grid, p_grid, d_grid, out_path)
    print(f"  wrote {out_path.name}")

    # ---- 3-day max H/T envelope -----------------------------------------
    # Initialize with the latest run, then fold in up to 3 prior daily runs.
    print("Building 3-day max envelope...")
    h_max = np.where(np.isfinite(h_grid), h_grid, -np.inf)
    p_max = np.where(np.isfinite(p_grid), p_grid, -np.inf)
    valid_any = np.isfinite(h_grid)

    history = find_history_runs(run_date, run_hour, n_days=4)[1:]  # exclude latest (already folded)
    for hd, hh in history:
        try:
            gp = fetch_wave_slice(hd, hh)
            la2, ln2, hi, pe, _ = open_wave(gp)
            hi_g, pe_g, _ = regrid_to_bbox(la2, ln2, hi, pe, np.zeros_like(hi))
            ok = np.isfinite(hi_g)
            if ok.any():
                h_max = np.where(ok, np.maximum(h_max, hi_g), h_max)
                p_max = np.where(ok, np.maximum(p_max, pe_g), p_max)
                valid_any = valid_any | ok
                print(f"  +{hd} t{hh:02d}z folded (max H now {np.nanmax(np.where(valid_any, h_max, np.nan)):.2f} m)")
        except Exception as e:
            print(f"  history fetch failed for {hd} t{hh:02d}z: {e!r}")

    h_max = np.where(valid_any, h_max, np.nan)
    p_max = np.where(valid_any, p_max, np.nan)
    if valid_any.any():
        print(f"  3-day max H: {np.nanmin(h_max):.2f}–{np.nanmax(h_max):.2f} m, mean {np.nanmean(h_max):.2f}")
        print(f"  3-day max T: {np.nanmin(p_max):.1f}–{np.nanmax(p_max):.1f} s, mean {np.nanmean(p_max):.1f}")
    encode_wave_png(h_max, p_max, np.zeros_like(h_max), OUT_DIR / "wave_max_3d.png")
    print("  wrote wave_max_3d.png")

    # Update manifest with wave entry — for now just metadata; the frontend
    # doesn't render waves as their own layer yet, but fetch_visibility.py
    # reads this PNG.
    manifest_path = OUT_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {
            "bbox": [BBOX["lng_min"], BBOX["lat_min"], BBOX["lng_max"], BBOX["lat_max"]],
            "layers": {},
        }
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    valid_at = (
        datetime(run_date.year, run_date.month, run_date.day, run_hour, tzinfo=timezone.utc)
    ).isoformat().replace("+00:00", "Z")
    manifest.setdefault("layers", {})["wave"] = {
        "height_range_m": list(HEIGHT_RANGE_M),
        "period_range_s": list(PERIOD_RANGE_S),
        "grid": {"width": GRID_W, "height": GRID_H},
        "source": "NOAA gfswave (WaveWatch III)",
        "generated_at": generated_at,
        "windows": {
            "now":    {"url": "/data/wave_now.png",    "valid_at": valid_at},
            "max_3d": {"url": "/data/wave_max_3d.png", "valid_at": valid_at},
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json")


if __name__ == "__main__":
    main()
