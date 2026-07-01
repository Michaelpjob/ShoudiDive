"""Higher-cadence CURRENT-canopy signal from Sentinel-2 (10 m, ~5-day) to blend
onto the slow quarterly Landsat 3-yr-peak baseline (kelp_source.py / the reservoir).

WHY (deep-research adjudicated, 2026-07-01, run wb11wiaov): the incumbent kelp
layer is the SBC LTER "Kelp from Landsat" QUARTERLY composite pulled as a 3-year
PEAK snapshot — it lags real bed condition, which shifts on ~2-4 week timescales
in summer. Sentinel-2 is the pragmatic higher-cadence source: FREE, ~5-day
revisit (S2A+S2B), 10 m (finer than Landsat 30 m for nearshore beds), and
reachable with NO auth via the Element84 earth-search STAC + the public AWS
`sentinel-cogs` COG bucket (CSP-clean batch job — beats Google Earth Engine's
service-account burden). Planet was rejected (its product is an ANNUAL September
composite + licensing/redistribution constraints).

WHAT this does (Stages 1+2): for each SCB kelp bed cell (the same 0.05deg grid
kelp_source.py bins the Landsat pixels into), find the newest recent low-cloud,
LOW-TIDE Sentinel-2 scene covering it, read red/green/NIR over the cell's 0.05deg
footprint, detect floating canopy (NDVI over a WATER-only footprint — land is
excluded GEOGRAPHICALLY via the model landmask, because floating kelp is
spectrally vegetation-like and a spectral water index would erase it too), and
record the CURRENT S2 canopy AREA (km^2) per cell. Output = a small committed
sidecar `data/sentinel2_kelp_scb.json` that kelp_source.py blends into CELL_HEALTH
(Stage 3), BOUNDED so a noisy fast source can never dominate the stable baseline
(the #229 report-assimilation lesson).

Tide (Stage 2): detected canopy swings ~40% per 2 m tide (Schroeder 2019), so
scenes acquired above ACCEPT_TIDE_M (NOAA CO-OPS prediction at La Jolla, the
overpass time) are REJECTED — submergence is the dominant false-"change" source.
Cloud: a cell whose Sentinel-2 SCL says it is >CLOUD_MAX_FRAC cloudy is SKIPPED
(no read) so a cloud is never misread as a bare bed (a false CELL_HEALTH drop).

Run:  python fetch_kelp_sentinel2.py            # writes data/sentinel2_kelp_scb.json
      python fetch_kelp_sentinel2.py --dry      # print per-cell, don't write

CAVEATS: the model landmask is 0.01deg (~1.1 km) so the innermost nearshore fringe
can be clipped; NDVI (not the full Mora-Soto KD index) is the v1 detector (clean
land/water separation validated on San Clemente; KD/sunglint refinement is a
follow-up); absolute S2 area is NOT directly comparable to 30 m Landsat area — the
blend (Stage 3) cross-calibrates regionally rather than trusting the raw area.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import numpy as np
import requests

import config
import kelp_source
from landmask import LandMask

STAC_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"
# Look-back is intentionally long: the SoCal marine layer ("May Gray / June Gloom")
# can cloud out a bed's optical view for a full month, so a "current canopy" read is
# the most-recent CLEAR look within this window (still far fresher than the quarterly
# 3-yr-peak Landsat, and within the ~2-4 wk real-change timescale most of the year).
DAYS_BACK = 60            # look back this far for a usable scene per cell
# Permissive SCENE-level cloud ceiling (the strict per-CELL SCL guard below does the
# real cloud rejection over each bed, so a partly-cloudy scene that is CLEAR over a
# given bed is still usable).
MAX_CLOUD = 40           # scene-level cloud ceiling (%) in the STAC query
CLOUD_MAX_FRAC = 0.30    # per-CELL SCL cloud fraction above which we skip the cell
MAX_SCENES_PER_CELL = 3  # composite the MAX canopy over up to this many recent clear scenes (noise-beat)
NDVI_KELP = 0.10         # floating-canopy NDVI threshold (validated on San Clemente)
NIR_MIN = 0.02           # floating canopy has vegetation-like NIR; excludes dark water
BIN_DEG = 0.05           # MUST match kelp_source.load_cells bin_deg
ACCEPT_TIDE_M = 1.20     # NOAA MLLW metres; reject scenes acquired above this (submergence)
TIDE_STATION = "9410230"  # La Jolla, CA — representative SCB overpass tide
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "sentinel2_kelp_scb.json")

# GDAL/vsicurl: read public COGs by HTTP range, no auth, no directory listing.
os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.TIF")


def stac_search(bbox, days_back, max_cloud):
    end = dt.datetime.utcnow()
    start = end - dt.timedelta(days=days_back)
    body = {
        "collections": [COLLECTION],
        "bbox": bbox,
        "datetime": f"{start.strftime('%Y-%m-%dT00:00:00Z')}/{end.strftime('%Y-%m-%dT23:59:59Z')}",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": 100,
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }
    r = requests.post(STAC_URL, json=body, timeout=90)
    r.raise_for_status()
    return r.json().get("features", [])


_tide_cache = {}


def tide_at(dt_utc):
    """NOAA CO-OPS predicted tide (m, MLLW) at the scene time; None if unavailable."""
    day = dt_utc.strftime("%Y%m%d")
    key = (TIDE_STATION, day)
    preds = _tide_cache.get(key)
    if preds is None:
        url = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
               f"?product=predictions&datum=MLLW&station={TIDE_STATION}"
               "&time_zone=gmt&units=metric&interval=6&format=json"
               f"&begin_date={day}&end_date={day}")
        try:
            j = requests.get(url, timeout=30).json()
            preds = [(dt.datetime.strptime(p["t"], "%Y-%m-%d %H:%M"), float(p["v"]))
                     for p in j.get("predictions", [])]
        except Exception:
            preds = []
        _tide_cache[key] = preds
    if not preds:
        return None
    naive = dt_utc.replace(tzinfo=None)
    return min(preds, key=lambda p: abs((p[0] - naive).total_seconds()))[1]


def _landmask_vec(lm, LO, LA):
    """Vectorised is_land over lng/lat arrays via the LandMask boolean raster."""
    x = ((LO - lm.bbox["lng_min"]) / lm.step).astype(int)
    y = ((lm.bbox["lat_max"] - LA) / lm.step).astype(int)
    inb = (x >= 0) & (y >= 0) & (x < lm.w) & (y < lm.h)
    out = np.zeros(LO.shape, dtype=bool)
    xi = np.clip(x, 0, lm.w - 1)
    yi = np.clip(y, 0, lm.h - 1)
    out[inb] = lm.mask[yi[inb], xi[inb]]
    return out


def _read_window(href, lng0, lat0, lng1, lat1):
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    with rasterio.open(href) as ds:
        b = transform_bounds("EPSG:4326", ds.crs, lng0, lat0, lng1, lat1)
        win = from_bounds(*b, ds.transform)
        return ds.read(1, window=win, boundless=True, fill_value=0)


def read_cell(feat, kj, ki, lm):
    """Current S2 canopy for one 0.05deg bed cell. Returns dict or None (cloudy/no data)."""
    lat_c, lng_c = kj * BIN_DEG, ki * BIN_DEG
    lat0, lat1 = lat_c - BIN_DEG / 2, lat_c + BIN_DEG / 2
    lng0, lng1 = lng_c - BIN_DEG / 2, lng_c + BIN_DEG / 2
    a = feat["assets"]
    red = _read_window(a["red"]["href"], lng0, lat0, lng1, lat1).astype("float32")
    nir = _read_window(a["nir"]["href"], lng0, lat0, lng1, lat1).astype("float32")
    scl = _read_window(a["scl"]["href"], lng0, lat0, lng1, lat1)  # 20 m; resampled to window

    valid = (red > 0) & (nir > 0)                       # 0 = S2 nodata
    if valid.sum() < 25:
        return None
    # SCL cloud codes: 3=shadow, 8/9=cloud med/high, 10=thin cirrus. Resample SCL to
    # red's shape by nearest if shapes differ (20 m vs 10 m windows).
    if scl.shape != red.shape:
        yj = (np.linspace(0, scl.shape[0] - 1, red.shape[0])).astype(int)
        xi = (np.linspace(0, scl.shape[1] - 1, red.shape[1])).astype(int)
        scl = scl[np.ix_(yj, xi)]
    cloudy = np.isin(scl, (3, 8, 9, 10)) & valid
    cloud_frac = cloudy.sum() / max(valid.sum(), 1)
    if cloud_frac > CLOUD_MAX_FRAC:
        return None                                     # too cloudy over THIS bed -> skip (keep Landsat)

    r = (red - 1000.0) / 10000.0                        # baseline BOA offset -> reflectance
    n = (nir - 1000.0) / 10000.0
    ndvi = (n - r) / (n + r + 1e-6)

    h, w = ndvi.shape
    lats = np.linspace(lat1, lat0, h)
    lngs = np.linspace(lng0, lng1, w)
    LO, LA = np.meshgrid(lngs, lats)
    land = _landmask_vec(lm, LO, LA)

    water = valid & ~land & ~cloudy
    kelp = water & (ndvi > NDVI_KELP) & (n > NIR_MIN)
    n_kelp = int(kelp.sum())
    return {
        "area_km2": round(n_kelp * 1e-4, 5),            # 10 m px = 100 m^2 = 1e-4 km^2
        "n_kelp": n_kelp,
        "n_water": int(water.sum()),
        "ndvi_p90": round(float(np.percentile(ndvi[water], 90)), 3) if water.any() else 0.0,
        "cloud_frac": round(float(cloud_frac), 3),
    }


# Per-cluster STAC cache: a single big-bbox query capped at 100 scenes returns the
# newest 100 across ALL tiles, which starves the less-frequently-imaged tiles (e.g.
# San Clemente). Query a SMALL bbox per coarse cluster so every region gets its own
# newest scenes; adjacent cells reuse the cached cluster result.
_STAC_CLUSTER_DEG = 0.3
_stac_cache = {}
_tide_rej = [0]


def scenes_for(lng, lat):
    key = (round(lng / _STAC_CLUSTER_DEG), round(lat / _STAC_CLUSTER_DEG))
    if key in _stac_cache:
        return _stac_cache[key]
    bbox = [lng - 0.2, lat - 0.2, lng + 0.2, lat + 0.2]
    feats = stac_search(bbox, DAYS_BACK, MAX_CLOUD)
    acc = []
    for f in feats:
        t = dt.datetime.fromisoformat(f["properties"]["datetime"].replace("Z", "+00:00"))
        tide = tide_at(t)
        if tide is not None and tide > ACCEPT_TIDE_M:   # Stage 2: reject submerged-canopy scenes
            _tide_rej[0] += 1
            continue
        f["_dt"], f["_tide"] = t, tide
        acc.append(f)
    acc.sort(key=lambda f: f["_dt"], reverse=True)
    _stac_cache[key] = acc
    return acc


def main(dry=False):
    print("== Sentinel-2 current-canopy fetch (blend onto Landsat baseline) ==")
    cells = kelp_source.load_cells()          # SCB bed cells (also fills CELL_HEALTH)
    lm = LandMask()

    out, n_cloud, n_nodata = {}, 0, 0
    for (name, lng, lat, _r, _isl, _area) in cells:
        kj, ki = round(lat / BIN_DEG), round(lng / BIN_DEG)
        cov = [f for f in scenes_for(lng, lat)
               if f["bbox"][0] <= lng <= f["bbox"][2] and f["bbox"][1] <= lat <= f["bbox"][3]]
        cov.sort(key=lambda f: f["_dt"], reverse=True)
        # COMPOSITE the MAX canopy over up to MAX_SCENES_PER_CELL recent CLEAR scenes
        # (mirrors why Landsat composites: a single scene carries tide/detection noise;
        # the recent max is the robust "best current look", comparable to Landsat's peak).
        rec, n_scenes = None, 0
        for f in cov:
            try:
                cand = read_cell(f, kj, ki, lm)
            except Exception:
                continue
            if cand is None:
                continue                     # cloudy/no-data over this bed on this scene
            n_scenes += 1
            cand["date"] = f["_dt"].strftime("%Y-%m-%d")
            cand["tide_m"] = f["_tide"]
            cand["cloud_scene"] = f["properties"].get("eo:cloud_cover")
            if rec is None or cand["area_km2"] > rec["area_km2"]:
                rec = cand
            if n_scenes >= MAX_SCENES_PER_CELL:
                break
        if rec is not None:
            rec["n_scenes"] = n_scenes
        if rec is None:
            (n_cloud if cov else n_nodata)  # noqa - just accounting below
            if cov:
                n_cloud += 1
            else:
                n_nodata += 1
            continue
        out[name] = rec

    covered = len(out)
    print(f"  STAC clusters queried: {len(_stac_cache)} | tide-rejected scenes: {_tide_rej[0]} "
          f"(> {ACCEPT_TIDE_M} m MLLW)")
    print(f"  cells: {len(cells)} total | {covered} with S2 read | "
          f"{n_cloud} all-cloudy | {n_nodata} no scene")
    if out:
        areas = sorted((v["area_km2"] for v in out.values()), reverse=True)
        dates = sorted({v["date"] for v in out.values()})
        print(f"  S2 area/cell: max {areas[0]:.3f} med {areas[len(areas)//2]:.3f} km^2 | "
              f"scene dates {dates[0]}..{dates[-1]}")

    payload = {
        "meta": {
            "source": "Sentinel-2 L2A via Element84 earth-search STAC + AWS sentinel-cogs",
            "detector": f"NDVI>{NDVI_KELP} over water (landmask-excluded), NIR>{NIR_MIN}",
            "tide_filter": {"station": TIDE_STATION, "accept_max_m_mllw": ACCEPT_TIDE_M},
            "cloud_cell_max_frac": CLOUD_MAX_FRAC,
            "bin_deg": BIN_DEG,
            "days_back": DAYS_BACK,
            "n_cells_covered": covered,
        },
        "cells": out,
    }
    if dry:
        print(json.dumps(payload["meta"], indent=2))
        return payload
    if covered == 0:
        print("  ERROR: 0 cells covered — refusing to write an empty sidecar", file=sys.stderr)
        raise SystemExit(2)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as fh:
        json.dump(payload, fh)
    print(f"  wrote {OUT_PATH} ({covered} cells)")
    return payload


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
