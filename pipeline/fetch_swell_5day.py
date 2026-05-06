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
from scipy.ndimage import distance_transform_edt
from scipy.spatial import cKDTree

BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)

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
OUT_DIR = ROOT / "public" / "data" / "swell"
HOURLY_DIR  = OUT_DIR / "hourly"
BUCKETS_DIR = OUT_DIR / "buckets"
CACHE_DIR   = ROOT / "pipeline" / ".cache"

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "*/*",
    "User-Agent": "shouldidive/0.1 (+github.com/Michaelpjob/ShoudiDive)",
})


def _head_ok(url: str) -> bool:
    try:
        return SESSION.head(url, timeout=30, allow_redirects=True).status_code == 200
    except requests.RequestException:
        return False


# ---- Run discovery ----------------------------------------------------------

def _idx_url(run_date: date, run_hour: int, fhour: int) -> str:
    return (
        f"{NOMADS_GFS}/gfs.{run_date.strftime('%Y%m%d')}/{run_hour:02d}/wave/gridded/"
        f"gfswave.t{run_hour:02d}z.wcoast.0p16.f{fhour:03d}.grib2.idx"
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
    slug = f"gfswave_{run_date.strftime('%Y%m%d')}_t{run_hour:02d}z_f{fhour:03d}"
    grib_path = CACHE_DIR / f"{slug}.grib2"
    if grib_path.exists():
        return grib_path

    base = (
        f"{NOMADS_GFS}/gfs.{run_date.strftime('%Y%m%d')}/{run_hour:02d}/wave/gridded/"
        f"gfswave.t{run_hour:02d}z.wcoast.0p16.f{fhour:03d}.grib2"
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


def fill_nearest(arr, max_cells: int = 40):
    """Fill NaN cells with their nearest valid neighbour's value.

    gfswave wcoast 0.16° masks shallow / nearshore cells where bathymetry
    interferes with the model — the inner-shelf cells along the CA coast
    come back as NaN. Without this fill the heatmap shows a band of "no
    data" hatching all along the shore, even though the open-water cells
    a few km offshore have perfectly good values.

    `max_cells` caps how far the fill can propagate (in grid cells, ~5 km
    each). 40 cells ≈ 200 km — wide enough to bridge gfswave's broader
    masked zones around Pt Conception, the Channel Islands lee shore, and
    inside SB / SD bays, but still narrow enough that far-inland cells
    (Arizona desert, etc.) stay NaN rather than getting painted with
    offshore Pacific Hs.
    """
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
    manifest_path = ROOT / "public" / "data" / "manifest.json"
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
