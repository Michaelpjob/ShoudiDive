"""7-day hourly wind forecast → per-day × per-bucket summaries.

Pulls every hourly forecast step the public NOAA models expose:
  * HRRR f00..f48  (3 km, 1-hour spacing) for the high-confidence near term.
  * GFS  f49..f168 (25 km, 1-hour spacing) to fill out days 3–7.

Filename is `fetch_wind_5day.py` for historical reasons; behavior is
now 7-day. Renaming touches `refresh-wind.yml` and the sibling
`fetch_swell_5day.py` so it's deferred to a coordinated rollout.

Each hourly slice is byte-range-fetched from NOMADS via its `.idx` index
so we only download the UGRD + VGRD bands at 10 m above ground (~5 MB
per file instead of ~500 MB for the full GRIB2). Total per cycle: ~170
slices × ~5 MB = ~850 MB pulled from NOMADS.

Day 5–6 confidence: GFS deterministic at lead 120–168 h has surface-
wind RMSE ~3–5 m/s (8–10 kt). Useful for "calm week vs windy week"
trend, NOT for hour-of-day planning. Tagged "low" in CONFIDENCE_BY_DAY
so the UI can downplay these days.

Outputs in `public/data/wind/`:
  hourly/d{0..6}_h{00..23}_uv.png   — RGBA U/V per Pacific-local hour
  buckets/d{0..6}_{bucket}_uv.png   — RGBA U/V averaged across each bucket
  summary.json                      — per-bucket stats + best_window

Buckets (Pacific Time, DST-aware):
  pre-dawn  04–06   morning   06–10   midday    10–14
  afternoon 14–19   evening   19–21   (21–04 collapsed: "Calm overnight")

Run: python pipeline/fetch_wind_5day.py
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
from scipy.spatial import cKDTree

# Match the existing app's bbox.
BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)

NOMADS_HRRR = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod"
NOMADS_GFS  = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"

GRID_W, GRID_H = 140, 110
UV_RANGE = (-30.0, 30.0)  # m/s

PT = ZoneInfo("America/Los_Angeles")

# Bucket boundaries in Pacific local hours [start, end).
BUCKETS: list[tuple[str, int, int]] = [
    ("predawn",   4,  6),
    ("morning",   6,  10),
    ("midday",    10, 14),
    ("afternoon", 14, 19),
    ("evening",   19, 21),
]
DAY_LABELS_REL = ["Today", "+1", "+2", "+3", "+4", "+5", "+6"]
# HRRR covers 0–48h — that overlaps roughly day 0 + most of day 1. Days
# 2 onward come from GFS, which is lower-confidence. Days 5–6 (GFS lead
# 120–168h) drift toward climatology — tag "low" so the UI can dim them.
# Future: blend GEFS ensemble mean for days 5–6 to recover some skill.
CONFIDENCE_BY_DAY = ["high", "high", "medium", "medium", "low", "low", "low"]
HORIZON_DAYS = len(DAY_LABELS_REL)  # 7

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public" / "data" / "wind"
HOURLY_DIR  = OUT_DIR / "hourly"
BUCKETS_DIR = OUT_DIR / "buckets"
CACHE_DIR   = ROOT / "pipeline" / ".cache"

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "*/*",
    "User-Agent": "shouldidive/0.1 (+github.com/Michaelpjob/ShoudiDive)",
})


# ---- Run discovery ----------------------------------------------------------

def _head_ok(url: str) -> bool:
    try:
        return SESSION.head(url, timeout=30, allow_redirects=True).status_code == 200
    except requests.RequestException:
        return False


def find_latest_hrrr_run_with_horizon(hours: int = 48) -> tuple[date, int]:
    """Latest HRRR run (00/06/12/18z) whose f{hours:02d} is published."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle = (now.hour // 6) * 6
    candidate = now.replace(hour=cycle)
    for _ in range(8):
        url = (
            f"{NOMADS_HRRR}/hrrr.{candidate.strftime('%Y%m%d')}/conus/"
            f"hrrr.t{candidate.hour:02d}z.wrfsfcf{hours:02d}.grib2.idx"
        )
        if _head_ok(url):
            return candidate.date(), candidate.hour
        print(f"  miss: HRRR {candidate.strftime('%Y-%m-%d %H')}z f{hours:02d} not yet published")
        candidate -= timedelta(hours=6)
    raise RuntimeError(f"No HRRR run with f{hours:02d} found in last 48 hours")


def find_latest_gfs_run_with_horizon(hours: int = 120) -> tuple[date, int]:
    """Latest GFS run (00/06/12/18z) whose f{hours:03d} is published."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycle = (now.hour // 6) * 6
    candidate = now.replace(hour=cycle)
    for _ in range(6):
        url = (
            f"{NOMADS_GFS}/gfs.{candidate.strftime('%Y%m%d')}/{candidate.hour:02d}/atmos/"
            f"gfs.t{candidate.hour:02d}z.pgrb2.0p25.f{hours:03d}.idx"
        )
        if _head_ok(url):
            return candidate.date(), candidate.hour
        print(f"  miss: GFS {candidate.strftime('%Y-%m-%d %H')}z f{hours:03d} not yet published")
        candidate -= timedelta(hours=6)
    raise RuntimeError(f"No GFS run with f{hours:03d} found in last 36 hours")


# ---- Byte-range UV slice fetch ---------------------------------------------

def _fetch_uv_slice(source: str, base_url: str, cache_path: Path) -> Path:
    """Pull just UGRD + VGRD at 10 m above ground via byte-range against
    .idx, cache to disk. Reused across HRRR and GFS — both wgrib2-indexed."""
    if cache_path.exists():
        return cache_path

    idx = SESSION.get(base_url + ".idx", timeout=60).text
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
        rng = f"bytes={min(u_start, v_start)}-{end}"
    else:
        rng = f"bytes={min(u_start, v_start)}-"

    r = SESSION.get(base_url, headers={"Range": rng}, timeout=180)
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
    """Open one wgrib2 byte-range slice, return (lat2d, lng2d, u, v).
    Handles both HRRR (Lambert-Conformal 2D coords) and GFS (regular 1D)."""
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
        lng2d, lat2d = np.meshgrid(lng, lat)
    else:
        lat2d, lng2d = lat, lng
    lng2d = ((lng2d + 180.0) % 360.0) - 180.0
    return lat2d, lng2d, u, v


def regrid_to_bbox(lat2d, lng2d, u, v, source: str):
    """Nearest-neighbor regrid of (u, v) onto our common bbox grid. The
    out-of-domain threshold is per-source — HRRR is 3 km, GFS is 25 km."""
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
    threshold = 0.4 if source == "gfs" else 0.1
    too_far = dists.reshape(grid_lat.shape) > threshold
    u_grid[too_far] = np.nan
    v_grid[too_far] = np.nan
    return u_grid, v_grid


# ---- Encoding ---------------------------------------------------------------

def encode_uv_png(u: np.ndarray, v: np.ndarray, out_path: Path) -> None:
    """RGBA: R=U byte, G=V byte (linear in UV_RANGE), A=0 means NaN."""
    valid = np.isfinite(u) & np.isfinite(v)
    lo, hi = UV_RANGE
    u_clip = np.clip((u - lo) / (hi - lo), 0.0, 1.0) * 255.0
    v_clip = np.clip((v - lo) / (hi - lo), 0.0, 1.0) * 255.0
    h, w = u.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0] = np.where(valid, u_clip, 0).astype(np.uint8)
    rgba[..., 1] = np.where(valid, v_clip, 0).astype(np.uint8)
    rgba[..., 2] = 0
    rgba[..., 3] = (valid * 255).astype(np.uint8)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(out_path, optimize=True)


# ---- Time math --------------------------------------------------------------

def hour_to_bucket(hour_pt: int) -> str | None:
    for name, h0, h1 in BUCKETS:
        if h0 <= hour_pt < h1:
            return name
    return None


def vector_mean_direction(u_mean: float, v_mean: float) -> float:
    """Direction the wind is COMING FROM, in compass degrees (0=N, 90=E).
    Same convention the legacy slot UI uses so the labels stay consistent."""
    if not (np.isfinite(u_mean) and np.isfinite(v_mean)):
        return float("nan")
    # Wind vector points TO; reverse for FROM.
    rad = np.arctan2(-u_mean, -v_mean)
    return float((np.rad2deg(rad) + 360.0) % 360.0)


# ---- Orchestrator -----------------------------------------------------------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HOURLY_DIR.mkdir(parents=True, exist_ok=True)
    BUCKETS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 1) Latest published runs.
    hrrr_d, hrrr_h = find_latest_hrrr_run_with_horizon(48)
    gfs_d,  gfs_h  = find_latest_gfs_run_with_horizon(168)
    hrrr_cycle = datetime(hrrr_d.year, hrrr_d.month, hrrr_d.day, hrrr_h, tzinfo=timezone.utc)
    gfs_cycle  = datetime(gfs_d.year,  gfs_d.month,  gfs_d.day,  gfs_h,  tzinfo=timezone.utc)
    print(f"HRRR cycle: {hrrr_cycle.isoformat()}  (f00..f48)")
    print(f"GFS  cycle: {gfs_cycle.isoformat()}  (f49..f168)")

    # 2) Anchor "today" to the *current* Pacific date, NOT the cycle's PT
    #    date. The cycle is gated on f48/f168 being published (~14 h after
    #    cycle issue), so cycle-anchored labels lag behind real time and a
    #    user viewing at mid-day on day X sees day 0 labeled "Today" but
    #    pointing at day X-1's data. Anchoring on now keeps the label
    #    truthful; forecast hours older than midnight PT today are filtered
    #    out by the day_offset < 0 check below, so day 0 just truncates to
    #    "from now → PT midnight tonight".
    anchor_pt_date = datetime.now(PT).date()
    print(f"Day anchor: {anchor_pt_date} (Pacific, today)")

    # 3) Fetch + decode every hourly step, indexed by Pacific (day, hour).
    hourly: dict[tuple[int, int], dict] = {}

    def store(valid_at_utc, u_grid, v_grid, source):
        valid_pt = valid_at_utc.astimezone(PT)
        day_offset = (valid_pt.date() - anchor_pt_date).days
        if day_offset < 0 or day_offset >= HORIZON_DAYS:
            return
        key = (day_offset, valid_pt.hour)
        # Prefer HRRR if both sources cover the same hour.
        if key in hourly and hourly[key]["source"] == "hrrr" and source == "gfs":
            return
        hourly[key] = {"u": u_grid, "v": v_grid, "source": source, "valid_at": valid_at_utc}

    # GFS goes to f168 (7 days) at 1-hour spacing through f120, then 3-hour
    # spacing f120–f240. We pull every step but the f120–f168 slices that
    # don't exist (GFS only emits f123, f126, f129, ...) will fail
    # individually and just leave hourly gaps for those hours, which
    # nanmean handles cleanly when bucketing.
    GFS_HORIZON_HRS = 168
    HRRR_END = 49  # exclusive
    GFS_END = GFS_HORIZON_HRS + 1  # exclusive

    fetched = 0
    failed  = 0
    for fhour in range(0, HRRR_END):
        try:
            grib = fetch_hrrr_slice(hrrr_d, hrrr_h, fhour)
            lat2d, lng2d, u_native, v_native = open_uv(grib)
            u_grid, v_grid = regrid_to_bbox(lat2d, lng2d, u_native, v_native, "hrrr")
            store(hrrr_cycle + timedelta(hours=fhour), u_grid, v_grid, "hrrr")
            fetched += 1
        except Exception as e:
            print(f"  HRRR f{fhour:02d}: {e!s}")
            failed += 1
    for fhour in range(HRRR_END, GFS_END):
        try:
            grib = fetch_gfs_slice(gfs_d, gfs_h, fhour)
            lat2d, lng2d, u_native, v_native = open_uv(grib)
            u_grid, v_grid = regrid_to_bbox(lat2d, lng2d, u_native, v_native, "gfs")
            store(gfs_cycle + timedelta(hours=fhour), u_grid, v_grid, "gfs")
            fetched += 1
        except Exception as e:
            # f120+ runs in 3-hour spacing on GFS, so 2/3 of these will
            # 404 — that's expected, not a bug. The gaps are tolerated.
            if fhour <= 120:
                print(f"  GFS  f{fhour:03d}: {e!s}")
            failed += 1
    total_attempted = HRRR_END + (GFS_END - HRRR_END)
    print(f"fetched {fetched}/{total_attempted} forecast hours ({failed} failed); "
          f"{len(hourly)} unique (day, hour) slots stored")

    # 4) Write per-hour PNGs.
    for (day_offset, hour_pt), entry in hourly.items():
        encode_uv_png(
            entry["u"], entry["v"],
            HOURLY_DIR / f"d{day_offset}_h{hour_pt:02d}_uv.png",
        )

    # 5) Aggregate to N×5 buckets (N = HORIZON_DAYS).
    bucket_summaries: list[dict] = []
    for day_offset in range(HORIZON_DAYS):
        for bucket_name, h0, h1 in BUCKETS:
            us, vs = [], []
            sources_in_bucket = set()
            valid_ats = []
            for h in range(h0, h1):
                e = hourly.get((day_offset, h))
                if e is None:
                    continue
                us.append(e["u"]); vs.append(e["v"])
                sources_in_bucket.add(e["source"])
                valid_ats.append(e["valid_at"])
            if not us:
                continue
            u_stack = np.stack(us, axis=0)
            v_stack = np.stack(vs, axis=0)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                mean_u = np.nanmean(u_stack, axis=0)
                mean_v = np.nanmean(v_stack, axis=0)
                speeds = np.sqrt(u_stack ** 2 + v_stack ** 2) * 1.94384  # kt, (T,H,W)

            # Per-hour bbox-mean speed: how does wind change ACROSS the bucket?
            # That's the right signal for the day-card range bar (e.g. morning
            # ramps 3 → 7 kt). Spatial variability is conveyed by the heatmap.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                bbox_speed_per_hour = np.nanmean(speeds, axis=(1, 2))  # (T,)
            bbox_mean_kt = (
                float(np.nanmean(bbox_speed_per_hour))
                if np.isfinite(bbox_speed_per_hour).any() else None
            )
            bbox_min_kt = (
                float(np.nanmin(bbox_speed_per_hour))
                if np.isfinite(bbox_speed_per_hour).any() else None
            )
            bbox_max_kt = (
                float(np.nanmax(bbox_speed_per_hour))
                if np.isfinite(bbox_speed_per_hour).any() else None
            )
            bbox_mean_u  = float(np.nanmean(mean_u))  if np.isfinite(mean_u).any()  else None
            bbox_mean_v  = float(np.nanmean(mean_v))  if np.isfinite(mean_v).any()  else None
            mean_dir = (
                vector_mean_direction(bbox_mean_u, bbox_mean_v)
                if bbox_mean_u is not None and bbox_mean_v is not None
                else None
            )

            encode_uv_png(
                mean_u, mean_v,
                BUCKETS_DIR / f"d{day_offset}_{bucket_name}_uv.png",
            )

            bucket_summaries.append({
                "day":         day_offset,
                "bucket":      bucket_name,
                "hours":       [h for h in range(h0, h1) if (day_offset, h) in hourly],
                "mean_kt":     None if bbox_mean_kt is None else round(bbox_mean_kt, 1),
                "min_kt":      None if bbox_min_kt  is None else round(bbox_min_kt,  1),
                "max_kt":      None if bbox_max_kt  is None else round(bbox_max_kt,  1),
                "mean_dir_deg":None if mean_dir     is None else round(mean_dir, 0),
                "uv_url":      f"/data/wind/buckets/d{day_offset}_{bucket_name}_uv.png",
                "sources":     sorted(sources_in_bucket),
            })
            print(f"  d{day_offset} {bucket_name:>9}: "
                  f"mean {bbox_mean_kt:.1f} kt  ({len([h for h in range(h0,h1) if (day_offset, h) in hourly])}/{h1-h0} hrs)"
                  if bbox_mean_kt is not None else
                  f"  d{day_offset} {bucket_name:>9}: no data")

    # 6) Best window — lowest mean_kt across all buckets, tie-break earlier.
    candidates = [b for b in bucket_summaries if b["mean_kt"] is not None]
    best_window = None
    if candidates:
        best = min(
            candidates,
            key=lambda b: (b["mean_kt"], b["day"], BUCKET_ORDER.index(b["bucket"])),
        )
        best_window = {
            "day":     best["day"],
            "bucket":  best["bucket"],
            "mean_kt": best["mean_kt"],
        }

    # 7) Per-day shells with confidence + Pacific date label.
    days = []
    for d in range(HORIZON_DAYS):
        date_pt = anchor_pt_date + timedelta(days=d)
        days.append({
            "day":          d,
            "date":         date_pt.isoformat(),
            "label":        DAY_LABELS_REL[d],
            "weekday":      date_pt.strftime("%A"),
            "confidence":   CONFIDENCE_BY_DAY[d],
            "buckets":      [b for b in bucket_summaries if b["day"] == d],
            "hourly_url_template": f"/data/wind/hourly/d{d}_h{{HH}}_uv.png",
        })

    summary = {
        "generated_at": (
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        ),
        "tz":           "America/Los_Angeles",
        "anchor_date":  anchor_pt_date.isoformat(),
        "hrrr_cycle":   hrrr_cycle.isoformat().replace("+00:00", "Z"),
        "gfs_cycle":    gfs_cycle.isoformat().replace("+00:00", "Z"),
        "buckets_def": [
            {"name": name, "start_hour_local": h0, "end_hour_local": h1}
            for name, h0, h1 in BUCKETS
        ],
        "days":         days,
        "best_window":  best_window,
        "speed_range":  [0.0, 50.0],  # kt (legacy ramp endpoints)
        "uv_range":     list(UV_RANGE),
        "grid":         {"width": GRID_W, "height": GRID_H},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"wrote {OUT_DIR / 'summary.json'}")

    # 8) Patch the existing top-level manifest so the frontend can discover
    #    the new wind block via a single fetch. We keep the legacy `wind`
    #    layer payload intact (still consumed by the current 4-slot UI) and
    #    add a new `wind5d` block alongside it. Once the new UI lands the
    #    legacy one can be dropped.
    manifest_path = ROOT / "public" / "data" / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {
            "bbox": [BBOX["lng_min"], BBOX["lat_min"], BBOX["lng_max"], BBOX["lat_max"]],
            "layers": {},
        }
    manifest["generated_at"] = summary["generated_at"]
    manifest.setdefault("layers", {})["wind5d"] = {
        "summary_url": "/data/wind/summary.json",
        "grid":        {"width": GRID_W, "height": GRID_H},
        "speed_range": [0.0, 50.0],
        "uv_range":    list(UV_RANGE),
        "tz":          "America/Los_Angeles",
        "best_window": best_window,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"patched {manifest_path}")


# Bucket name → display order, used for best_window tie-breaking.
BUCKET_ORDER = [name for name, _, _ in BUCKETS]


if __name__ == "__main__":
    main()
