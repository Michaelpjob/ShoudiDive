"""Build a first-pass surface-current layer.

Inputs, in priority order:
  - IOOS/NDBC HFRNet U.S. West Coast 6 km near-real-time surface currents.
  - Existing wind bucket grids, tides.json, and lunar spring/neap phase for
    short-horizon inferred buckets when direct observations are unavailable
    or aging out.

Outputs:
  public/data/currents/buckets/d{0..4}_{bucket}_uv.png
  public/data/currents/summary.json

The frontend treats this like wind/swell: a vector heatmap plus a time
scrubber. The values are surface currents, not depth currents.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import xarray as xr
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree


# Bbox via pipeline/regions/ (PR-X-1). CA / PNW / tropical switch on
# SHOULDIDIVE_REGION; default `ca` preserves today's behavior.
try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

BBOX = active_region().bbox
GRID_W, GRID_H = 280, 220
UV_RANGE = (-1.5, 1.5)  # m/s, roughly 0..3 kt in either component.
HORIZON_DAYS = 5
PT = ZoneInfo("America/Los_Angeles")

HFR_USWC_6KM = "https://dods.ndbc.noaa.gov/thredds/dodsC/hfradar_uswc_6km"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = active_region().data_output_dir(ROOT)
OUT_DIR = DATA_DIR / "currents"
BUCKETS_DIR = OUT_DIR / "buckets"
LAND_PATH = DATA_DIR / "land.geojson"

BUCKETS: list[tuple[str, int, int]] = [
    ("predawn", 4, 6),
    ("morning", 6, 10),
    ("midday", 10, 14),
    ("afternoon", 14, 19),
    ("evening", 19, 21),
]
DAY_LABELS = ["Today", "+1", "+2", "+3", "+4"]
_LAND_MASK: np.ndarray | None = None


def target_grid() -> tuple[np.ndarray, np.ndarray]:
    lats = np.linspace(BBOX["lat_max"], BBOX["lat_min"], GRID_H, dtype=np.float32)
    lngs = np.linspace(BBOX["lng_min"], BBOX["lng_max"], GRID_W, dtype=np.float32)
    return np.meshgrid(lngs, lats)


def _coord_to_grid_xy(coord: list[float]) -> tuple[float, float]:
    lng, lat = coord[:2]
    x = ((lng - BBOX["lng_min"]) / (BBOX["lng_max"] - BBOX["lng_min"])) * (GRID_W - 1)
    y = ((BBOX["lat_max"] - lat) / (BBOX["lat_max"] - BBOX["lat_min"])) * (GRID_H - 1)
    return x, y


def land_mask() -> np.ndarray:
    global _LAND_MASK
    if _LAND_MASK is not None:
        return _LAND_MASK
    if not LAND_PATH.exists():
        _LAND_MASK = np.zeros((GRID_H, GRID_W), dtype=bool)
        return _LAND_MASK

    fc = json.loads(LAND_PATH.read_text(encoding="utf-8"))
    img = Image.new("L", (GRID_W, GRID_H), 0)
    draw = ImageDraw.Draw(img)
    for feature in fc.get("features", []):
        geom = feature.get("geometry") or {}
        if geom.get("type") == "Polygon":
            polys = [geom.get("coordinates") or []]
        elif geom.get("type") == "MultiPolygon":
            polys = geom.get("coordinates") or []
        else:
            continue
        for poly in polys:
            if not poly:
                continue
            exterior = [_coord_to_grid_xy(pt) for pt in poly[0]]
            if exterior:
                draw.polygon(exterior, fill=1)
            for hole in poly[1:]:
                pts = [_coord_to_grid_xy(pt) for pt in hole]
                if pts:
                    draw.polygon(pts, fill=0)

    # Keep this exact. Visual land clipping happens in SVG map space; padding
    # this coarse grid creates visible no-data moats around islands and shore.
    _LAND_MASK = np.asarray(img, dtype=np.uint8) > 0
    return _LAND_MASK


def apply_land_mask(u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = land_mask()
    u = u.copy()
    v = v.copy()
    u[mask] = np.nan
    v[mask] = np.nan
    return u, v


def km_coords(lng: np.ndarray, lat: np.ndarray) -> np.ndarray:
    clat = 0.5 * (BBOX["lat_min"] + BBOX["lat_max"])
    x = lng * 111.0 * math.cos(math.radians(clat))
    y = lat * 111.0
    return np.column_stack([x.ravel(), y.ravel()])


def regrid_to_bbox(lat: np.ndarray, lng: np.ndarray, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if lat.ndim == 1 and lng.ndim == 1:
        lng2d, lat2d = np.meshgrid(lng, lat)
    else:
        lat2d, lng2d = lat, lng
    lng2d = ((lng2d + 180.0) % 360.0) - 180.0

    finite = np.isfinite(u) & np.isfinite(v)
    near = (
        (lat2d >= BBOX["lat_min"] - 0.25) &
        (lat2d <= BBOX["lat_max"] + 0.25) &
        (lng2d >= BBOX["lng_min"] - 0.25) &
        (lng2d <= BBOX["lng_max"] + 0.25)
    )
    mask = finite & near
    out_u = np.full((GRID_H, GRID_W), np.nan, dtype=np.float32)
    out_v = np.full((GRID_H, GRID_W), np.nan, dtype=np.float32)
    if int(mask.sum()) < 4:
        return out_u, out_v

    tree = cKDTree(km_coords(lng2d[mask], lat2d[mask]))
    tgt_lng, tgt_lat = target_grid()
    dist, idx = tree.query(km_coords(tgt_lng, tgt_lat), distance_upper_bound=18.0)
    src_u = u[mask].astype(np.float32)
    src_v = v[mask].astype(np.float32)
    good = np.isfinite(dist) & (idx < src_u.size)
    flat_u = out_u.ravel()
    flat_v = out_v.ravel()
    flat_u[good] = src_u[idx[good]]
    flat_v[good] = src_v[idx[good]]
    return apply_land_mask(out_u, out_v)


def iso_from_time_value(value) -> str:
    try:
        dt = np.datetime64(value, "s")
        return np.datetime_as_string(dt, unit="s") + "Z"
    except Exception:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_hfr_latest() -> dict | None:
    """Return latest HFR current grid over the app bbox, or None."""
    print("fetching HFRNet USWC 6 km latest surface currents")
    try:
        ds = xr.open_dataset(HFR_USWC_6KM)
    except Exception as exc:
        print(f"  HFR open failed: {exc!s}")
        return None

    lat = np.asarray(ds["lat"].values)
    lng = ((np.asarray(ds["lon"].values) + 180.0) % 360.0) - 180.0
    lat_idx = np.where((lat >= BBOX["lat_min"] - 0.25) & (lat <= BBOX["lat_max"] + 0.25))[0]
    lng_idx = np.where((lng >= BBOX["lng_min"] - 0.25) & (lng <= BBOX["lng_max"] + 0.25))[0]
    if lat_idx.size == 0 or lng_idx.size == 0:
        print("  HFR bbox selection empty")
        return None

    lat_slice = slice(int(lat_idx.min()), int(lat_idx.max()) + 1)
    lng_slice = slice(int(lng_idx.min()), int(lng_idx.max()) + 1)
    time_len = int(ds.sizes.get("time", 0))
    for ti in range(time_len - 1, max(-1, time_len - 18), -1):
        try:
            sub = ds.isel(time=ti, lat=lat_slice, lon=lng_slice)
            u = np.asarray(sub["u"].values, dtype=np.float32)
            v = np.asarray(sub["v"].values, dtype=np.float32)
            hu, hv = regrid_to_bbox(lat[lat_slice], lng[lng_slice], u, v)
        except Exception as exc:
            print(f"  HFR time index {ti} failed: {exc!s}")
            continue
        coverage = float(np.isfinite(hu).sum() / hu.size)
        if coverage < 0.01:
            continue
        valid_at = iso_from_time_value(ds["time"].values[ti])
        print(f"  HFR valid_at={valid_at} coverage={coverage:.2%}")
        return {
            "u": hu,
            "v": hv,
            "valid_at": valid_at,
            "coverage_frac": round(coverage, 3),
            "source": "hfr_uswc_6km",
        }
    print("  HFR had no usable recent coverage over bbox")
    return None


def encode_uv_png(u: np.ndarray, v: np.ndarray, out: Path) -> None:
    lo, hi = UV_RANGE
    span = hi - lo
    # Do not use PNG alpha as the visual land clip: the browser stretches and
    # interpolates this grid, so transparent land cells become visible moats
    # around islands and near shore. The frontend clips the raster with the
    # vector coastline mask instead, while stats still apply apply_land_mask().
    valid = np.isfinite(u) & np.isfinite(v)
    img = np.zeros((u.shape[0], u.shape[1], 4), dtype=np.uint8)
    img[..., 0][valid] = np.clip(np.round(((u[valid] - lo) / span) * 255), 0, 255).astype(np.uint8)
    img[..., 1][valid] = np.clip(np.round(((v[valid] - lo) / span) * 255), 0, 255).astype(np.uint8)
    img[..., 3][valid] = 255
    Image.fromarray(img, mode="RGBA").save(out, optimize=True)


def decode_wind_uv(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    if not path.exists():
        return None
    img = np.asarray(Image.open(path).convert("RGBA"))
    valid = img[..., 3] > 0
    u = np.full(img.shape[:2], np.nan, dtype=np.float32)
    v = np.full(img.shape[:2], np.nan, dtype=np.float32)
    lo, hi = -30.0, 30.0
    span = hi - lo
    u[valid] = lo + (img[..., 0][valid].astype(np.float32) / 255.0) * span
    v[valid] = lo + (img[..., 1][valid].astype(np.float32) / 255.0) * span
    return u, v


def resample_to_currents_grid(arr: np.ndarray) -> np.ndarray:
    """Nearest-neighbor resize for wind grids that are already bbox-aligned."""
    if arr.shape == (GRID_H, GRID_W):
        return arr.astype(np.float32)
    y_idx = np.clip(np.round(np.linspace(0, arr.shape[0] - 1, GRID_H)).astype(int), 0, arr.shape[0] - 1)
    x_idx = np.clip(np.round(np.linspace(0, arr.shape[1] - 1, GRID_W)).astype(int), 0, arr.shape[1] - 1)
    return arr[np.ix_(y_idx, x_idx)].astype(np.float32)


def wind_for_slot(day: int, bucket: str) -> tuple[np.ndarray, np.ndarray]:
    decoded = decode_wind_uv(DATA_DIR / "wind" / "buckets" / f"d{day}_{bucket}_uv.png")
    if decoded is None:
        z = np.zeros((GRID_H, GRID_W), dtype=np.float32)
        return z, z
    return resample_to_currents_grid(decoded[0]), resample_to_currents_grid(decoded[1])


def load_tide_range_m() -> float:
    path = DATA_DIR / "tides.json"
    if not path.exists():
        return 1.5
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 1.5
    vals = [float(s.get("range_m")) for s in data.get("stations", []) if s.get("range_m") is not None]
    return float(np.nanmean(vals)) if vals else 1.5


def moon_spring_factor(dt: datetime) -> float:
    # Approximate synodic phase. Springs near new/full, neaps near quarters.
    epoch = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    days = (dt - epoch).total_seconds() / 86400.0
    phase = (days % 29.53058867) / 29.53058867
    return 0.75 + 0.5 * abs(math.cos(2.0 * math.pi * phase))


def tide_signal(dt: datetime) -> float:
    epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
    hours = (dt - epoch).total_seconds() / 3600.0
    return math.sin(2.0 * math.pi * hours / 12.4206)


def tide_vector(dt: datetime, tide_range_m: float) -> tuple[float, float]:
    # Positive signal means a southeast-setting alongshore push; negative
    # means northwest-setting. This is deliberately broad, then corrected by
    # HFR persistence where available.
    signal = tide_signal(dt)
    amp = (0.05 + 0.06 * min(2.5, max(0.5, tide_range_m))) * moon_spring_factor(dt)
    coast_u, coast_v = 0.55, -0.84
    return coast_u * amp * signal, coast_v * amp * signal


def bucket_center(anchor: date, day: int, start_hour: int, end_hour: int) -> datetime:
    center = (start_hour + end_hour) / 2.0
    hour = int(center)
    minute = int(round((center - hour) * 60))
    local = datetime.combine(anchor + timedelta(days=day), time(hour, minute), tzinfo=PT)
    return local.astimezone(timezone.utc)


def circular_dir_to(u: np.ndarray, v: np.ndarray) -> float | None:
    mask = np.isfinite(u) & np.isfinite(v)
    if not mask.any():
        return None
    mu = float(np.nanmean(u[mask]))
    mv = float(np.nanmean(v[mask]))
    if not math.isfinite(mu) or not math.isfinite(mv) or (mu == 0 and mv == 0):
        return None
    return (math.degrees(math.atan2(mu, mv)) + 360.0) % 360.0


def stats_for_grid(u: np.ndarray, v: np.ndarray, valid_at: datetime, source: str, hfr_weight: float) -> dict:
    u, v = apply_land_mask(u, v)
    speed_kt = np.sqrt(u * u + v * v) * 1.94384
    valid = np.isfinite(speed_kt)
    if not valid.any():
        return {
            "mean_kt": None,
            "min_kt": None,
            "max_kt": None,
            "mean_dir_to_deg": None,
            "coverage_frac": 0.0,
            "consistency": 0,
            "reversal_risk": "unknown",
            "source": source,
        }
    mean_speed = float(np.nanmean(speed_kt[valid]))
    std_speed = float(np.nanstd(speed_kt[valid]))
    mean_u = float(np.nanmean(u[valid]))
    mean_v = float(np.nanmean(v[valid]))
    coherence = math.sqrt(mean_u * mean_u + mean_v * mean_v) * 1.94384 / max(mean_speed, 0.05)
    cv = std_speed / max(mean_speed, 0.05)
    consistency = int(round(100.0 * max(0.0, min(1.0, coherence)) * (1.0 - min(0.7, cv * 0.35))))
    sig = abs(tide_signal(valid_at))
    reversal_risk = "high" if sig < 0.18 else "medium" if sig < 0.35 else "low"
    return {
        "mean_kt": round(mean_speed, 2),
        "min_kt": round(float(np.nanmin(speed_kt[valid])), 2),
        "max_kt": round(float(np.nanmax(speed_kt[valid])), 2),
        "mean_dir_to_deg": None if circular_dir_to(u, v) is None else round(float(circular_dir_to(u, v)), 1),
        "coverage_frac": round(float(valid.sum() / speed_kt.size), 3),
        "consistency": consistency,
        "reversal_risk": reversal_risk,
        "source": source,
        "hfr_weight": round(hfr_weight, 2),
    }


def build_slot(valid_at: datetime, day: int, bucket: str, hfr: dict | None, tide_range_m: float, now: datetime) -> tuple[np.ndarray, np.ndarray, str, float]:
    wind_u, wind_v = wind_for_slot(min(day, 4), bucket)
    tide_u, tide_v = tide_vector(valid_at, tide_range_m)
    inferred_u = np.full((GRID_H, GRID_W), tide_u, dtype=np.float32)
    inferred_v = np.full((GRID_H, GRID_W), tide_v, dtype=np.float32)
    # A small wind-drift term captures surface set without pretending to be
    # a hydrodynamic model.
    #
    # v2 (2026-05-19): bumped 0.012 → 0.025. The previous 1.2% Ekman-drift
    # coefficient was too tame for regions without HFR coverage (Baja, all
    # tropical) — user feedback was "current barely changes over the
    # forecast" because tide signal repeats every ~12h and the wind term
    # was producing <0.1 kt swings that were invisible on the heatmap.
    # 2.5% matches the classic surface Ekman estimate (3% of wind speed)
    # and gives visible day-to-day variation when wind changes.
    inferred_u = inferred_u + np.nan_to_num(wind_u, nan=0.0) * 0.025
    inferred_v = inferred_v + np.nan_to_num(wind_v, nan=0.0) * 0.025

    lead_h = max(0.0, (valid_at - now).total_seconds() / 3600.0)
    if hfr is None:
        return inferred_u, inferred_v, "inferred_tide_wind", 0.0

    hfr_weight = math.exp(-lead_h / 8.0)
    hfr_weight = max(0.0, min(0.9, hfr_weight))
    hmask = np.isfinite(hfr["u"]) & np.isfinite(hfr["v"])
    out_u = inferred_u.copy()
    out_v = inferred_v.copy()
    out_u[hmask] = hfr_weight * hfr["u"][hmask] + (1.0 - hfr_weight) * inferred_u[hmask]
    out_v[hmask] = hfr_weight * hfr["v"][hmask] + (1.0 - hfr_weight) * inferred_v[hmask]
    source = "hfr_observed" if lead_h <= 1.5 else "hfr_persistence_tide_wind"
    return out_u, out_v, source, hfr_weight


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BUCKETS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    generated_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    anchor = now.astimezone(PT).date()
    tide_range_m = load_tide_range_m()
    hfr = fetch_hfr_latest()

    days = []
    best = None
    for day in range(HORIZON_DAYS):
        day_date = anchor + timedelta(days=day)
        day_entry = {
            "day": day,
            "date": day_date.isoformat(),
            "label": DAY_LABELS[day],
            "weekday": day_date.strftime("%A"),
            "confidence": "high" if day == 0 and hfr else "medium" if day <= 2 else "low",
            "buckets": [],
        }
        for bucket, start_h, end_h in BUCKETS:
            valid_at = bucket_center(anchor, day, start_h, end_h)
            u, v, source, hfr_weight = build_slot(valid_at, day, bucket, hfr, tide_range_m, now)
            out = BUCKETS_DIR / f"d{day}_{bucket}_uv.png"
            encode_uv_png(u, v, out)
            stats = stats_for_grid(u, v, valid_at, source, hfr_weight)
            bucket_entry = {
                "day": day,
                "bucket": bucket,
                "hours": [start_h, end_h],
                "valid_at": valid_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "uv_url": f"/data/currents/buckets/d{day}_{bucket}_uv.png",
                **stats,
            }
            day_entry["buckets"].append(bucket_entry)
            if stats["mean_kt"] is not None:
                score = stats["consistency"] - 18.0 * stats["mean_kt"] - (20 if stats["reversal_risk"] == "high" else 0)
                if best is None or score > best["score"]:
                    best = {"day": day, "bucket": bucket, "mean_kt": stats["mean_kt"], "score": round(score, 1)}
            print(f"  wrote currents/buckets/d{day}_{bucket}_uv.png")
        days.append(day_entry)

    summary = {
        "generated_at": generated_at,
        "tz": "America/Los_Angeles",
        "anchor_date": anchor.isoformat(),
        "unit": "kt",
        "vector_convention": "direction_to",
        "surface_note": "Surface-current estimate. Reef-depth current can differ near structure, kelp, and steep island shelves.",
        "sources": ["hfr_uswc_6km", "tide_range", "moon_phase", "wind_drift"],
        "observed": {
            "source": hfr["source"] if hfr else None,
            "valid_at": hfr["valid_at"] if hfr else None,
            "coverage_frac": hfr["coverage_frac"] if hfr else 0.0,
        },
        "tide_range_m": round(float(tide_range_m), 3),
        "buckets_def": [
            {"name": name, "start_hour_local": start_h, "end_hour_local": end_h}
            for name, start_h, end_h in BUCKETS
        ],
        "days": days,
        "best_window": None if best is None else {k: v for k, v in best.items() if k != "score"},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("wrote currents/summary.json")

    manifest_path = DATA_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "bbox": [BBOX["lng_min"], BBOX["lat_min"], BBOX["lng_max"], BBOX["lat_max"]],
            "layers": {},
        }
    manifest.setdefault("layers", {})["current5d"] = {
        "summary_url": "/data/currents/summary.json",
        "grid": {"width": GRID_W, "height": GRID_H},
        "uv_range": list(UV_RANGE),
        "speed_range": [0.0, 3.0],
        "unit": "kt",
        "vector_convention": "direction_to",
        "generated_at": generated_at,
        "best_window": summary["best_window"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("updated manifest.json with current5d")


if __name__ == "__main__":
    main()
