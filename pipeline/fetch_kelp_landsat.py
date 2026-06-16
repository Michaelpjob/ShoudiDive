#!/usr/bin/env python
"""Refresh the compact CA+Baja Landsat kelp extract.

Pulls the latest revision of the SBC LTER "Kelp from Landsat" dataset
(EDI knb-lter-sbc.74 — a ~2.6 GB NetCDF, station-list schema with
area(time, station) in m^2 per 30 m pixel) and extracts the CA+Baja
ever-kelp pixels (extent) plus the recent-year canopy into a small
committed NetCDF (pipeline/data/landsat_kelp_ca.nc, ~1 MB).

build_spot_bundles.py reads that compact file to draw per-spot kelp for
the spots CDFW doesn't cover (Coronados / Baja, NorCal bull kelp, the
islands CDFW timed out on). This script is run quarterly by
.github/workflows/refresh-kelp.yml so the spot bundles always rebuild
against fresh canopy without a 2.6 GB download in the daily pipeline.

Usage:
  python pipeline/fetch_kelp_landsat.py                 # download latest from EDI
  python pipeline/fetch_kelp_landsat.py --source X.nc   # use a local NetCDF
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "pipeline" / "data" / "landsat_kelp_ca.nc"
PASTA = "https://pasta.lternet.edu/package"
SCOPE, PKG_ID = "knb-lter-sbc", "74"
# CA + Baja Pacific kelp box. Reaches down to 27N / -113.5 so the Baja
# Pacific kelp forests (Isla Cedros ~28.2N, Islas San Benito ~28.3N,
# Bahia Tortugas ~27.7N) are captured alongside the full CA coast (to
# ~40.5N). The California-Current kelp dataset carries no Sea-of-Cortez
# stations, so the eastern margin harmlessly spans the peninsula. Margin
# on all sides.
BBOX = (27.0, 40.5, -125.0, -113.5)   # lat_min, lat_max, lon_min, lon_max
RECENT_QUARTERS = 4                    # last year defines the "canopy" class


def latest_netcdf_url():
    """Resolve the newest revision's NetCDF data-entity download URL."""
    revs = requests.get(f"{PASTA}/eml/{SCOPE}/{PKG_ID}", timeout=60).text.split()
    rev = max(int(r) for r in revs)
    ents = requests.get(f"{PASTA}/data/eml/{SCOPE}/{PKG_ID}/{rev}", timeout=60).text.split()
    named = [(e, requests.get(
        f"{PASTA}/name/eml/{SCOPE}/{PKG_ID}/{rev}/{e}", timeout=60).text.strip())
        for e in ents]
    # The package ships a single NetCDF data entity, named by a
    # descriptive title (e.g. "Satellite kelp biomass since 1984") rather
    # than a filename. Match it by keyword, falling back to the sole entity.
    kw = ("biomass", "kelp", "canopy", "satellite", "landsat", ".nc")
    pick = next((p for p in named if any(k in p[1].lower() for k in kw)), None)
    if pick is None and named:
        pick = named[0]
    if pick is None:
        raise RuntimeError(f"no data entity in {SCOPE}.{PKG_ID} r{rev}")
    ent, name = pick
    print(f"  EDI {SCOPE}.{PKG_ID} r{rev}: {name}")
    return f"{PASTA}/data/eml/{SCOPE}/{PKG_ID}/{rev}/{ent}", rev


def download(url, dest):
    print(f"  downloading -> {dest}")
    with requests.get(url, stream=True, timeout=900) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    print(f"  {os.path.getsize(dest) / 1e6:.0f} MB")


def extract(nc_path, rev=None):
    import numpy as np
    import xarray as xr
    ds = xr.open_dataset(nc_path)
    lon = ds.longitude.values
    lat = ds.latitude.values
    print("  loading area grid (decompresses the source)…")
    area = ds.area.values                       # (time, station) m^2/pixel
    ever = np.nanmax(area, axis=0)              # peak over all years
    recent = np.nanmax(area[-RECENT_QUARTERS:, :], axis=0)  # last year peak
    la_min, la_max, lo_min, lo_max = BBOX
    keep = (
        (lat >= la_min) & (lat <= la_max)
        & (lon >= lo_min) & (lon <= lo_max)
        & (ever > 0)
    )
    n = int(keep.sum())
    recent_q = str(ds.time.values[-1])[:10]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = xr.Dataset(
        {
            "longitude":   ("station", lon[keep].astype("float32")),
            "latitude":    ("station", lat[keep].astype("float32")),
            "recent_area": ("station", np.nan_to_num(recent[keep]).astype("float32")),
            "ever_area":   ("station", ever[keep].astype("float32")),
        },
        attrs={
            "source": f"SBC LTER {SCOPE}.{PKG_ID}"
                      f"{f' r{rev}' if rev else ''} Kelp from Landsat",
            "recent_quarter": recent_q,
            "recent_window_quarters": RECENT_QUARTERS,
            "pixel_m": 30,
            "bbox": str(BBOX),
        },
    )
    enc = {v: {"zlib": True, "complevel": 6} for v in out.data_vars}
    out.to_netcdf(OUT_PATH, encoding=enc)
    print(f"  wrote {OUT_PATH.relative_to(REPO_ROOT)} "
          f"({os.path.getsize(OUT_PATH) // 1024} KB, {n} stations, "
          f"canopy quarter {recent_q})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", help="local NetCDF path (skip the EDI download)")
    args = ap.parse_args()
    if args.source:
        extract(args.source)
    else:
        url, rev = latest_netcdf_url()
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "kelp.nc")
            download(url, dest)
            extract(dest, rev)
    return 0


if __name__ == "__main__":
    sys.exit(main())
