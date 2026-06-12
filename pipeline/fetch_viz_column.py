"""Water-column visibility (PRD water-column C1) — v1 heuristic fetcher.

Derives a per-cell two-layer visibility profile from data the pipeline
has ALREADY fetched this cycle — no network calls here. Inputs (all
under the region's data dir):

    viz_p50_ft.png       surface visibility (existing model = above-cliff)
    wind_uv_now.png      10 m wind U/V -> upwelling index
    wave_now.png         Hs + Tp -> near-bottom resuspension
    bathy.png/.json      bottom depth (resampled onto the viz grid)
    tides.json           hi/lo events -> internal-tide cliff swing phase

Outputs:

    viz_column_below_ft.png   below-cliff visibility, linear 0-80 ft
                              (same range/scale as the viz layer so the
                              existing legend semantics apply)
    viz_column_cliff_ft.png   cliff (thermocline proxy) depth, 0-100 ft
    viz_column_spots.json     per-saved-spot column profiles + the 24 h
                              cliff-depth series for the diurnal strip
    manifest.layers.viz_column  contract per lib/layer_spec.py

The math lives in pipeline/viz_column/ (pure functions, unit-tested);
this file is I/O + assembly only. Run AFTER fetch_visibility.py /
fetch_wind.py / fetch_waves.py / fetch_bathy.py / fetch_tides.py in the
refresh chain; any missing input degrades gracefully (column layers
simply aren't written, the map is never blocked — PRD §6 error rule).

Region scope: CA only at v1 (PRD §3 — other regions inherit the
heuristic once their input sets are verified). The module exits 0 as a
no-op for other regions.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]

try:
    from pipeline.regions import active_region
    from pipeline.lib.decode import decode_linear_png, decode_uv_png, decode_wave_png
    from pipeline.lib.encode import encode_linear_png
    from pipeline.lib.sampling import bilinear_sample
    from pipeline.viz_column import config as colcfg
    from pipeline.viz_column import model as colmodel
except ModuleNotFoundError:
    from regions import active_region
    from lib.decode import decode_linear_png, decode_uv_png, decode_wave_png
    from lib.encode import encode_linear_png
    from lib.sampling import bilinear_sample
    from viz_column import config as colcfg
    from viz_column import model as colmodel

REGION = active_region()
BBOX = REGION.bbox
OUT_DIR = REGION.data_output_dir(ROOT)

# Standard output grid — identical to viz/wind/wave (manifest grid).
GRID_W, GRID_H = 140, 110

# Encoder ranges. Below-cliff vis reuses the viz layer's 0-80 ft range
# so the frontend can decode + colour it with the existing Vis legend
# semantics; cliff depth gets its own range. These constants are
# asserted against lib/layer_spec.py by the unit layer — change both
# together.
VIZ_COLUMN_RANGE_FT = (0.0, 80.0)
VIZ_COLUMN_CLIFF_RANGE_FT = (0.0, 100.0)

# Regions the v1 heuristic runs for (PRD: CA first).
ENABLED_REGIONS = ("ca",)

# Saved dive spots for the per-spot sidecar. Mirrors the `ca` list in
# src/lib/mapData.js REGION_SAVED_SPOTS — keep the two in lockstep
# (QUESTIONS.md tracks unifying them into one shared registry; v1
# accepts the duplication to avoid a frontend refactor in a pipeline
# PR).
CA_SPOTS = [
    {"id": "monterey",  "name": "Monterey",       "lng": -121.92, "lat": 36.62},
    {"id": "morro",     "name": "Morro Bay",      "lng": -120.88, "lat": 35.36},
    {"id": "pt-concep", "name": "Pt. Conception", "lng": -120.47, "lat": 34.45},
    {"id": "santabarb", "name": "Santa Barbara",  "lng": -119.70, "lat": 34.40},
    {"id": "santacruz", "name": "Santa Cruz I.",  "lng": -119.75, "lat": 34.05},
    {"id": "malibu",    "name": "Malibu",         "lng": -118.78, "lat": 34.02},
    {"id": "catalina",  "name": "Catalina",       "lng": -118.45, "lat": 33.39},
    {"id": "lajolla",   "name": "La Jolla",       "lng": -117.28, "lat": 32.85},
    {"id": "sandiego",  "name": "San Diego",      "lng": -117.18, "lat": 32.70},
    # Anchored in the deep kelp bed west of the peninsula — mirrors the
    # mapData.js entry (added with the pointloma spot-detail bundle).
    {"id": "pointloma", "name": "Point Loma",     "lng": -117.27, "lat": 32.685},
    {"id": "coronados", "name": "Coronados",      "lng": -117.27, "lat": 32.40},
]


def _grid_axes():
    lat_axis = np.linspace(BBOX["lat_max"], BBOX["lat_min"], GRID_H)
    lng_axis = np.linspace(BBOX["lng_min"], BBOX["lng_max"], GRID_W)
    lng_grid, lat_grid = np.meshgrid(lng_axis, lat_axis)
    return lat_axis, lng_axis, lng_grid, lat_grid


def _load_inputs():
    """Load + grid-align every input. Returns None (with a log line)
    if a required input is missing — the column layer then simply
    isn't produced this cycle."""
    paths = {
        "viz": OUT_DIR / "viz_p50_ft.png",
        "wind": OUT_DIR / "wind_uv_now.png",
        "wave": OUT_DIR / "wave_now.png",
        "bathy": OUT_DIR / "bathy.png",
        "bathy_meta": OUT_DIR / "bathy.json",
    }
    missing = [k for k, p in paths.items() if not p.exists()]
    if missing:
        print(f"[viz-column] missing inputs {missing} — skipping column build")
        return None

    surface_ft = decode_linear_png(paths["viz"], 0.0, 80.0)
    u10, v10 = decode_uv_png(paths["wind"], -30.0, 30.0)
    hs_m, tp_s, _wave_dir = decode_wave_png(paths["wave"])

    meta = json.loads(paths["bathy_meta"].read_text())
    lo, hi = meta.get("depth_range_m", [0.0, 6000.0])
    bathy = decode_linear_png(paths["bathy"], float(lo), float(hi))
    _, _, lng_grid, lat_grid = _grid_axes()
    depth_m = bilinear_sample(
        bathy, int(meta["out_w"]), int(meta["out_h"]),
        lng_grid, lat_grid, bbox=BBOX)

    # Period: fill gaps with the long-period default rather than NaN —
    # a missing Tp must not NaN-poison the resuspension term.
    tp_s = np.where(np.isfinite(tp_s) & (tp_s > 1.0),
                    tp_s, colcfg.DEFAULT_SWELL_PERIOD_S)
    return {
        "surface_ft": surface_ft, "u10": u10, "v10": v10,
        "hs_m": np.where(np.isfinite(hs_m), hs_m, 0.0), "tp_s": tp_s,
        "depth_m": depth_m, "lat_grid": lat_grid,
        "dts_km": _distance_to_shore_km(depth_m),
    }


def _distance_to_shore_km(depth_m: np.ndarray) -> np.ndarray:
    """Per-cell distance to the nearest land cell (km) on the viz grid.

    The v1.1 cliff model is cross-shore-aware: upwelling shoaling is
    coastal-trapped and the offshore thermocline relaxes deeper. Land
    = NaN cells in the (resampled) bathy grid, which includes the
    Channel Islands. Euclidean distance transform with the grid's
    physical cell sizes; coarse (~10 km cells) but the model's decay
    scales are 25-40 km, so the resolution is adequate.
    """
    ocean = np.isfinite(depth_m)
    if not (~ocean).any():
        # No land in the bbox (shouldn't happen for CA) — treat all
        # cells as far offshore rather than dividing by zero.
        return np.full(depth_m.shape, 200.0)
    dlat_km = (BBOX["lat_max"] - BBOX["lat_min"]) / (GRID_H - 1) * 111.2
    mean_lat = math.radians((BBOX["lat_max"] + BBOX["lat_min"]) / 2.0)
    dlng_km = ((BBOX["lng_max"] - BBOX["lng_min"]) / (GRID_W - 1)
               * 111.2 * math.cos(mean_lat))
    return ndimage.distance_transform_edt(ocean, sampling=(dlat_km, dlng_km))


def _hours_since_high_water(events: list[dict], now: datetime) -> float | None:
    """Hours from the most recent high water to `now` from a station's
    hi/lo event list (fetch_tides.py schema). None when unavailable."""
    highs = []
    for e in events or []:
        if e.get("type") != "H":
            continue
        try:
            t = datetime.fromisoformat(e["t"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        highs.append(t)
    past = [t for t in highs if t <= now]
    if past:
        return (now - max(past)).total_seconds() / 3600.0
    if highs:  # only future highs known — count back from the next one
        return ((now - min(highs)).total_seconds() / 3600.0) % colcfg.M2_PERIOD_HOURS
    return None


def _nearest_station(stations: list[dict], lat: float, lng: float) -> dict | None:
    best, best_d2 = None, float("inf")
    for st in stations:
        d2 = (st["lat"] - lat) ** 2 + (st["lng"] - lng) ** 2
        if d2 < best_d2:
            best, best_d2 = st, d2
    return best


def _sample_grid(arr: np.ndarray, lat: float, lng: float) -> float:
    """Nearest-cell sample of a (GRID_H, GRID_W) field at lat/lng."""
    row = (BBOX["lat_max"] - lat) / (BBOX["lat_max"] - BBOX["lat_min"]) * (GRID_H - 1)
    col = (lng - BBOX["lng_min"]) / (BBOX["lng_max"] - BBOX["lng_min"]) * (GRID_W - 1)
    r = int(np.clip(round(row), 0, GRID_H - 1))
    c = int(np.clip(round(col), 0, GRID_W - 1))
    return float(arr[r, c])


def _spot_value(arr: np.ndarray, lat: float, lng: float) -> float | None:
    """Nearest-finite sample: dive spots sit on the land/sea boundary
    where the nearest cell can be NaN (land) — search a small
    neighborhood for the closest finite cell."""
    v = _sample_grid(arr, lat, lng)
    if np.isfinite(v):
        return v
    row = (BBOX["lat_max"] - lat) / (BBOX["lat_max"] - BBOX["lat_min"]) * (GRID_H - 1)
    col = (lng - BBOX["lng_min"]) / (BBOX["lng_max"] - BBOX["lng_min"]) * (GRID_W - 1)
    r0 = int(np.clip(round(row), 0, GRID_H - 1))
    c0 = int(np.clip(round(col), 0, GRID_W - 1))
    for radius in (1, 2, 3):
        window = arr[max(0, r0 - radius):r0 + radius + 1,
                     max(0, c0 - radius):c0 + radius + 1]
        finite = window[np.isfinite(window)]
        if finite.size:
            return float(finite[0])
    return None


def build_spot_sidecar(fields: dict, out: dict, now: datetime) -> dict:
    """Per-spot column profiles + 24 h cliff series for the diurnal strip."""
    tides_path = OUT_DIR / "tides.json"
    stations = []
    if tides_path.exists():
        try:
            stations = json.loads(tides_path.read_text()).get("stations", [])
        except (json.JSONDecodeError, OSError):
            stations = []

    month = now.month
    spots = {}
    for spot in CA_SPOTS:
        lat, lng = spot["lat"], spot["lng"]
        surface = _spot_value(fields["surface_ft"], lat, lng)
        bottom_m = _spot_value(fields["depth_m"], lat, lng)
        if surface is None or bottom_m is None:
            continue
        bottom_ft = bottom_m * colcfg.FT_PER_M
        cliff = _spot_value(out["cliff_ft"], lat, lng)
        below = _spot_value(out["below_ft"], lat, lng)
        if cliff is None or below is None:
            continue
        no_cliff = bottom_ft <= cliff

        # 24 h cliff series, phase-locked to the nearest station's most
        # recent high water. Without events (older tides.json or fetch
        # fallback) we publish the swing band only — the UI renders the
        # band without an hourly curve.
        series = None
        station = _nearest_station(stations, lat, lng)
        hours_since_hw = _hours_since_high_water(
            (station or {}).get("events"), now)
        if hours_since_hw is not None and not no_cliff:
            series = colmodel.cliff_series_ft(
                cliff, month,
                [hours_since_hw + h for h in range(24)])

        swing = colmodel.swing_amplitude_ft(month)
        spots[spot["id"]] = {
            "name": spot["name"],
            "lat": lat, "lng": lng,
            "bottom_ft": round(bottom_ft, 1),
            "surface_ft": round(surface, 1),
            "cliff_ft": None if no_cliff else round(cliff, 1),
            "cliff_swing_ft": None if no_cliff else round(swing, 1),
            "below_ft": None if no_cliff else round(below, 1),
            "no_cliff": bool(no_cliff),
            "cliff_series_ft": series,  # hourly, [now .. now+23h]
            "tide_station": (station or {}).get("name"),
        }
    return spots


def main() -> int:
    if REGION.name not in ENABLED_REGIONS:
        print(f"[viz-column] region={REGION.name} not enabled at v1 — no-op")
        return 0

    now = datetime.now(timezone.utc)
    fields = _load_inputs()
    if fields is None:
        return 0  # degrade gracefully; never block the refresh

    out = colmodel.column(
        surface_vis_ft=fields["surface_ft"],
        bottom_ft=fields["depth_m"] * colcfg.FT_PER_M,
        month=now.month,
        lat_deg=fields["lat_grid"],
        u10=fields["u10"], v10=fields["v10"],
        hs_m=fields["hs_m"], period_s=fields["tp_s"],
        dts_km=fields["dts_km"],
    )

    # Mask to the surface model's ocean coverage: no surface vis cell,
    # no column cell (keeps land + nodata identical between layers).
    ocean = np.isfinite(fields["surface_ft"])
    below = np.where(ocean, out["below_ft"], np.nan)
    cliff = np.where(ocean, out["cliff_ft"], np.nan)
    out["below_ft"], out["cliff_ft"] = below, cliff

    encode_linear_png(below, *VIZ_COLUMN_RANGE_FT,
                      OUT_DIR / "viz_column_below_ft.png")
    encode_linear_png(cliff, *VIZ_COLUMN_CLIFF_RANGE_FT,
                      OUT_DIR / "viz_column_cliff_ft.png")
    print(f"  wrote viz_column_below_ft.png + viz_column_cliff_ft.png "
          f"({GRID_H}x{GRID_W})")

    spots = build_spot_sidecar(fields, out, now)
    spots_doc = {
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "method": "viz_column v1 heuristic",
        "spots": spots,
    }
    (OUT_DIR / "viz_column_spots.json").write_text(
        json.dumps(spots_doc, indent=2) + "\n")
    print(f"  wrote viz_column_spots.json ({len(spots)} spots)")

    # Manifest merge — mirror fetch_visibility.py's pattern: preserve
    # everything we didn't write, replace only our layer.
    manifest_path = OUT_DIR / "manifest.json"
    manifest = (json.loads(manifest_path.read_text())
                if manifest_path.exists() else {"layers": {}})
    manifest.setdefault("layers", {})
    ts = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    manifest["layers"]["viz_column"] = {
        "range_ft": list(VIZ_COLUMN_RANGE_FT),
        "cliff_range_ft": list(VIZ_COLUMN_CLIFF_RANGE_FT),
        "scale": "linear",
        "unit": "ft",
        "grid": {"width": GRID_W, "height": GRID_H},
        "source": "viz_column v1 heuristic (DERIVED)",
        "method": ("two-layer column: existing surface vis above a "
                   "seasonal-MLD cliff modulated by upwelling, with "
                   "swell-resuspension + upwelling attenuation below; "
                   "internal-tide swing phase-locked to CO-OPS tides"),
        "beta": True,
        "beta_reason": ("v1 heuristic — below-cliff estimate is modeled, "
                        "uncalibrated until the C4 harness lands"),
        "swing_ft": round(colmodel.swing_amplitude_ft(now.month), 1),
        "spots_url": "/data/viz_column_spots.json",
        "generated_at": ts,
        "windows": {
            "now": {
                "url": "/data/viz_column_below_ft.png",
                "cliff_url": "/data/viz_column_cliff_ft.png",
                "valid_at": ts,
            },
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json (viz_column)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
