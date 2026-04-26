"""Run the viz_predict model over the bbox grid; encode predicted Secchi
visibility (ft) and a quality-flag raster as PNGs the frontend can read.

Inputs (everything wired today):
  - public/data/sst_1d.png         — today's SST (degC), source pipeline=fetch.py
  - public/data/chl_1d.png         — most recent valid chlorophyll-a (mg/m³)
  - public/data/wind_uv_now.png    — today's HRRR/GFS U/V wind (m/s)
  - public/data/land.geojson       — coastline, used for per-pixel coast_normal
                                     and distance-to-shore/islands

Inputs deferred (passed as conservative defaults; model still runs):
  - swell direction + height       — pending NOAA WaveWatch III fetcher
  - 7-day precip                   — pending NOAA gridded precip fetcher
  - river discharge                — pending USGS NWIS fetcher
  - tide range                     — pending NOAA CO-OPS fetcher
  - 5-day wind history             — using today's wind tiled along the time axis
  - real chl + SST climatology     — using today's chl/SST as proxy (anomaly = 0)
  - is_kelp / is_sandy             — False everywhere

Outputs in public/data/:
  - viz_p50_ft.png                 — Secchi p50 in feet, 8-bit linear 0..80
  - viz_quality.png                — quality flag mapped to 1..7 (0 = no data)

Run: python pipeline/fetch_visibility.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial import cKDTree

from viz_predict import predict as viz_predict

# Match the existing app's bbox.
BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)

# Output grid (regular lat/lng over bbox). Same shape as wind for visual consistency.
GRID_W, GRID_H = 140, 110

# Encoding ranges
VIZ_RANGE_FT = (0.0, 80.0)

# Quality-flag mapping for the viz_quality PNG.
QUALITY_CODES = {
    "OBSERVED_1D":         1,
    "OBSERVED_3D":         2,
    "INTERPOLATED":        3,
    "PREDICTED_HIGH_CONF": 4,
    "PREDICTED_MED_CONF":  5,
    "PREDICTED_LOW_CONF":  6,
    "CLIMATOLOGY_ONLY":    7,
}

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public" / "data"


# ---- PNG decoders matching dataSource.js encoding -------------------------

def decode_linear_png(path: Path, lo: float, hi: float):
    """8-bit grayscale: 0=NaN, 1..255 linear from lo..hi."""
    img = np.array(Image.open(path))  # mode L, shape (h, w)
    out = np.full(img.shape, np.nan, dtype=np.float32)
    valid = img > 0
    out[valid] = lo + ((img[valid].astype(np.float32) - 1) / 254) * (hi - lo)
    return out


def decode_uv_png(path: Path, lo: float, hi: float):
    """RGBA: R=U byte, G=V byte (lo..hi linear), A=0 means NaN."""
    img = np.array(Image.open(path))  # shape (h, w, 4)
    valid = img[..., 3] > 0
    span = hi - lo
    u = np.full(img.shape[:2], np.nan, dtype=np.float32)
    v = np.full(img.shape[:2], np.nan, dtype=np.float32)
    u[valid] = lo + (img[..., 0][valid].astype(np.float32) / 255) * span
    v[valid] = lo + (img[..., 1][valid].astype(np.float32) / 255) * span
    return u, v


def decode_wave_png(path: Path):
    """Wave RGBA PNG: R=height (0..12 m), G=period (0..25 s), B=direction (0..360°)."""
    img = np.array(Image.open(path))
    valid = img[..., 3] > 0
    h = np.full(img.shape[:2], np.nan, dtype=np.float32)
    p = np.full(img.shape[:2], np.nan, dtype=np.float32)
    d = np.full(img.shape[:2], np.nan, dtype=np.float32)
    h[valid] = (img[..., 0][valid].astype(np.float32) / 255) * 12.0
    p[valid] = (img[..., 1][valid].astype(np.float32) / 255) * 25.0
    d[valid] = (img[..., 2][valid].astype(np.float32) / 255) * 360.0
    return h, p, d


def bilinear_sample(src_arr, src_w, src_h, lng_grid, lat_grid):
    """Sample src_arr at the given lng/lat using bilinear interp.
    src_arr is shape (src_h, src_w) where row 0 = lat_max."""
    fx = (lng_grid - BBOX["lng_min"]) / (BBOX["lng_max"] - BBOX["lng_min"]) * (src_w - 1)
    fy = (BBOX["lat_max"] - lat_grid) / (BBOX["lat_max"] - BBOX["lat_min"]) * (src_h - 1)
    fx = np.clip(fx, 0, src_w - 1)
    fy = np.clip(fy, 0, src_h - 1)
    x0 = np.floor(fx).astype(int); x1 = np.minimum(x0 + 1, src_w - 1)
    y0 = np.floor(fy).astype(int); y1 = np.minimum(y0 + 1, src_h - 1)
    tx = fx - x0; ty = fy - y0
    v00 = src_arr[y0, x0]
    v10 = src_arr[y0, x1]
    v01 = src_arr[y1, x0]
    v11 = src_arr[y1, x1]
    out = v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty) + v01 * (1 - tx) * ty + v11 * tx * ty
    return out


# ---- Static fields from land.geojson --------------------------------------

# Major CA river mouths within the bbox. Coordinates are approximate but
# good enough for the runoff_index decay (e-folding scale 5 km, so the
# precise mouth doesn't matter much beyond a few km).
RIVER_MOUTHS = [
    ("salinas",      36.747, -121.808),  # Salinas River, Monterey Bay
    ("santa-clara",  34.236, -119.265),  # Santa Clara River, Ventura
    ("ventura",      34.275, -119.302),  # Ventura River
    ("la-river",     33.748, -118.205),  # Los Angeles River
    ("santa-ana",    33.633, -117.953),  # Santa Ana River, Newport
    ("san-luis-rey", 33.215, -117.395),  # San Luis Rey River, Oceanside
    ("san-diego",    32.756, -117.221),  # San Diego River
    ("tijuana",      32.555, -117.130),  # Tijuana River
    ("carmel",       36.539, -121.929),  # Carmel River
    ("santa-ynez",   34.701, -120.597),  # Santa Ynez River, Lompoc
]


def static_fields(grid_lat, grid_lng):
    """Compute coast_normal_deg, dist_to_shore_km, dist_to_island_km,
    dist_to_river_km from the bundled land.geojson + the RIVER_MOUTHS list."""
    land = json.loads((OUT_DIR / "land.geojson").read_text())

    # Mainland is feature[0] (the largest by point count).
    feats = sorted(land["features"], key=lambda f: -_geom_point_count(f["geometry"]))
    mainland = feats[0]
    islands = feats[1:]

    mainland_pts = _all_points(mainland["geometry"])  # (N, 2) lng/lat
    island_pts = np.concatenate([_all_points(f["geometry"]) for f in islands], axis=0) \
        if islands else np.empty((0, 2))

    # cKDTree expects (x, y) — we'll use (lng, lat) but adjust for the latitude
    # squashing of lng-distance with a per-point cosine. Trees can't do that
    # natively, so first convert to a local equirectangular projection (km).
    def to_km(pts):
        # Project (lng, lat) to (x_km, y_km) using the bbox center latitude.
        clat = (BBOX["lat_min"] + BBOX["lat_max"]) * 0.5
        x = (pts[..., 0] - BBOX["lng_min"]) * 111.0 * np.cos(np.deg2rad(clat))
        y = (pts[..., 1] - BBOX["lat_min"]) * 111.0
        return np.stack([x, y], axis=-1)

    grid_pts = np.stack([grid_lng, grid_lat], axis=-1)  # (H, W, 2)
    grid_km = to_km(grid_pts).reshape(-1, 2)

    mainland_km = to_km(mainland_pts)
    island_km = to_km(island_pts) if island_pts.size else np.empty((0, 2))

    # 1) distance to mainland coast (km)
    tree_main = cKDTree(mainland_km)
    dts_km, idx_main = tree_main.query(grid_km, k=1)
    dts_km = dts_km.reshape(grid_lat.shape)

    # 2) distance to nearest island
    if island_km.size:
        tree_isl = cKDTree(island_km)
        dti_km, _ = tree_isl.query(grid_km, k=1)
        dti_km = dti_km.reshape(grid_lat.shape)
    else:
        dti_km = np.full(grid_lat.shape, 1e6)

    # 3) coast normal (degrees from north, clockwise, pointing seaward).
    # Approximate: for each grid cell, find its nearest two mainland coast
    # points; the local coast tangent is (b - a). The seaward normal is the
    # tangent rotated 90° clockwise (pointing into the ocean — west of CA).
    _, idx2 = tree_main.query(grid_km, k=2)
    a = mainland_pts[idx2[:, 0]]  # (M, 2) lng/lat
    b = mainland_pts[idx2[:, 1]]
    tx = b[:, 0] - a[:, 0]
    ty = b[:, 1] - a[:, 1]
    # Convert tangent to km space (lng squashed by latitude)
    clat = (BBOX["lat_min"] + BBOX["lat_max"]) * 0.5
    tx_km = tx * 111.0 * np.cos(np.deg2rad(clat))
    ty_km = ty * 111.0
    # Seaward normal: rotate (tx, ty) by -90° → (ty, -tx). On the CA west
    # coast that points into the Pacific (west). We then pick the sign
    # consistent with "away from coast" by flipping if the normal points
    # toward land (i.e. away from the grid point).
    nx_km = ty_km
    ny_km = -tx_km
    # Vector from coast point to grid point — should align with normal.
    gx_km = grid_km[:, 0] - mainland_km[idx_main, 0]
    gy_km = grid_km[:, 1] - mainland_km[idx_main, 1]
    sign = np.sign(nx_km * gx_km + ny_km * gy_km)
    sign = np.where(sign == 0, 1.0, sign)
    nx_km *= sign
    ny_km *= sign
    # Compass bearing from north (clockwise): atan2(east, north)
    coast_normal_deg = (np.rad2deg(np.arctan2(nx_km, ny_km)) + 360.0) % 360.0
    coast_normal_deg = coast_normal_deg.reshape(grid_lat.shape)

    # 4) distance to nearest known river mouth
    river_pts = np.array([[r[2], r[1]] for r in RIVER_MOUTHS])  # (N, 2) lng/lat
    river_km = to_km(river_pts)
    tree_river = cKDTree(river_km)
    dtr_km, _ = tree_river.query(grid_km, k=1)
    dtr_km = dtr_km.reshape(grid_lat.shape)

    return dts_km, dti_km, coast_normal_deg, dtr_km


def _geom_point_count(geom):
    if not geom:
        return 0
    if geom["type"] == "Polygon":
        return sum(len(r) for r in geom["coordinates"])
    if geom["type"] == "MultiPolygon":
        return sum(len(r) for poly in geom["coordinates"] for r in poly)
    return 0


def _all_points(geom):
    pts = []
    if not geom:
        return np.empty((0, 2))
    if geom["type"] == "Polygon":
        for ring in geom["coordinates"]:
            pts.extend(ring)
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            for ring in poly:
                pts.extend(ring)
    return np.asarray(pts, dtype=float)


# ---- Encoders -------------------------------------------------------------

def encode_linear_png(arr, lo, hi, out_path):
    valid = np.isfinite(arr)
    scaled = (arr - lo) / (hi - lo)
    px = np.zeros(arr.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def encode_quality_png(quality_str_arr, out_path):
    px = np.zeros(quality_str_arr.shape, dtype=np.uint8)
    for s, code in QUALITY_CODES.items():
        px[quality_str_arr == s] = code
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


# ---- Orchestrator ---------------------------------------------------------

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build the regular bbox grid — 1D arrays over (H * W) cells.
    lat_axis = np.linspace(BBOX["lat_max"], BBOX["lat_min"], GRID_H)  # row 0 = top = lat_max
    lng_axis = np.linspace(BBOX["lng_min"], BBOX["lng_max"], GRID_W)
    lng_grid, lat_grid = np.meshgrid(lng_axis, lat_axis)

    # Static fields (one-shot every run; cheap enough at 140x110)
    print("Computing static fields (coast normal + distances + river mouths)...")
    dts_km, dti_km, coast_normal_deg, dist_to_river_km = static_fields(lat_grid, lng_grid)

    # Existing PNG inputs
    sst_path = OUT_DIR / "sst_1d.png"
    chl_path = OUT_DIR / "chl_1d.png"
    wind_uv_path = OUT_DIR / "wind_uv_now.png"

    if not (sst_path.exists() and chl_path.exists() and wind_uv_path.exists()):
        print("Required SST/chl/wind PNGs not present — run fetch.py + fetch_wind.py first.", file=sys.stderr)
        sys.exit(1)

    # Decode source PNGs
    sst_src = decode_linear_png(sst_path, 9.0, 25.0)            # degC
    chl_src = decode_linear_png(chl_path, 0.05, 20.0)            # mg/m³ — note: chl PNG uses log10 scale
    # The chl PNG is log10-encoded (per fetch.py), so re-decode in log space.
    chl_src = decode_linear_png(chl_path, 0.05, 20.0)
    # Actually re-do: chl was encoded log10. The decode helper above is linear.
    # Use a dedicated log10 decode here:
    img = np.array(Image.open(chl_path))
    valid = img > 0
    log_lo = np.log10(0.05)
    log_hi = np.log10(20.0)
    chl_src = np.full(img.shape, np.nan, dtype=np.float32)
    chl_src[valid] = 10.0 ** (log_lo + ((img[valid].astype(np.float32) - 1) / 254) * (log_hi - log_lo))

    u_src, v_src = decode_uv_png(wind_uv_path, -30.0, 30.0)

    # Bilinear-sample each source onto our grid
    sst_today = bilinear_sample(sst_src, sst_src.shape[1], sst_src.shape[0], lng_grid, lat_grid)
    chl_today = bilinear_sample(chl_src, chl_src.shape[1], chl_src.shape[0], lng_grid, lat_grid)
    u_today = bilinear_sample(u_src,   u_src.shape[1],   u_src.shape[0],   lng_grid, lat_grid)
    v_today = bilinear_sample(v_src,   v_src.shape[1],   v_src.shape[0],   lng_grid, lat_grid)

    # Flatten to 1D for predict_all — easier than reshape-then-restore.
    shape2d = lat_grid.shape
    n = lat_grid.size
    flat = lambda a: np.asarray(a).reshape(n)

    # Climatology proxies (until real climo is wired): use today's value as
    # the climo so anomaly = 0 and seasonal_residual = 0. Still honest:
    # the model just relies on persistence + driver-anomaly contributions.
    chl_climo_doy_flat = flat(chl_today)
    chl_climo_annual_flat = flat(chl_today)
    sst_climo_flat = flat(sst_today)

    # 5-day wind history: tile today's wind. The upwelling_anomaly_5d feature
    # then collapses to (today_along - climo_along), which is what we want.
    u_5d = np.tile(flat(u_today)[:, None], (1, 5))
    v_5d = np.tile(flat(v_today)[:, None], (1, 5))
    along_climo_5d = np.zeros(n, dtype=float)  # placeholder

    # Wave data from NOAA WaveWatch III (gfswave wcoast 0.16°), if present.
    wave_path = OUT_DIR / "wave_now.png"
    if wave_path.exists():
        wave_h_src, wave_p_src, wave_d_src = decode_wave_png(wave_path)
        wave_h = bilinear_sample(wave_h_src, wave_h_src.shape[1], wave_h_src.shape[0], lng_grid, lat_grid)
        wave_p = bilinear_sample(wave_p_src, wave_p_src.shape[1], wave_p_src.shape[0], lng_grid, lat_grid)
        wave_d = bilinear_sample(wave_d_src, wave_d_src.shape[1], wave_d_src.shape[0], lng_grid, lat_grid)
        # NaN-safe defaults where waves aren't covered (over land, etc.).
        sig_wave_height = flat(np.where(np.isfinite(wave_h), wave_h, 0.0))
        peak_period     = flat(np.where(np.isfinite(wave_p), wave_p, 10.0))
        swell_dir_deg   = flat(np.where(np.isfinite(wave_d), wave_d, 270.0))
        swell_height    = sig_wave_height.copy()  # use HTSGW as today's swell height
        print(f"  using WW3 waves: height mean {np.nanmean(wave_h):.2f} m, period mean {np.nanmean(wave_p):.1f} s")
    else:
        sig_wave_height = np.zeros(n)
        peak_period = np.full(n, 10.0)
        swell_dir_deg = np.zeros(n)
        swell_height = np.zeros(n)
        print("  wave_now.png missing — passing zero swell (run pipeline/fetch_waves.py first for full prediction)")

    # Precip from CPC US Unified Daily (7-day cumulative), if present.
    precip_path = OUT_DIR / "precip_7d.png"
    if precip_path.exists():
        precip_src = decode_linear_png(precip_path, 0.0, 200.0)  # mm
        precip_grid = bilinear_sample(precip_src, precip_src.shape[1], precip_src.shape[0], lng_grid, lat_grid)
        # Land cells beyond the CPC grid (e.g. Mexican waters) come back NaN —
        # treat as 0 mm rather than fake. The runoff_index decays with
        # distance to a river mouth anyway, so far-from-river NaN cells
        # contribute zero either way.
        precip_7d = flat(np.where(np.isfinite(precip_grid), precip_grid, 0.0))
        if np.nanmax(precip_grid) > 0.5:
            print(f"  using CPC precip: max {np.nanmax(precip_grid):.1f} mm, mean {np.nanmean(precip_grid):.1f} mm")
        else:
            print("  using CPC precip: dry week, all <0.5 mm")
    else:
        precip_7d = np.zeros(n)
        print("  precip_7d.png missing — passing zero precip (run pipeline/fetch_precip.py first)")
    river_disch = np.full(n, 5.0)
    river_climo = np.full(n, 5.0)
    tide_range = np.full(n, 1.5)
    cloud_frac = np.full(n, 0.55)

    # No staleness info per cell yet — assume today's chl is the last valid
    # observation (age = 0 where chl is valid; large age elsewhere).
    chl_obs_today = flat(chl_today)
    chl_lastvalid = np.where(np.isnan(chl_obs_today), np.nan, chl_obs_today)
    age = np.where(np.isnan(chl_obs_today), 999.0, 0.0)

    print("Running viz_predict over the grid (n=%d)..." % n)
    result = viz_predict.predict_all(
        lat=flat(lat_grid), lng=flat(lng_grid),
        depth_m=np.full(n, 200.0),  # bathymetry not yet wired
        dist_to_shore_km=flat(dts_km),
        dist_to_island_km=flat(dti_km),
        dist_to_river_km=flat(dist_to_river_km),
        coast_normal_deg=flat(coast_normal_deg),
        is_kelp=np.zeros(n, dtype=bool),
        is_sandy=np.zeros(n, dtype=bool),
        chl_obs_today=chl_obs_today,
        chl_lastvalid=chl_lastvalid,
        chl_lastvalid_age_days=age,
        chl_climo_doy=chl_climo_doy_flat,
        chl_climo_annual=chl_climo_annual_flat,
        sst_today=flat(sst_today),
        sst_climo=sst_climo_flat,
        u_wind_5d=u_5d, v_wind_5d=v_5d, along_climo_5d=along_climo_5d,
        u_wind_today=flat(u_today), v_wind_today=flat(v_today),
        sig_wave_height_3d_max=sig_wave_height,
        peak_period_3d_max=peak_period,
        swell_dir_today_deg=swell_dir_deg,
        swell_height_today_m=swell_height,
        precip_7d_mm=precip_7d,
        river_discharge_cfs=river_disch, river_climo_cfs=river_climo,
        tide_range_today_m=tide_range,
        cloud_fraction_7d=cloud_frac,
    )

    viz_p50_ft = result["viz_p50_ft"].reshape(shape2d)
    viz_p10_ft = result["viz_p10_ft"].reshape(shape2d)  # turbid end
    viz_p90_ft = result["viz_p90_ft"].reshape(shape2d)  # clear end
    quality   = result["quality"].reshape(shape2d)

    print(f"  viz_p50_ft range: {np.nanmin(viz_p50_ft):.1f} – {np.nanmax(viz_p50_ft):.1f} ft, "
          f"mean {np.nanmean(viz_p50_ft):.1f}")

    encode_linear_png(viz_p50_ft, *VIZ_RANGE_FT, OUT_DIR / "viz_p50_ft.png")
    encode_linear_png(viz_p10_ft, *VIZ_RANGE_FT, OUT_DIR / "viz_p10_ft.png")
    encode_linear_png(viz_p90_ft, *VIZ_RANGE_FT, OUT_DIR / "viz_p90_ft.png")
    encode_quality_png(quality, OUT_DIR / "viz_quality.png")
    print("  wrote viz_{p10,p50,p90}_ft.png + viz_quality.png")

    # Merge into manifest
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
    manifest.setdefault("layers", {})["viz"] = {
        "range_ft": list(VIZ_RANGE_FT),
        "scale": "linear",
        "unit": "ft",
        "grid": {"width": GRID_W, "height": GRID_H},
        "source": "viz_predict (PREDICTED)",
        "windows": {
            "now": {
                "url":      "/data/viz_p50_ft.png",
                "p10_url":  "/data/viz_p10_ft.png",
                "p90_url":  "/data/viz_p90_ft.png",
                "quality_url": "/data/viz_quality.png",
                "valid_at": manifest["generated_at"],
            },
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json")


if __name__ == "__main__":
    main()
