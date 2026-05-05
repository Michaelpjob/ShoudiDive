"""One-time fetch of real CA-bbox bathymetry from GMRT (Global Multi-
Resolution Topography). Idempotent — skips if the output PNG already
exists. Run as part of refresh-data.yml so CI re-fetches on a clean
checkout but commits the result to the repo.

Replaces the crude shelf_depth_from_dist approximation in
fetch_visibility.py for `depth_m` lookups. The shelf approximation
worked at ~order-of-magnitude scale for the mainland but was wrong by
~3 orders of magnitude over the Channel/Coastal Islands (treating SCI
as 4000 m water when its shelf is 5–50 m). Real bathymetry tightens
bottom_stir + tide_index on every island shelf.

Source: GMRT GridServer (public, no auth). Mid resolution gives ~1 km
native cells over the CA bbox (~720×580), which we save at 560×440 to
keep the encoded PNG under ~200 KB.

Encoding:
  * 8-bit grayscale, 0 = NaN (land), 1..255 linear over 0..6000 m depth
  * Resolution 6000 m / 254 ≈ 23.6 m per pixel level — finer than
    the model's bottom-stir sensitivity to depth.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "public" / "data" / "bathy.png"

# Match the rest of the pipeline's CA bbox.
BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)

# Output PNG dimensions. 4× the model's standard 140×110 to preserve
# shelf-edge detail; consumers bilinear-resample to their own grid.
OUT_W = 560
OUT_H = 440

# Linear depth encoding range (m). 0 m = shoreline, 6000 m = deep abyss
# (CA's deepest ~3000 m, headroom for the encoding).
DEPTH_MIN_M = 0.0
DEPTH_MAX_M = 6000.0

GMRT_URL = "https://www.gmrt.org/services/GridServer"


def fetch_gmrt_netcdf(bbox, resolution="med"):
    """Hit the GMRT GridServer + return the NetCDF bytes."""
    params = {
        "north":      bbox["lat_max"],
        "south":      bbox["lat_min"],
        "west":       bbox["lng_min"],
        "east":       bbox["lng_max"],
        "format":     "netcdf",
        "resolution": resolution,
    }
    print(f"  GET {GMRT_URL} {params}")
    r = requests.get(GMRT_URL, params=params, timeout=180)
    r.raise_for_status()
    return r.content


def parse_netcdf_to_depth(nc_bytes):
    """Extract the elevation grid + return (depth_m, lats, lngs).

    GMRT NetCDFs use variable name 'z' for elevation (positive up), with
    coordinate vars 'x' (lng) and 'y' (lat). We flip to depth (positive
    down) and clip land to NaN so the consumer can decide whether to
    treat land as 0 or skip.
    """
    # Lazy imports — netCDF4 + xarray are heavy to load
    import os
    import tempfile
    import xarray as xr

    # The netCDF4 backend (HDF5-backed) requires a real file path; it
    # can't read from BytesIO. Write to a temp file, parse, then unlink.
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tf:
        tf.write(nc_bytes)
        tmp_path = tf.name
    try:
        ds = xr.open_dataset(tmp_path, engine="netcdf4")
        ds.load()  # pull data into memory before we delete the file
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # GMRT serves the legacy GMT-style grid file format inside its
    # NetCDF wrapper, not a CF-compliant per-coordinate layout. The
    # actual fields are:
    #   x_range  — [min_lon, max_lon]
    #   y_range  — [min_lat, max_lat]
    #   z_range  — [min_z, max_z]   (informational)
    #   spacing  — [dx, dy]         (degrees)
    #   dimension — [nx, ny]        (int)
    #   z        — flat (nx * ny,) array, row-major, NORTH→SOUTH scan
    print(f"  NetCDF variables: {list(ds.variables.keys())}")

    if "x_range" in ds.variables and "dimension" in ds.variables:
        # GMT-style grid format
        x_range = np.asarray(ds["x_range"].values).flatten()
        y_range = np.asarray(ds["y_range"].values).flatten()
        dim     = np.asarray(ds["dimension"].values).flatten()
        nx, ny  = int(dim[0]), int(dim[1])
        z_flat  = np.asarray(ds["z"].values).flatten().astype(np.float32)
        if z_flat.size != nx * ny:
            raise ValueError(
                f"GMT grid: z size {z_flat.size} != nx*ny {nx * ny}"
            )
        # GMT grids are row-major, top-left origin (north-most row first).
        z = z_flat.reshape(ny, nx)
        # Build the explicit lon/lat coordinate vectors.
        lons = np.linspace(x_range[0], x_range[1], nx)
        lats = np.linspace(y_range[1], y_range[0], ny)  # north→south
        print(f"  GMT-grid: {nx}×{ny}, "
              f"lon {x_range[0]:.2f}..{x_range[1]:.2f}, "
              f"lat {y_range[0]:.2f}..{y_range[1]:.2f}")
    else:
        # CF-compliant schema (in case GMRT changes serving format)
        Z_CANDIDATES   = ("z", "altitude", "elevation", "depth", "Band1")
        LON_CANDIDATES = ("lon", "longitude", "x")
        LAT_CANDIDATES = ("lat", "latitude", "y")

        def _pick(names, *, where):
            for n in names:
                if n in where:
                    return n
            return None

        z_name = _pick(Z_CANDIDATES, where=ds.variables)
        lon_name = _pick(LON_CANDIDATES, where=ds.coords) or _pick(LON_CANDIDATES, where=ds.variables)
        lat_name = _pick(LAT_CANDIDATES, where=ds.coords) or _pick(LAT_CANDIDATES, where=ds.variables)
        if z_name is None or lon_name is None or lat_name is None:
            raise KeyError(
                f"GMRT NetCDF: unrecognized schema. "
                f"Variables: {list(ds.variables.keys())} "
                f"Coords: {list(ds.coords.keys())}"
            )
        z = np.asarray(ds[z_name].values, dtype=np.float32)
        lons = np.asarray(ds[lon_name].values)
        lats = np.asarray(ds[lat_name].values)
        print(f"  CF-style: elevation='{z_name}', lon='{lon_name}', lat='{lat_name}'")
    # Some grids come N→S, normalize so lats[0] is the southmost row.
    if lats[0] > lats[-1]:
        lats = lats[::-1]
        z = z[::-1, :]
    # Same for lons (W→E, lons[0] should be most negative).
    if lons[0] > lons[-1]:
        lons = lons[::-1]
        z = z[:, ::-1]

    depth = np.where(z < 0, -z, np.nan).astype(np.float32)
    return depth, lats, lons


def resample_to_grid(depth, src_lats, src_lons, out_w, out_h, bbox):
    """Bilinear resample depth raster onto a regular (out_h, out_w) grid
    over the CA bbox. NaN cells (land) are preserved as NaN."""
    # Target grid coordinates (regular within bbox)
    out_lng = np.linspace(bbox["lng_min"], bbox["lng_max"], out_w)
    # Target grid laid out N→S to match the rest of the pipeline's PNG
    # convention (row 0 = north edge).
    out_lat = np.linspace(bbox["lat_max"], bbox["lat_min"], out_h)

    src_h, src_w = depth.shape

    # Fractional source indices for each target cell.
    fx = (out_lng - src_lons[0]) / (src_lons[-1] - src_lons[0]) * (src_w - 1)
    fy = (out_lat - src_lats[0]) / (src_lats[-1] - src_lats[0]) * (src_h - 1)

    fx2 = fx[None, :]
    fy2 = fy[:, None]

    x0 = np.clip(np.floor(fx2).astype(int), 0, src_w - 1)
    x1 = np.clip(x0 + 1, 0, src_w - 1)
    y0 = np.clip(np.floor(fy2).astype(int), 0, src_h - 1)
    y1 = np.clip(y0 + 1, 0, src_h - 1)
    wx = fx2 - x0
    wy = fy2 - y0

    a = depth[y0, x0]
    b = depth[y0, x1]
    c = depth[y1, x0]
    d = depth[y1, x1]

    # Treat NaN as no-data: if any of the 4 corners is NaN we propagate
    # NaN. Bilinear over partial NaNs would smear coast pixels into land.
    valid = np.isfinite(a) & np.isfinite(b) & np.isfinite(c) & np.isfinite(d)
    out = np.full((out_h, out_w), np.nan, dtype=np.float32)
    out[valid] = (
        a[valid] * (1 - wx[valid]) * (1 - wy[valid])
        + b[valid] * wx[valid] * (1 - wy[valid])
        + c[valid] * (1 - wx[valid]) * wy[valid]
        + d[valid] * wx[valid] * wy[valid]
    )
    return out


def encode_linear_png(arr, lo, hi, out_path):
    """8-bit grayscale: 0=NaN, 1..255 linear from lo..hi.
    Mirrors fetch_visibility.encode_linear_png so the encoding round-trips
    through fetch_visibility.decode_linear_png cleanly."""
    valid = np.isfinite(arr)
    scaled = (arr - lo) / (hi - lo)
    px = np.zeros(arr.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def main():
    if OUT_PATH.exists():
        print(f"  {OUT_PATH.relative_to(REPO_ROOT)} already exists, skipping")
        return 0

    print(f"Fetching GMRT bathymetry → {OUT_PATH.relative_to(REPO_ROOT)}")
    try:
        nc_bytes = fetch_gmrt_netcdf(BBOX, resolution="med")
    except Exception as exc:  # noqa: BLE001
        print(f"  GMRT fetch failed: {exc.__class__.__name__}: {exc}")
        # Non-fatal — fetch_visibility falls back to shelf_depth_from_dist
        # when bathy.png is missing.
        return 1

    print(f"  got {len(nc_bytes) // 1024} KB of NetCDF")
    depth, lats, lons = parse_netcdf_to_depth(nc_bytes)
    print(f"  source grid: {depth.shape[1]}×{depth.shape[0]}, "
          f"depth range {np.nanmin(depth):.0f}–{np.nanmax(depth):.0f} m, "
          f"NaN (land) cells {np.isnan(depth).mean() * 100:.0f}%")

    out = resample_to_grid(depth, lats, lons, OUT_W, OUT_H, BBOX)
    print(f"  resampled to {OUT_W}×{OUT_H}, "
          f"output depth range {np.nanmin(out):.0f}–{np.nanmax(out):.0f} m")

    encode_linear_png(out, DEPTH_MIN_M, DEPTH_MAX_M, OUT_PATH)
    size_kb = OUT_PATH.stat().st_size // 1024
    print(f"  wrote {OUT_PATH.relative_to(REPO_ROOT)} ({size_kb} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
