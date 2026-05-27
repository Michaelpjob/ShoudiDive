"""Build per-spot bundles for the Spot Detail view.

For each pilot spot (id → centre lng/lat + radius km), generate a
self-contained bundle of nav-quality static assets:

  public/data/spots/<id>/
    bundle.json       — manifest tying the rest together
    bathy.png         — high-res grayscale bathymetry (linear depth encoding)
    contours.geojson  — depth contour lines derived from the DEM
    coastline.geojson — high-res OSM coastline, clipped to spot bbox
    kelp.geojson      — CDFW admin kelp beds, clipped to spot bbox
    mpa.geojson       — CDFW MPA polygons, clipped to spot bbox

  public/data/spots/
    index.json        — { spots: [...], generated_at }

The frontend (SpotDetailView.jsx) reads index.json on app boot to know
which saved-spot pins have a "View detailed map" affordance, then loads
the per-spot bundle on demand when the user clicks the affordance.

Design choices vs. the handover (`docs/spot-detail-handover.md`):

  * DEM source: GMRT high-resolution mode rather than NOAA CUDEM. GMRT
    high mode gives ~100 m native near the coast, which is plenty for
    a 480 × 480 spot view (one screen pixel ≈ 16-30 m on the ground at
    4-8 km radius). CUDEM 1/9 arc-sec (~3 m) would be sharper but
    requires THREDDS subsetting + a tile-stitching pass; GMRT is one
    HTTP call with bbox params and is already proven in fetch_bathy.py.
    Documented in bundle.json["sources"]["bathy"] so the next refresh
    knows what it's pulling.
  * Contours: numpy + custom marching-squares (no scikit-image dep —
    keeps requirements.txt thin). Contour intervals are 1 m / 5 m /
    25 m piecewise, which matches diver-relevant depth bands.
  * Skip-if-fresh: 30-day TTL per spot, idempotent on re-runs.

Run on demand or as a daily refresh step:

  python pipeline/build_spot_bundles.py            # idempotent, skips fresh
  python pipeline/build_spot_bundles.py --force    # rebuild even if fresh
  python pipeline/build_spot_bundles.py --spot lajolla   # one spot only
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

REGION = active_region()
SPOTS_DIR = REGION.data_output_dir(REPO_ROOT) / "spots"
INDEX_PATH = SPOTS_DIR / "index.json"

# Mirror of frontend's REGION_SAVED_SPOTS.ca for the three pilot spots.
# The Python copy is intentional (handover §4 Task B item 1) — keeps
# the builder dependency-free from JS. Add CA spots here as the pilot
# expands per the kelp/spot roadmap.
SPOT_CENTRES = {
    "lajolla":  {"name": "La Jolla",  "lng": -117.275, "lat": 32.854},
    "catalina": {"name": "Catalina",  "lng": -118.450, "lat": 33.389},
    "monterey": {"name": "Monterey",  "lng": -121.920, "lat": 36.620},
}

# Mirror of frontend's SPOT_BUNDLE_RADIUS_KM. Same single-source-of-truth
# rationale as SPOT_CENTRES above.
SPOT_RADIUS_KM = {
    "lajolla":  4,
    "catalina": 8,
    "monterey": 6,
}

# Pixel size for the spot's bathy PNG. 480 × 480 keeps each bundle tiny
# (~120-180 KB encoded) while delivering above-display-resolution detail
# at the 4-8 km radius (one pixel ≈ 17-33 m on the ground).
BATHY_SIZE = 480

# Depth encoding (mirrors fetch_bathy.py). 0 = NaN/land; 1..255 linear
# over depth_range_m. Per-spot depth ranges are different — La Jolla
# Canyon hits ~400 m within 4 km of shore; Catalina's deepest local
# bathy is ~600-800 m off the windward side; Monterey Canyon is ~1500+ m
# offshore but the inner 6 km is ~200 m. We pick per-spot ranges from
# observation rather than a global "0 to 6000 m" range, so the inshore
# detail (where divers actually go) gets the full 8-bit ramp.
SPOT_DEPTH_RANGES_M = {
    "lajolla":  (0,  500),    # La Jolla Canyon rim drops fast
    "catalina": (0,  800),    # Windward side dropoff
    "monterey": (0, 1200),    # Inner Monterey Canyon
}
DEFAULT_DEPTH_RANGE_M = (0, 500)

# Contour intervals (m). Sparser at depth — 1 m up to 10 m is dive-
# planning detail; 5 m to 50 m is nav-zone; 25 m past 50 m is bottom
# context only. Tuned so each spot ends up with ~20-50 contour lines
# total — readable but not noisy.
CONTOUR_INTERVALS = [
    (0,    10,  1),
    (10,   50,  5),
    (50,   500, 25),
    (500,  5000, 100),
]

# Skip-if-fresh threshold. 30 days matches the handover's recommendation
# — CUDEM is decadal, coastline is yearly, kelp/mpa rarely change.
FRESHNESS_DAYS = 30

GMRT_URL = "https://www.gmrt.org/services/GridServer"


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def km_to_degrees(km: float, lat: float) -> tuple[float, float]:
    """Convert km to (d_lng, d_lat) at a given latitude.

    Latitudes degrees are roughly constant (111 km / °); longitude
    degrees shrink with cos(lat). Used to derive the spot bbox from
    a (centre, radius_km) pair.
    """
    d_lat = km / 111.0
    d_lng = km / (111.0 * max(math.cos(math.radians(lat)), 0.05))
    return d_lng, d_lat


def spot_bbox(centre_lng: float, centre_lat: float, radius_km: float) -> dict:
    """Square-ish bbox in lng/lat space around the centre.

    "Square-ish" because we use cos(lat) to make lng/lat distances
    match metrically — the resulting bbox is wider in degrees than
    tall (at CA latitudes, lng degrees are ~80% as long as lat
    degrees), but rendered with `projectInBbox` it covers the same
    on-the-ground distance both ways.
    """
    d_lng, d_lat = km_to_degrees(radius_km, centre_lat)
    return {
        "lng_min": centre_lng - d_lng,
        "lng_max": centre_lng + d_lng,
        "lat_min": centre_lat - d_lat,
        "lat_max": centre_lat + d_lat,
    }


# ---------------------------------------------------------------------------
# GMRT bathymetry fetch + parse
# ---------------------------------------------------------------------------

def fetch_gmrt_dem(bbox: dict) -> bytes:
    """Pull GMRT high-resolution NetCDF for the spot bbox.

    Same endpoint fetch_bathy.py uses, with resolution=high instead
    of med. High mode gives ~100 m near the coast — small enough that
    a 480 × 480 PNG over a 4-8 km bbox is the limiting resolution,
    not the source.
    """
    params = {
        "north":      bbox["lat_max"],
        "south":      bbox["lat_min"],
        "west":       bbox["lng_min"],
        "east":       bbox["lng_max"],
        "format":     "netcdf",
        "resolution": "high",
    }
    print(f"  GET GMRT high-res {bbox['lng_min']:.4f},{bbox['lat_min']:.4f} → "
          f"{bbox['lng_max']:.4f},{bbox['lat_max']:.4f}")
    r = requests.get(GMRT_URL, params=params, timeout=120)
    r.raise_for_status()
    return r.content


def parse_gmrt_netcdf(nc_bytes: bytes):
    """Extract (depth_m, lats_north_to_south, lons_west_to_east) from
    a GMRT NetCDF response.

    GMRT serves the GMT-style flat grid format (x_range / y_range /
    z_range / dimension / z[nx*ny]) rather than CF-compliant per-
    coordinate arrays. We handle both formats — same logic as
    fetch_bathy.py, just inlined here to keep this builder
    self-contained.
    """
    import os
    import tempfile
    import xarray as xr

    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tf:
        tf.write(nc_bytes)
        tmp_path = tf.name
    try:
        ds = xr.open_dataset(tmp_path, engine="netcdf4")
        ds.load()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if "x_range" in ds.variables and "dimension" in ds.variables:
        x_range = np.asarray(ds["x_range"].values).flatten()
        y_range = np.asarray(ds["y_range"].values).flatten()
        dim     = np.asarray(ds["dimension"].values).flatten()
        nx, ny  = int(dim[0]), int(dim[1])
        z_flat  = np.asarray(ds["z"].values).flatten().astype(np.float32)
        if z_flat.size != nx * ny:
            raise ValueError(f"GMT grid: z size {z_flat.size} != nx*ny {nx * ny}")
        z = z_flat.reshape(ny, nx)
        lons = np.linspace(x_range[0], x_range[1], nx)
        lats = np.linspace(y_range[1], y_range[0], ny)  # N→S
    else:
        # CF-compliant fallback (in case GMRT changes format)
        Z_NAMES = ("z", "altitude", "elevation", "depth", "Band1")
        LON_NAMES = ("lon", "longitude", "x")
        LAT_NAMES = ("lat", "latitude", "y")
        def _pick(names, where):
            for n in names:
                if n in where: return n
            return None
        zname = _pick(Z_NAMES, ds.variables)
        lonname = _pick(LON_NAMES, ds.coords) or _pick(LON_NAMES, ds.variables)
        latname = _pick(LAT_NAMES, ds.coords) or _pick(LAT_NAMES, ds.variables)
        if not (zname and lonname and latname):
            raise KeyError(f"Unrecognized NetCDF schema: vars={list(ds.variables)}")
        z = np.asarray(ds[zname].values, dtype=np.float32)
        lons = np.asarray(ds[lonname].values)
        lats = np.asarray(ds[latname].values)

    # Normalize: lats S→N, lons W→E
    if lats[0] > lats[-1]:
        lats = lats[::-1]
        z = z[::-1, :]
    if lons[0] > lons[-1]:
        lons = lons[::-1]
        z = z[:, ::-1]

    depth = np.where(z < 0, -z, np.nan).astype(np.float32)
    return depth, lats, lons


def resample_to_bbox(depth, src_lats, src_lons, bbox, out_w, out_h):
    """Bilinear resample depth raster onto a regular (out_h × out_w)
    grid spanning the spot bbox, row 0 = north edge."""
    out_lng = np.linspace(bbox["lng_min"], bbox["lng_max"], out_w)
    out_lat = np.linspace(bbox["lat_max"], bbox["lat_min"], out_h)

    # Fractional source indices
    fx = np.interp(out_lng, src_lons, np.arange(len(src_lons)))
    fy = np.interp(out_lat, src_lats, np.arange(len(src_lats)))

    x0 = np.clip(np.floor(fx).astype(int), 0, len(src_lons) - 1)
    x1 = np.clip(x0 + 1, 0, len(src_lons) - 1)
    y0 = np.clip(np.floor(fy).astype(int), 0, len(src_lats) - 1)
    y1 = np.clip(y0 + 1, 0, len(src_lats) - 1)

    wx = fx - x0
    wy = fy - y0

    src_h, _src_w = depth.shape
    # depth is laid out (lat_idx, lng_idx) but src_lats may be S→N
    # while depth's row 0 is original orientation. We normalized above
    # so src_lats is now S→N AND depth rows match — but out_lat is
    # N→S, so the y indices need flipping.
    y0f = (src_h - 1) - y0
    y1f = (src_h - 1) - y1

    out = np.full((out_h, out_w), np.nan, dtype=np.float32)
    for j in range(out_h):
        v00 = depth[y0f[j], x0]
        v01 = depth[y0f[j], x1]
        v10 = depth[y1f[j], x0]
        v11 = depth[y1f[j], x1]
        wy_j = wy[j]
        top = v00 * (1 - wx) + v01 * wx
        bot = v10 * (1 - wx) + v11 * wx
        out[j, :] = top * (1 - wy_j) + bot * wy_j
    return out


def encode_depth_png(depth_grid, depth_range_m, out_path: Path):
    """Encode the depth grid as 8-bit grayscale PNG.

    0 = NaN/land (consumer treats as transparent or land mask).
    1..255 = linear over depth_range_m. Values outside the range
    clamp to the endpoints so the visualisation stays useful even
    where the actual depth exceeds the per-spot scale.
    """
    d_min, d_max = depth_range_m
    span = max(d_max - d_min, 1.0)
    normalized = np.clip((depth_grid - d_min) / span, 0.0, 1.0)
    # 1..255 ramp for valid depths, 0 reserved for NaN.
    encoded = (1 + np.round(normalized * 254.0)).astype(np.uint8)
    nan_mask = ~np.isfinite(depth_grid)
    encoded[nan_mask] = 0
    img = Image.fromarray(encoded, mode="L")
    img.save(out_path, format="PNG", optimize=True)


# ---------------------------------------------------------------------------
# Depth contours via custom marching-squares
# ---------------------------------------------------------------------------

def _trace_contours_at_level(depth, level, bbox):
    """Naive marching-squares contour extraction at a single depth level.

    Returns a list of (lng, lat) polylines. We don't try to close rings
    or chain segments — the visual rendering pass treats each segment
    independently, which is fine for thin contour lines.

    Why not skimage.measure.find_contours? Adding scikit-image as a
    runtime dep balloons the wheel size by ~40 MB and pulls in matplotlib
    + scipy.sparse for one function. The custom traversal here is ~30
    lines and produces equivalent output for our use case (visual
    contour overlay, not metrology).
    """
    h, w = depth.shape
    # Pre-compute mask of valid (non-NaN) cells
    valid = np.isfinite(depth)
    # Replace NaN with a value above the level so it doesn't generate
    # spurious crossings into land
    d = np.where(valid, depth, level + 1e9)

    # Cell-by-cell marching squares. For each 2×2 cell, check which
    # corners are below the level; emit one or two segments per case.
    segments = []
    dx_lng = (bbox["lng_max"] - bbox["lng_min"]) / (w - 1)
    dy_lat = (bbox["lat_max"] - bbox["lat_min"]) / (h - 1)

    for j in range(h - 1):
        for i in range(w - 1):
            # Corner values: top-left, top-right, bottom-right, bottom-left
            v00 = d[j,     i    ]
            v01 = d[j,     i + 1]
            v11 = d[j + 1, i + 1]
            v10 = d[j + 1, i    ]
            # 4-bit case index
            case = 0
            if v00 < level: case |= 1
            if v01 < level: case |= 2
            if v11 < level: case |= 4
            if v10 < level: case |= 8
            if case == 0 or case == 15:
                continue
            # Edge interpolation helper — `t` is the fractional position
            # along the edge where the contour crosses
            def edge(a, b):
                if a == b: return 0.5
                return (level - a) / (b - a)

            # Corner pixel coords in lng/lat space
            x_l = bbox["lng_min"] + i * dx_lng
            x_r = x_l + dx_lng
            y_t = bbox["lat_max"] - j * dy_lat
            y_b = y_t - dy_lat

            # Crossing points on each edge (only computed if needed)
            pts = {}
            if (case in (1, 14, 3, 12, 5, 10, 7, 8, 9, 6, 11, 4, 13, 2)):
                # top edge (between v00 and v01)
                t = edge(v00, v01)
                pts["T"] = (x_l + t * dx_lng, y_t)
                # right edge (v01 - v11)
                t = edge(v01, v11)
                pts["R"] = (x_r, y_t - t * dy_lat)
                # bottom edge (v10 - v11)
                t = edge(v10, v11)
                pts["B"] = (x_l + t * dx_lng, y_b)
                # left edge (v00 - v10)
                t = edge(v00, v10)
                pts["L"] = (x_l, y_t - t * dy_lat)

            # Case → segment list. 16 cases, but the lookup table is
            # symmetric across "below the level" vs "above" so we only
            # write 8 (cases 0/15 skipped above).
            CASES = {
                1:  [("L", "T")],
                2:  [("T", "R")],
                3:  [("L", "R")],
                4:  [("R", "B")],
                5:  [("L", "T"), ("R", "B")],   # saddle
                6:  [("T", "B")],
                7:  [("L", "B")],
                8:  [("B", "L")],
                9:  [("B", "T")],
                10: [("T", "L"), ("R", "B")],   # saddle
                11: [("R", "B")],
                12: [("L", "R")],
                13: [("T", "R")],
                14: [("L", "T")],
            }
            for (a, b) in CASES.get(case, []):
                if a in pts and b in pts:
                    segments.append((pts[a], pts[b]))
    return segments


def generate_contours(depth_grid, bbox) -> dict:
    """Walk the depth grid + produce a GeoJSON FeatureCollection of
    MultiLineString contours, one feature per depth level.

    Each feature carries `properties.depth_m` so the frontend can
    style by depth bin (e.g. thicker stroke at every 10 m).
    """
    features = []
    for (lo, hi, step) in CONTOUR_INTERVALS:
        levels = list(range(lo, hi, step))
        # Include the upper-band boundary so e.g. 10 m and 50 m don't
        # both fall off the list when the bands stitch together
        if hi not in levels: levels.append(hi)
        for level in levels:
            if level == 0:
                continue  # shoreline is the coastline overlay's job
            segments = _trace_contours_at_level(depth_grid, level, bbox)
            if not segments:
                continue
            coords = [
                [
                    [round(p1[0], 5), round(p1[1], 5)],
                    [round(p2[0], 5), round(p2[1], 5)],
                ]
                for (p1, p2) in segments
            ]
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": coords,
                },
                "properties": {
                    "depth_m": level,
                },
            })
    return {
        "type": "FeatureCollection",
        "features": features,
    }


# ---------------------------------------------------------------------------
# GeoJSON clipping (coastline / kelp / mpa)
# ---------------------------------------------------------------------------

def clip_geojson_to_bbox(src_path: Path, bbox: dict) -> dict | None:
    """Read a GeoJSON file + return a new FeatureCollection containing
    only features whose geometry intersects the bbox.

    We use a feature-level bbox prefilter rather than full geometry
    intersection because (a) the source polygons are small enough
    that bbox overlap = visual overlap for our purposes, and (b) it
    avoids pulling shapely's full geom intersection cost on the
    daily refresh. Properties pass through unchanged.

    Returns None if the source file doesn't exist (e.g. kelp before
    Phase 1A shipped) — caller treats that as "skip this layer."
    """
    if not src_path.exists():
        return None
    with src_path.open("r", encoding="utf-8") as f:
        fc = json.load(f)
    features = fc.get("features", []) or []
    keep = []
    for feat in features:
        geom = feat.get("geometry")
        if not geom:
            continue
        b = _geometry_bounds(geom)
        if b is None:
            continue
        if (b["lng_max"] < bbox["lng_min"] or
            b["lng_min"] > bbox["lng_max"] or
            b["lat_max"] < bbox["lat_min"] or
            b["lat_min"] > bbox["lat_max"]):
            continue
        keep.append(feat)
    return {"type": "FeatureCollection", "features": keep}


def _geometry_bounds(geom):
    """Compute the lng/lat bounding box of a GeoJSON geometry.
    Supports Polygon, MultiPolygon, LineString, MultiLineString.
    """
    coords = geom.get("coordinates")
    if not coords: return None
    pts = []
    t = geom.get("type")
    if t == "Polygon":
        for ring in coords:
            pts.extend(ring)
    elif t == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                pts.extend(ring)
    elif t == "LineString":
        pts.extend(coords)
    elif t == "MultiLineString":
        for line in coords:
            pts.extend(line)
    elif t == "Point":
        pts.append(coords)
    elif t == "MultiPoint":
        pts.extend(coords)
    else:
        return None
    if not pts: return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {
        "lng_min": min(xs), "lng_max": max(xs),
        "lat_min": min(ys), "lat_max": max(ys),
    }


# ---------------------------------------------------------------------------
# Freshness check + main loop
# ---------------------------------------------------------------------------

def bundle_is_fresh(bundle_dir: Path) -> bool:
    """Return True if bundle.json exists and was generated within
    FRESHNESS_DAYS — i.e. we can skip rebuilding."""
    manifest_path = bundle_dir / "bundle.json"
    if not manifest_path.exists():
        return False
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        gen_at = manifest.get("generated_at")
        if not gen_at:
            return False
        dt = datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - dt
        return age < timedelta(days=FRESHNESS_DAYS)
    except (json.JSONDecodeError, ValueError, KeyError):
        return False


def build_spot(spot_id: str, *, force: bool) -> bool:
    """Build a single spot bundle. Returns True on success, False on
    skip / soft-fail. Hard errors (HTTP 5xx, parse errors) raise.
    """
    if spot_id not in SPOT_CENTRES:
        print(f"  unknown spot id {spot_id!r} — skipping")
        return False
    centre = SPOT_CENTRES[spot_id]
    radius_km = SPOT_RADIUS_KM.get(spot_id, 5)
    bundle_dir = SPOTS_DIR / spot_id

    if not force and bundle_is_fresh(bundle_dir):
        print(f"  [{spot_id}] fresh bundle exists ≤ {FRESHNESS_DAYS} d — skipping")
        return True

    print(f"\n[{spot_id}] building bundle (radius={radius_km} km)")
    bbox = spot_bbox(centre["lng"], centre["lat"], radius_km)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # 1. Bathy DEM
    print(f"  fetching GMRT high-res DEM…")
    try:
        nc_bytes = fetch_gmrt_dem(bbox)
        depth, src_lats, src_lons = parse_gmrt_netcdf(nc_bytes)
        depth_grid = resample_to_bbox(depth, src_lats, src_lons, bbox,
                                       BATHY_SIZE, BATHY_SIZE)
        depth_range_m = SPOT_DEPTH_RANGES_M.get(spot_id, DEFAULT_DEPTH_RANGE_M)
        bathy_path = bundle_dir / "bathy.png"
        encode_depth_png(depth_grid, depth_range_m, bathy_path)
        print(f"    wrote {bathy_path.name} ({bathy_path.stat().st_size / 1024:.1f} KB), "
              f"depth range {depth_range_m[0]}–{depth_range_m[1]} m")
    except Exception as e:
        print(f"  ERROR fetching/encoding bathy: {e}")
        return False

    # 2. Contours from the (un-resampled, native-resolution) depth.
    # Operating on the native grid gives finer-grained contour lines;
    # we project crossings back into lng/lat via the source grid's
    # spacing in _trace_contours_at_level. Actually — for simplicity
    # we use the resampled grid since that's already on the bbox's
    # regular lat/lng axes. The contours come out smooth enough for
    # diver viz at 480 × 480 native cell-size (~17-33 m / cell).
    print(f"  generating depth contours…")
    contours = generate_contours(depth_grid, bbox)
    contours_path = bundle_dir / "contours.geojson"
    contours_path.write_text(json.dumps(contours, separators=(",", ":")))
    print(f"    wrote {contours_path.name} "
          f"({len(contours['features'])} levels, "
          f"{contours_path.stat().st_size / 1024:.1f} KB)")

    # 3. Coastline clip
    print(f"  clipping reference overlays…")
    layers_meta = {
        "bathy": {
            "url": "bathy.png",
            "width": BATHY_SIZE,
            "height": BATHY_SIZE,
            "depth_range_m": list(depth_range_m),
            "encoding": "linear_8bit_0nan",
        },
        "contours": {
            "url": "contours.geojson",
            "intervals_m": [iv[2] for iv in CONTOUR_INTERVALS],
            "levels": len(contours["features"]),
        },
    }
    region_data_dir = REGION.data_output_dir(REPO_ROOT)
    for layer_key, src_name in [
        ("coastline", "coastline.geojson"),
        ("kelp",      "kelp-beds.geojson"),
        ("mpa",       "mpa-boundaries.geojson"),
    ]:
        src_path = region_data_dir / src_name
        out_path = bundle_dir / f"{layer_key}.geojson"
        clipped = clip_geojson_to_bbox(src_path, bbox)
        if clipped is None:
            print(f"    [{layer_key}] source {src_path.name} not present — skipping")
            continue
        out_path.write_text(json.dumps(clipped, separators=(",", ":")))
        feature_count = len(clipped["features"])
        layers_meta[layer_key] = {
            "url": f"{layer_key}.geojson",
            "features": feature_count,
        }
        print(f"    [{layer_key}] {feature_count} features → {out_path.name}")

    # 4. Bundle manifest
    manifest = {
        "id": spot_id,
        "name": centre["name"],
        "centre": {"lng": centre["lng"], "lat": centre["lat"]},
        "bbox": bbox,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "layers": layers_meta,
        "sources": {
            "bathy":     "GMRT high-resolution GridServer (NetCDF, ~100 m near coast)",
            "coastline": "OSM natural=coastline via Overpass (clipped)",
            "kelp":      "CDFW Administrative Kelp Beds ds3135 (clipped)",
            "mpa":       "CDFW MPA ds582 (clipped)",
        },
    }
    manifest_path = bundle_dir / "bundle.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"  wrote {manifest_path.name}")
    return True


def write_index(built_spots: list[str]) -> None:
    """Write public/data/spots/index.json — the frontend reads this to
    know which saved-spot pins have a bundle available."""
    SPOTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "spots": sorted(built_spots),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    INDEX_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {INDEX_PATH.relative_to(REPO_ROOT)} "
          f"({len(built_spots)} spots: {', '.join(sorted(built_spots))})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--spot", help="Build only this spot id (default: all)")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild even if bundle.json is < 30 d old")
    args = parser.parse_args()

    region_name = REGION.name if hasattr(REGION, "name") else "ca"
    if region_name != "ca":
        # MVP is CA-only. Write an empty index so the frontend's
        # bundled-spots set is { } and no "View detailed map" buttons
        # render outside CA — graceful no-op.
        print(f"[build_spot_bundles] region={region_name} — MVP is CA-only, "
              f"writing empty index.json")
        write_index([])
        return 0

    target_spots = [args.spot] if args.spot else list(SPOT_CENTRES.keys())
    built = []
    failed = []
    for spot_id in target_spots:
        try:
            if build_spot(spot_id, force=args.force):
                built.append(spot_id)
            else:
                failed.append(spot_id)
        except Exception as e:
            print(f"  [{spot_id}] FAILED: {e!r}")
            failed.append(spot_id)

    # Always re-emit the index — even on partial failure, the spots that
    # already have a bundle in their directory should be discoverable.
    # Discover by scanning the spots dir rather than only listing this
    # run's successes, so e.g. a failed catalina rebuild doesn't drop
    # catalina from the index if its prior bundle is still on disk.
    on_disk = [
        d.name for d in sorted(SPOTS_DIR.iterdir())
        if d.is_dir() and (d / "bundle.json").exists()
    ] if SPOTS_DIR.exists() else []
    write_index(on_disk)

    print(f"\nDone. built={len(built)}, failed={len(failed)}, "
          f"on_disk={len(on_disk)}")
    # Return 0 unless EVERY targeted spot failed — partial success
    # should still let the workflow's commit step pick up the wins.
    return 1 if (failed and not built) else 0


if __name__ == "__main__":
    sys.exit(main())
