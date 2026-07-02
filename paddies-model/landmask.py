"""Rasterized land mask for stranding.

Loads ShoudiDive's published land.geojson (read-only) and burns it into
a boolean grid so we can ask, cheaply, "is this lng/lat on land?" A
drifting paddy that steps onto land BEACHES (strands) and leaves the
offshore population — it does not keep following the current through the
shoreline.
"""
from __future__ import annotations

import json
import os

import numpy as np
from PIL import Image, ImageDraw

import config

_HERE = os.path.dirname(os.path.abspath(__file__))


def _candidates():
    """Where to find ShoudiDive's published land.geojson, most-specific first.

    The hard-coded Windows path used to be the only non-`out/` candidate, which
    silently fails anywhere else (e.g. a Linux CI runner) — disabling the mask so
    the green field + cones never clip to water and nothing beaches ("kelp on
    land"). PADDIES_LOCAL_DATA matches the deployed paddies-model/sd_source.py;
    the sibling path covers the usual side-by-side checkout layout.
    """
    out = []
    local = os.environ.get("PADDIES_LOCAL_DATA", "").strip()
    if local:
        out.append(os.path.join(local, "data", "land.geojson"))
    out += [
        os.path.join(config.OUT_DIR, "land.geojson"),
        os.path.join(_HERE, "..", "ShoudiDive", "public", "data", "land.geojson"),
        os.path.join(_HERE, "..", "sd-kelp-paddies", "public", "data", "land.geojson"),
        r"C:\Users\Michael Job\Claude\ShoudiDive\public\data\land.geojson",
    ]
    return out


def _load_land():
    # land.geojson now includes real OSM Baja coastline down past Ensenada
    # (pipeline/fetch_coastline_baja.py merges it), so no supplement is needed.
    for p in _candidates():
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    try:
        import requests
        r = requests.get("https://shouldidive.com/data/land.geojson", timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _polys(geom):
    t = (geom or {}).get("type")
    if t == "Polygon":
        return [geom["coordinates"]]
    if t == "MultiPolygon":
        return geom["coordinates"]
    return []


class LandMask:
    def __init__(self, bbox=None, step=None):
        self.bbox = bbox or config.FIELD_BBOX
        self.step = step or config.LAND_MASK_STEP_DEG
        b = self.bbox
        self.w = int(round((b["lng_max"] - b["lng_min"]) / self.step)) + 1
        self.h = int(round((b["lat_max"] - b["lat_min"]) / self.step)) + 1
        self.mask = self._raster()
        self.coverage = float(self.mask.mean()) if self.mask.size else 0.0

    def _xy(self, lng, lat):
        x = (lng - self.bbox["lng_min"]) / self.step
        y = (self.bbox["lat_max"] - lat) / self.step   # row 0 = north
        return x, y

    def _raster(self):
        land = _load_land()
        img = Image.new("1", (self.w, self.h), 0)
        if not land:
            print("  landmask: land.geojson not found — stranding disabled")
            return np.zeros((self.h, self.w), dtype=bool)
        draw = ImageDraw.Draw(img)
        n = 0
        for feat in land.get("features", []):
            for poly in _polys(feat.get("geometry")):
                if not poly:
                    continue
                ext = [self._xy(*pt[:2]) for pt in poly[0]]
                if len(ext) >= 3:
                    draw.polygon(ext, fill=1)
                    n += 1
                for hole in poly[1:]:
                    hh = [self._xy(*pt[:2]) for pt in hole]
                    if len(hh) >= 3:
                        draw.polygon(hh, fill=0)
        arr = np.array(img, dtype=bool)
        print(f"  landmask: {n} land polygons -> {arr.mean()*100:.1f}% of bbox is land")
        return arr

    def is_land(self, lng, lat):
        x, y = self._xy(lng, lat)
        i, j = int(round(x)), int(round(y))
        if i < 0 or j < 0 or i >= self.w or j >= self.h:
            return False   # outside the bbox = open ocean
        return bool(self.mask[j, i])
