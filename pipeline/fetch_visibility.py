"""Run the viz_predict model over the bbox grid; encode predicted Secchi
visibility (ft) and a quality-flag raster as PNGs the frontend can read.

Inputs (all real where available, with documented graceful fallbacks):
  - public/data/sst_1d.png            — today's SST (degC), source pipeline=fetch.py
  - public/data/chl_1d.png            — most recent valid chlorophyll-a (mg/m³)
  - public/data/wind_uv_now.png       — today's HRRR U/V wind (m/s) — d-0
  - public/data/wind_uv_d-{1..4}.png  — prior-4-days GFS analyses (5-day stack)
  - public/data/wave_now.png          — today's WaveWatch III H/T/dir
  - public/data/wave_max_3d.png       — 3-day max H/T (storm history)
  - public/data/precip_7d.png         — NOAA CPC 7-day cumulative rainfall
  - public/data/rivers.json           — USGS NWIS recent + climo discharge
  - public/data/tides.json            — NOAA CO-OPS today's tide range per station
  - public/data/sst_climo.png         — month-of-year SST climatology
  - public/data/chl_climo.png         — month-of-year chl climatology
  - public/data/chl_climo_annual.png  — annual-mean chl baseline
  - pipeline/static_substrate.json    — approximate kelp + sandy regions
  - public/data/land.geojson          — coastline, used for distances/normals

Outputs in public/data/:
  - viz_p10/p50/p90_ft.png            — Secchi (ft), 8-bit linear 0..80
  - viz_quality.png                   — quality flag mapped to 1..7 (0 = no data)

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
PIPELINE_DIR = ROOT / "pipeline"


# ---- Helpers --------------------------------------------------------------

def _km_to(lat_a, lng_a, lat_b, lng_b):
    """Equirectangular km distance — accurate enough at our bbox latitudes
    and ~10x faster than haversine on a grid."""
    clat = 0.5 * (BBOX["lat_min"] + BBOX["lat_max"])
    dx = (lng_a - lng_b) * 111.0 * np.cos(np.deg2rad(clat))
    dy = (lat_a - lat_b) * 111.0
    return np.sqrt(dx * dx + dy * dy)


def shelf_depth_from_dist(dist_to_shore_km, dist_to_island_km=None):
    """Crude shelf bathymetry: 0–5 km from-shore ramp to 200 m, 5–15 km
    steep slope to ~1500 m, then deep abyss to 4000 m.

    `dist_to_island_km` (optional, set after the 2026-05-05 island-shelf
    fix): each Channel/Coastal Island has its OWN shelf, dropping to
    abyssal depth in 5–15 km offshore (much like the mainland). Without
    accounting for this, San Clemente Island at 75 km from the
    mainland was being treated as 4000 m water — wrong by 3 orders of
    magnitude vs the real 5–50 m on its shelf. Two downstream effects
    were being silently zeroed for SCI / Catalina / SBI / SNI:
      * bottom_stir clipped to ~0 (no swell turbidity penalty)
      * tide_index clipped to 0 (no tide stirring)
    Both of those terms matter on island shelves and show up in shore-
    dive observations, so passing the island distance and using
    min(mainland, island) as the effective shoreline distance is the
    right call. For points truly out in the bight basin (>15 km from
    everything) the math is unchanged.
    """
    d_main = np.asarray(dist_to_shore_km, dtype=np.float32)
    if dist_to_island_km is not None:
        d_isl = np.asarray(dist_to_island_km, dtype=np.float32)
        # Whichever shoreline (mainland or named island) is closer drives
        # the shelf depth. Bay islets etc. are already filtered out of
        # dti via static_fields, so this can't be polluted by tiny features.
        d = np.minimum(d_main, d_isl)
    else:
        d = d_main
    out = np.empty_like(d)
    near = d < 5.0
    mid  = (d >= 5.0) & (d < 15.0)
    far  = d >= 15.0
    out[near] = 40.0 * d[near]                       # 0..200
    out[mid]  = 200.0 + 130.0 * (d[mid] - 5.0)        # 200..1500
    out[far]  = np.minimum(1500.0 + 50.0 * (d[far] - 15.0), 4000.0)
    return np.clip(out, 1.0, 4000.0)


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


def decode_log10_png(path: Path, lo: float, hi: float):
    """8-bit grayscale where 0=NaN, 1..255 maps to log10 lo..hi."""
    img = np.array(Image.open(path))
    valid = img > 0
    log_lo = np.log10(lo)
    log_hi = np.log10(hi)
    out = np.full(img.shape, np.nan, dtype=np.float32)
    out[valid] = 10.0 ** (log_lo + ((img[valid].astype(np.float32) - 1) / 254) * (log_hi - log_lo))
    return out


def decode_age_png(path: Path):
    """Decode an age-days sidecar PNG (mode='L'). Convention: pixel
    value 0 = no data (encoded as 999.0 in the output to make
    downstream conditionals cleaner — `np.isnan` checks are noisy and
    `999.0` is well above any realistic age threshold). Pixel 1..255
    maps to age = pixel - 1 days.

    Returned array has the same orientation as the source PNG —
    row 0 = lat_max — so it can be passed straight to
    `bilinear_sample`.
    """
    img = np.array(Image.open(path))
    out = np.full(img.shape, 999.0, dtype=np.float32)
    valid = img > 0
    out[valid] = img[valid].astype(np.float32) - 1.0
    return out


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
    islands_all = feats[1:]

    # Filter "islands" down to the named Channel/Coastal islands that
    # CHANNEL_ISLAND_CENTROIDS recognizes. The previous behaviour treated
    # every non-mainland polygon (Coronado bay islets, harbor breakwaters,
    # Mission Bay islands, kelp islets) as a real "island", which made
    # San Diego shore points fall within 10 km of "an island" and
    # classify as `bight_islands`. That mis-classification dragged the
    # bight_islands secchi calibration DOWN to fit shore-dive observations
    # (a=8.0 → 6.5 in v3.3) — and that drag also affected the GENUINE
    # bight_islands locations like San Clemente, Catalina, San Nicolas
    # which now under-predict viz vs reality.
    #
    # See pipeline/viz_predict/zones.py for the named centroid list.
    # NOTE on import path: this script is run as `python pipeline/fetch_visibility.py`
    # which puts `pipeline/` on sys.path, so the module lives at
    # `viz_predict.config` (not `pipeline.viz_predict.config`).
    # Other imports in this file follow the same convention.
    from viz_predict.config import CHANNEL_ISLAND_CENTROIDS
    NAMED_ISLAND_DISTANCE_KM = 25.0  # generous so any point of e.g. Santa Cruz I.
                                     # (long island, ~30 km tip-to-tip) is covered
    centroid_pts = np.array([(c[1], c[0]) for c in CHANNEL_ISLAND_CENTROIDS.values()])
    centroid_km_for_filter = (centroid_pts - np.array(
        [BBOX["lng_min"], BBOX["lat_min"]]
    )) * np.array([
        111.0 * np.cos(np.deg2rad((BBOX["lat_min"] + BBOX["lat_max"]) * 0.5)),
        111.0,
    ])

    def _polygon_belongs_to_named_island(feat):
        pts = _all_points(feat["geometry"])
        if pts.size == 0:
            return False
        # Project this polygon's points to km then to the centroid frame.
        x = (pts[..., 0] - BBOX["lng_min"]) * 111.0 * np.cos(np.deg2rad(
            (BBOX["lat_min"] + BBOX["lat_max"]) * 0.5
        ))
        y = (pts[..., 1] - BBOX["lat_min"]) * 111.0
        feat_km = np.stack([x, y], axis=-1)
        # If ANY centroid is within NAMED_ISLAND_DISTANCE_KM of ANY point of
        # this polygon, treat it as a real island. Long islands (Santa Cruz,
        # Catalina) extend well beyond their centroid; the per-point check
        # avoids wrongly excluding their tips.
        from scipy.spatial import cKDTree as _Tree
        tree = _Tree(feat_km)
        d, _ = tree.query(centroid_km_for_filter, k=1)
        return bool(np.any(d < NAMED_ISLAND_DISTANCE_KM))

    islands = [f for f in islands_all if _polygon_belongs_to_named_island(f)]

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


# ---- River + tide nearest-station spreading -------------------------------

def _spread_rivers(rivers_json: Path, grid_lat, grid_lng):
    """For each grid cell, pick the nearest river-mouth gauge and assign its
    discharge + climo to that cell. The model decays runoff with
    dist_to_river_km already, so picking the nearest gauge is sufficient.
    """
    data = json.loads(rivers_json.read_text())
    rivers = data.get("rivers", [])
    if not rivers:
        return np.full(grid_lat.shape, 5.0), np.full(grid_lat.shape, 5.0)
    pts = np.array([[r["lat"], r["lng"]] for r in rivers])
    disch = np.array([r["discharge_cfs"] for r in rivers], dtype=np.float32)
    climo = np.array([r["climo_cfs"] for r in rivers], dtype=np.float32)
    h, w = grid_lat.shape
    out_d = np.empty((h, w), dtype=np.float32)
    out_c = np.empty((h, w), dtype=np.float32)
    flat_lat = grid_lat.ravel()
    flat_lng = grid_lng.ravel()
    for i in range(flat_lat.size):
        d_km = _km_to(pts[:, 0], pts[:, 1], flat_lat[i], flat_lng[i])
        k = int(np.argmin(d_km))
        out_d.flat[i] = disch[k]
        out_c.flat[i] = climo[k]
    return out_d, out_c


def _spread_tides(tides_json: Path, grid_lat, grid_lng):
    """Nearest CO-OPS station per cell. Tide-energy mixing varies smoothly
    along the coast so a Voronoi-style assignment is fine."""
    data = json.loads(tides_json.read_text())
    stations = data.get("stations", [])
    if not stations:
        return np.full(grid_lat.shape, 1.5)
    pts = np.array([[s["lat"], s["lng"]] for s in stations])
    rng = np.array([s["range_m"] for s in stations], dtype=np.float32)
    h, w = grid_lat.shape
    out = np.empty((h, w), dtype=np.float32)
    flat_lat = grid_lat.ravel()
    flat_lng = grid_lng.ravel()
    for i in range(flat_lat.size):
        d_km = _km_to(pts[:, 0], pts[:, 1], flat_lat[i], flat_lng[i])
        out.flat[i] = rng[int(np.argmin(d_km))]
    return out


# ---- Static kelp + substrate masks ---------------------------------------

def _static_substrate_masks(grid_lat, grid_lng):
    """Decode pipeline/static_substrate.json into (is_kelp, is_sandy) bool
    grids. Each region is a (lat, lng, radius_km) circle; cells inside ANY
    circle in the corresponding list are tagged True."""
    path = PIPELINE_DIR / "static_substrate.json"
    is_kelp = np.zeros(grid_lat.shape, dtype=bool)
    is_sandy = np.zeros(grid_lat.shape, dtype=bool)
    if not path.exists():
        return is_kelp, is_sandy
    cfg = json.loads(path.read_text())
    for circle in cfg.get("kelp_circles", []):
        d = _km_to(grid_lat, grid_lng, circle["lat"], circle["lng"])
        is_kelp |= d <= float(circle["radius_km"])
    for circle in cfg.get("sandy_circles", []):
        d = _km_to(grid_lat, grid_lng, circle["lat"], circle["lng"])
        is_sandy |= d <= float(circle["radius_km"])
    return is_kelp, is_sandy


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
    sst_src = decode_linear_png(sst_path, 9.0, 25.0)             # degC
    chl_src = decode_log10_png(chl_path, 0.05, 20.0)              # mg/m³ (log10-encoded)
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

    # ---- Climatology PNGs (sst + chl + chl annual) ----------------------
    # The model's persistence_with_decay does `np.log(climo)` so a single
    # NaN climo cell propagates to a NaN visibility output. Any cell where
    # BOTH today's obs and the climo are missing (e.g. SoCal Bight under
    # heavy marine layer for the climo's sample days, plus VIIRS NRT 404s
    # for today) used to render as a hatched no-data zone. Cascade through
    # this-month climo → annual climo → today's obs → a global default so
    # the model always has something finite to anchor on.
    SST_DEFAULT_C = 16.0   # rough CA-coast annual mean
    CHL_DEFAULT_MGPM3 = 1.0  # rough CA-coast annual mean

    sst_climo_path = OUT_DIR / "sst_climo.png"
    chl_climo_path = OUT_DIR / "chl_climo.png"
    chl_annual_path = OUT_DIR / "chl_climo_annual.png"

    if sst_climo_path.exists():
        sst_climo_src = decode_linear_png(sst_climo_path, 9.0, 25.0)
        sst_climo_grid = bilinear_sample(sst_climo_src, sst_climo_src.shape[1], sst_climo_src.shape[0], lng_grid, lat_grid)
        print(f"  using SST climo: {np.nanmean(sst_climo_grid):.2f} °C mean "
              f"({np.isnan(sst_climo_grid).mean() * 100:.0f}% NaN cells)")
    else:
        sst_climo_grid = np.full(lat_grid.shape, np.nan, dtype=np.float32)
        print("  sst_climo.png missing — falling back to today's SST + default")
    sst_climo_filled = np.where(np.isfinite(sst_climo_grid), sst_climo_grid, sst_today)
    sst_climo_filled = np.where(np.isfinite(sst_climo_filled), sst_climo_filled, SST_DEFAULT_C)
    sst_climo_flat = flat(sst_climo_filled)

    if chl_annual_path.exists():
        chl_annual_src = decode_log10_png(chl_annual_path, 0.05, 20.0)
        chl_annual_grid = bilinear_sample(chl_annual_src, chl_annual_src.shape[1], chl_annual_src.shape[0], lng_grid, lat_grid)
    else:
        chl_annual_grid = np.full(lat_grid.shape, np.nan, dtype=np.float32)

    if chl_climo_path.exists():
        chl_climo_src = decode_log10_png(chl_climo_path, 0.05, 20.0)
        chl_climo_grid = bilinear_sample(chl_climo_src, chl_climo_src.shape[1], chl_climo_src.shape[0], lng_grid, lat_grid)
        print(f"  using chl climo: {np.nanmean(chl_climo_grid):.3f} mg/m³ mean "
              f"({np.isnan(chl_climo_grid).mean() * 100:.0f}% NaN cells)")
    else:
        chl_climo_grid = np.full(lat_grid.shape, np.nan, dtype=np.float32)
        print("  chl_climo.png missing — falling back to today's chl + annual + default")
    # Cascade: monthly → annual → today → default. After this every cell is finite.
    chl_climo_filled = np.where(np.isfinite(chl_climo_grid), chl_climo_grid, chl_annual_grid)
    chl_climo_filled = np.where(np.isfinite(chl_climo_filled), chl_climo_filled, chl_today)
    chl_climo_filled = np.where(np.isfinite(chl_climo_filled), chl_climo_filled, CHL_DEFAULT_MGPM3)
    chl_climo_doy_flat = flat(chl_climo_filled)

    chl_annual_filled = np.where(np.isfinite(chl_annual_grid), chl_annual_grid, chl_climo_filled)
    chl_climo_annual_flat = flat(chl_annual_filled)

    # Today's SST is used for the anomaly term (sst_today - sst_climo). NaN
    # cells (over land, gap-filled holes) need to fall back to the climo so
    # anomaly = 0 rather than NaN.
    sst_today_filled = np.where(np.isfinite(sst_today), sst_today, sst_climo_filled)
    sst_today_flat = flat(sst_today_filled)
    nan_sst_pct = np.isnan(sst_today).mean() * 100
    if nan_sst_pct > 1:
        print(f"  sst_today: filled {nan_sst_pct:.0f}% NaN cells with climo")

    # Today's wind feeds upwelling + exposure. NaN cells fall back to 0 (calm).
    u_today_filled = np.where(np.isfinite(u_today), u_today, 0.0)
    v_today_filled = np.where(np.isfinite(v_today), v_today, 0.0)

    # ---- 5-day wind history --------------------------------------------
    # d-0 is today's wind (already decoded as u_today/v_today). We layer in
    # d-1..d-4 from the GFS-history PNGs, falling back to d-0 if a slot is
    # missing (e.g. NOMADS purged that day).
    u_stack = [flat(u_today_filled)]
    v_stack = [flat(v_today_filled)]
    history_used = 1
    for k in range(1, 5):
        hp = OUT_DIR / f"wind_uv_d-{k}.png"
        if hp.exists():
            uh, vh = decode_uv_png(hp, -30.0, 30.0)
            uh_g = bilinear_sample(uh, uh.shape[1], uh.shape[0], lng_grid, lat_grid)
            vh_g = bilinear_sample(vh, vh.shape[1], vh.shape[0], lng_grid, lat_grid)
            u_stack.append(flat(np.where(np.isfinite(uh_g), uh_g, u_today_filled)))
            v_stack.append(flat(np.where(np.isfinite(vh_g), vh_g, v_today_filled)))
            history_used += 1
        else:
            u_stack.append(flat(u_today_filled))
            v_stack.append(flat(v_today_filled))
    u_5d = np.stack(u_stack, axis=-1)  # (n, 5)
    v_5d = np.stack(v_stack, axis=-1)
    print(f"  wind 5-day stack: {history_used}/5 real days, rest tile from today")

    # CA-coast monthly alongshore-wind climatology (m/s, positive =
    # upwelling-favorable, NW→SE component along a coast normal of 295°).
    # Sourced from NDBC buoy long-term means, latitude-averaged across
    # 32–37°N. Replaces the previous along_climo_5d=0 hardcode which
    # made every normal upwelling-season day register as +5–7 m/s
    # "anomalous" — that drove the upwell coefficient to over-predict
    # chl all summer. A proper per-pixel ERA5 climatology is the next
    # step; this gets the seasonal bias right within ~1 m/s.
    #
    # Indexed by month (Jan=index 0). Peak upwelling May–Jul, relaxed
    # Oct–Feb. The number is the alongshore COMPONENT, not wind speed
    # — so 6 m/s alongshore in June is consistent with a typical
    # 8 m/s NW wind (sin/cos projection through the coast normal).
    ALONG_CLIMO_BY_MONTH = np.array([
        1.0,  # Jan
        1.5,  # Feb
        3.5,  # Mar
        4.5,  # Apr
        5.5,  # May
        6.5,  # Jun
        5.5,  # Jul
        4.5,  # Aug
        3.5,  # Sep
        2.0,  # Oct
        1.0,  # Nov
        0.5,  # Dec
    ], dtype=np.float64)
    _today_month = datetime.now(timezone.utc).month
    _along_climo_value = float(ALONG_CLIMO_BY_MONTH[_today_month - 1])
    along_climo_5d = np.full(n, _along_climo_value, dtype=np.float64)
    print(f"  alongshore-wind climo: {_along_climo_value:.1f} m/s (month {_today_month})")

    # ---- Waves: today (now) + 3-day max envelope -----------------------
    wave_path = OUT_DIR / "wave_now.png"
    wave_max_path = OUT_DIR / "wave_max_3d.png"
    if wave_path.exists():
        wave_h_src, wave_p_src, wave_d_src = decode_wave_png(wave_path)
        wave_h = bilinear_sample(wave_h_src, wave_h_src.shape[1], wave_h_src.shape[0], lng_grid, lat_grid)
        wave_p = bilinear_sample(wave_p_src, wave_p_src.shape[1], wave_p_src.shape[0], lng_grid, lat_grid)
        wave_d = bilinear_sample(wave_d_src, wave_d_src.shape[1], wave_d_src.shape[0], lng_grid, lat_grid)
        swell_height_today = flat(np.where(np.isfinite(wave_h), wave_h, 0.0))
        swell_dir_deg = flat(np.where(np.isfinite(wave_d), wave_d, 270.0))
        print(f"  using WW3 now: height mean {np.nanmean(wave_h):.2f} m, period mean {np.nanmean(wave_p):.1f} s")
    else:
        swell_height_today = np.zeros(n)
        swell_dir_deg = np.full(n, 270.0)
        print("  wave_now.png missing — zero swell")

    if wave_max_path.exists():
        wmh_src, wmp_src, _ = decode_wave_png(wave_max_path)
        wmh = bilinear_sample(wmh_src, wmh_src.shape[1], wmh_src.shape[0], lng_grid, lat_grid)
        wmp = bilinear_sample(wmp_src, wmp_src.shape[1], wmp_src.shape[0], lng_grid, lat_grid)
        sig_wave_height_3d_max = flat(np.where(np.isfinite(wmh), wmh, swell_height_today.reshape(shape2d)))
        peak_period_3d_max     = flat(np.where(np.isfinite(wmp), wmp, 10.0))
        print(f"  using WW3 3d max: height mean {np.nanmean(wmh):.2f} m, period mean {np.nanmean(wmp):.1f} s")
    else:
        sig_wave_height_3d_max = swell_height_today.copy()
        peak_period_3d_max = np.full(n, 10.0)
        print("  wave_max_3d.png missing — using today's wave as 3d max")

    # ---- Precip ----------------------------------------------------------
    precip_path = OUT_DIR / "precip_7d.png"
    if precip_path.exists():
        precip_src = decode_linear_png(precip_path, 0.0, 200.0)
        precip_grid = bilinear_sample(precip_src, precip_src.shape[1], precip_src.shape[0], lng_grid, lat_grid)
        precip_7d = flat(np.where(np.isfinite(precip_grid), precip_grid, 0.0))
        if np.nanmax(precip_grid) > 0.5:
            print(f"  using CPC precip: max {np.nanmax(precip_grid):.1f} mm")
        else:
            print("  using CPC precip: dry week (<0.5 mm)")
    else:
        precip_7d = np.zeros(n)
        print("  precip_7d.png missing — zero precip")

    # ---- Rivers (USGS NWIS, nearest gauge per cell) --------------------
    rivers_json_path = OUT_DIR / "rivers.json"
    if rivers_json_path.exists():
        river_disch, river_climo = _spread_rivers(rivers_json_path, lat_grid, lng_grid)
        river_disch = flat(river_disch); river_climo = flat(river_climo)
        print(f"  using USGS rivers: discharge mean {np.nanmean(river_disch):.0f} cfs (cell-avg)")
    else:
        river_disch = np.full(n, 5.0)
        river_climo = np.full(n, 5.0)
        print("  rivers.json missing — using flat 5 cfs (anomaly=0)")

    # ---- Tides (CO-OPS, nearest station per cell) ----------------------
    tides_json_path = OUT_DIR / "tides.json"
    if tides_json_path.exists():
        tide_grid = _spread_tides(tides_json_path, lat_grid, lng_grid)
        tide_range = flat(tide_grid)
        print(f"  using CO-OPS tides: range mean {np.nanmean(tide_range):.2f} m")
    else:
        tide_range = np.full(n, 1.5)
        print("  tides.json missing — using flat 1.5 m")

    # ---- Substrate / kelp (static) -------------------------------------
    is_kelp_grid, is_sandy_grid = _static_substrate_masks(lat_grid, lng_grid)
    is_kelp = flat(is_kelp_grid)
    is_sandy = flat(is_sandy_grid)
    print(f"  static substrate: {is_kelp.sum()} kelp cells, {is_sandy.sum()} sandy cells")

    # ---- Cloud fraction proxy ------------------------------------------
    # Treat NaN/missing chl as a proxy for clouds blocking the satellite over
    # the past week. This is rough — chl gap-fill already imputes — but it
    # captures the "we couldn't see, so we're less confident" signal.
    cloud_frac_grid = np.where(np.isnan(chl_today), 0.85, 0.45)
    cloud_frac = flat(cloud_frac_grid)

    # ---- Per-cell chl freshness ---------------------------------------
    # `fetch.py:build_layer` walks back up to 7 days hunting for non-cloudy
    # chl pixels and writes whatever it found into chl_1d.png. Without
    # the age sidecar we hardcode age=0 — telling the model "this is
    # today's observation" even when the actual data is 4 days stale.
    # Three downstream consequences if we lie about freshness:
    #   * persistence_with_decay weight stays at 1.0 (should decay to
    #     ~0.07–0.51 at real ages, blending in climatology)
    #   * effective_sigma keeps p10/p90 narrow (calibration over-confident)
    #   * assign_quality flags everything OBSERVED_1D (the bottom of the
    #     audit trail — diver thinks the model has fresh satellite when
    #     it doesn't)
    #
    # Read the sidecar fetch.py emits, bilinear-resample onto our prediction
    # grid, then gate `chl_obs_today` on age==0 so assign_quality correctly
    # downgrades stale cells to OBSERVED_3D / PREDICTED_*. `chl_lastvalid`
    # carries the older fallback for persistence_with_decay.
    chl_age_path = OUT_DIR / "chl_1d_age_days.png"
    if chl_age_path.exists():
        age_grid = decode_age_png(chl_age_path)
        age_resampled = bilinear_sample(
            age_grid, age_grid.shape[1], age_grid.shape[0], lng_grid, lat_grid
        )
        # Bilinear can interpolate an age across a NaN boundary; force
        # 999 wherever today's chl itself is NaN so we never claim a
        # finite age for a cell with no observation at all.
        age_resampled = np.where(np.isnan(chl_today), 999.0, age_resampled)
        valid_age = age_resampled < 999
        n_total = int(valid_age.sum())
        n_stale = int(((age_resampled > 0) & valid_age).sum())
        mean_age = float(age_resampled[valid_age].mean()) if n_total else 0.0
        print(
            f"  chl freshness: {n_stale}/{n_total} cells stale, mean age {mean_age:.2f} days"
        )
    else:
        # Pre-PR1 deployment — fall back to legacy behaviour (assume
        # everything is fresh) so this path doesn't crash old runs that
        # haven't regenerated the sidecar yet.
        print("  chl_1d_age_days.png missing — assuming chl is fresh (legacy behaviour)")
        age_resampled = np.where(np.isnan(chl_today), 999.0, 0.0)

    # `chl_obs_today` = TODAY's actual observation (NaN if stale).
    # `chl_lastvalid` = most recent valid obs at any age (drives
    # persistence_with_decay).
    chl_today_fresh_only = np.where(age_resampled == 0, chl_today, np.nan)
    chl_obs_today = flat(chl_today_fresh_only)
    chl_lastvalid = flat(chl_today)
    age = flat(age_resampled)

    # ---- Kd_490 (Phase 2) -----------------------------------------------
    # Mirror the chl freshness pipeline: decode log10 PNG + age sidecar,
    # bilinear-resample onto our grid, gate by age. The model blends Kd
    # against the chl-derived Secchi (predict.py) when fresh and falls
    # back to the chl-only path when Kd is missing/stale — so a
    # missing kd490_1d.png is a graceful no-op, not an error.
    kd490_path = OUT_DIR / "kd490_1d.png"
    kd490_age_path = OUT_DIR / "kd490_1d_age_days.png"
    if kd490_path.exists() and kd490_age_path.exists():
        kd_src = decode_log10_png(kd490_path, 0.02, 10.0)
        kd_grid = bilinear_sample(kd_src, kd_src.shape[1], kd_src.shape[0], lng_grid, lat_grid)
        kd_age_src = decode_age_png(kd490_age_path)
        kd_age_grid = bilinear_sample(
            kd_age_src, kd_age_src.shape[1], kd_age_src.shape[0], lng_grid, lat_grid
        )
        # Same defensive clamp as chl: bilinear can interpolate an age
        # across a NaN boundary; force 999 wherever Kd itself is NaN.
        kd_age_grid = np.where(np.isnan(kd_grid), 999.0, kd_age_grid)
        valid_kd = kd_age_grid < 999
        n_kd_valid = int(valid_kd.sum())
        if n_kd_valid:
            mean_age_kd = float(kd_age_grid[valid_kd].mean())
            n_fresh = int((kd_age_grid == 0).sum())
            print(
                f"  kd490: {n_kd_valid}/{kd_grid.size} valid cells, "
                f"mean age {mean_age_kd:.2f} days, {n_fresh} fresh (age=0)"
            )
        else:
            print("  kd490: PNGs present but no valid cells — blend will no-op")
        kd490_obs_today = flat(kd_grid)
        kd490_age_days = flat(kd_age_grid)
    else:
        print("  kd490 PNGs missing — falling back to chl-only model path")
        kd490_obs_today = None
        kd490_age_days = None

    print("Running viz_predict over the grid (n=%d)..." % n)
    # Pass dti_km so the island shelves are recognized — see
    # shelf_depth_from_dist's docstring for why.
    depth_m = flat(shelf_depth_from_dist(dts_km, dti_km))
    result = viz_predict.predict_all(
        lat=flat(lat_grid), lng=flat(lng_grid),
        depth_m=depth_m,
        dist_to_shore_km=flat(dts_km),
        dist_to_island_km=flat(dti_km),
        dist_to_river_km=flat(dist_to_river_km),
        coast_normal_deg=flat(coast_normal_deg),
        is_kelp=is_kelp,
        is_sandy=is_sandy,
        chl_obs_today=chl_obs_today,
        chl_lastvalid=chl_lastvalid,
        chl_lastvalid_age_days=age,
        chl_climo_doy=chl_climo_doy_flat,
        chl_climo_annual=chl_climo_annual_flat,
        sst_today=sst_today_flat,
        sst_climo=sst_climo_flat,
        u_wind_5d=u_5d, v_wind_5d=v_5d, along_climo_5d=along_climo_5d,
        u_wind_today=flat(u_today_filled), v_wind_today=flat(v_today_filled),
        sig_wave_height_3d_max=sig_wave_height_3d_max,
        peak_period_3d_max=peak_period_3d_max,
        swell_dir_today_deg=swell_dir_deg,
        swell_height_today_m=swell_height_today,
        precip_7d_mm=precip_7d,
        river_discharge_cfs=river_disch, river_climo_cfs=river_climo,
        tide_range_today_m=tide_range,
        cloud_fraction_7d=cloud_frac,
        kd490_obs_today=kd490_obs_today,
        kd490_age_days=kd490_age_days,
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

    # ---- Validation archive snapshot ----------------------------------
    # Persist the per-cell prediction (with the active config's SHA) so
    # the validation pipeline can later compare ground-truth observations
    # against this run. Adds ~700 KB gzipped per day; non-fatal if it
    # fails — the live site doesn't depend on the archive.
    try:
        from validation import archive as _viz_archive
        _viz_archive.write_snapshot(
            grid_lat=flat(lat_grid),
            grid_lng=flat(lng_grid),
            predict_result=result,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  archive snapshot failed (non-fatal): {exc}")


if __name__ == "__main__":
    main()
