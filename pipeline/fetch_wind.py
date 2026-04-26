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

# Match the existing app's bbox.
BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)

NOMADS_HRRR = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod"
NOMADS_GFS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"

# Per-slot config: (source, fcst_hour). HRRR for short-range; GFS for +72h
# since HRRR maxes out at f48.
SLOTS = {
    "now":  {"source": "hrrr", "fhour": 0},
    "p6h":  {"source": "hrrr", "fhour": 6},
    "p24h": {"source": "hrrr", "fhour": 24},
    "p72h": {"source": "gfs",  "fhour": 72},
}

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
OUT_DIR = ROOT / "public" / "data"
CACHE_DIR = ROOT / "pipeline" / ".cache"

# NOMADS HTTP/2 implementation is broken; force HTTP/1.1.
SESSION = requests.Session()
SESSION.headers.update({"Accept": "*/*", "User-Agent": "shouldidive/0.1 (+github.com/Michaelpjob/ShoudiDive)"})


def _get(url, **kwargs):
    return SESSION.get(url, timeout=180, **kwargs)


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


# ---- Encoders ---------------------------------------------------------------

def encode_speed_png(u: np.ndarray, v: np.ndarray, out_path: Path) -> None:
    speed_kt = np.sqrt(u * u + v * v) * 1.94384
    valid = np.isfinite(speed_kt)
    lo, hi = SPEED_RANGE
    scaled = (speed_kt - lo) / (hi - lo)
    px = np.zeros(speed_kt.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


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

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Find runs lazily — only call HRRR/GFS discovery if at least one slot needs it.
    sources_needed = {cfg["source"] for cfg in SLOTS.values()}
    runs: dict[str, tuple[date, int]] = {}
    if "hrrr" in sources_needed:
        d, h = find_latest_hrrr_extended_run()
        runs["hrrr"] = (d, h)
        print(f"HRRR run: {d} t{h:02d}z")
    if "gfs" in sources_needed:
        d, h = find_latest_gfs_run_with_f072()
        runs["gfs"] = (d, h)
        print(f"GFS run:  {d} t{h:02d}z")

    wind_layer = {
        "speed_range": list(SPEED_RANGE),
        "uv_range": list(UV_RANGE),
        "unit": "kt",
        "grid": {"width": GRID_W, "height": GRID_H},
        "source": "NOAA HRRR + GFS",
        "windows": {},
    }

    for slot, cfg in SLOTS.items():
        source = cfg["source"]
        fhour = cfg["fhour"]
        run_date, run_hour = runs[source]

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

        speed_path = OUT_DIR / f"wind_speed_{slot}.png"
        uv_path = OUT_DIR / f"wind_uv_{slot}.png"
        encode_speed_png(u, v, speed_path)
        encode_uv_png(u, v, uv_path)
        valid_at = (
            datetime(run_date.year, run_date.month, run_date.day, run_hour, tzinfo=timezone.utc)
            + timedelta(hours=fhour)
        )
        wind_layer["windows"][slot] = {
            "speed_url": f"/data/wind_speed_{slot}.png",
            "uv_url": f"/data/wind_uv_{slot}.png",
            "valid_at": valid_at.isoformat().replace("+00:00", "Z"),
            "fcst_hour": fhour,
            "source": source.upper(),
        }
        print(f"  wrote wind_{slot}  ({GRID_H}x{GRID_W})  source={source}")

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
    manifest["generated_at"] = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    manifest.setdefault("layers", {})["wind"] = wind_layer
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json")


if __name__ == "__main__":
    main()
