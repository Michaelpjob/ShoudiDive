"""Extend land.geojson south into northern Baja (past Ensenada) with real,
detailed OSM coastline — same method as fetch_coastline.py, but for the Baja
Pacific strip the CA-region bbox cuts off (published land stops at ~31.8N, so
the paddy map showed blank water down there AND drifting paddies "flipped over"
unmasked Baja land).

Fetches natural=coastline for the Baja bbox from Overpass, polygonizes to land
polygons (seeded so the ocean face is rejected), clips, simplifies, and MERGES
the Baja land features into the existing public/data/land.geojson. Idempotent:
re-running replaces the previously-merged Baja features (tagged baja-ext).

  python pipeline/fetch_coastline_baja.py

Durability: land.geojson is regenerated on demand by fetch_coastline.py (yearly).
Run this after it, or fold the Baja bbox into the CA region to make it permanent.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
from shapely.geometry import LineString, Point, box, mapping
from shapely.ops import polygonize, unary_union

# Baja Pacific strip: Tijuana border down past Ensenada to ~Santo Tomás/Colnett.
BBOX = dict(lat_min=30.2, lat_max=32.4, lng_min=-117.5, lng_max=-115.8)
CLIP_LAT_MAX = 31.95   # abut (don't heavily overlap) the existing US coverage (~31.81N)
PAD = 0.15
SIMPLIFY = 1e-4        # ~10 m, matches fetch_coastline.py CA tolerance
MIN_AREA = 1e-6

LAND_SEEDS = [  # (name, lng, lat) — inland points so polygonize tags land, not sea
    ("baja-mainland-n", -116.95, 32.05),
    ("baja-ensenada",   -116.45, 31.85),
    ("baja-maneadero",  -116.30, 31.55),
    ("baja-santo-tomas",-116.20, 31.30),
    ("baja-colnett",    -116.05, 30.90),
    ("baja-south",      -115.95, 30.45),
]
OCEAN_SEEDS = [  # offshore Pacific, west of the coast
    (-117.35, 31.60), (-117.25, 31.10), (-117.10, 30.55), (-117.40, 30.90),
]
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
ROOT = Path(__file__).resolve().parents[1]
LAND = ROOT / "public" / "data" / "land.geojson"


def overpass():
    b = dict(lat_min=BBOX["lat_min"] - PAD, lat_max=BBOX["lat_max"] + PAD,
             lng_min=BBOX["lng_min"] - PAD, lng_max=BBOX["lng_max"] + PAD)
    q = (f'[out:json][timeout:120];way["natural"="coastline"]'
         f'({b["lat_min"]},{b["lng_min"]},{b["lat_max"]},{b["lng_max"]});out geom;')
    last = None
    for url in OVERPASS:
        try:
            print(f"POST {url}")
            r = requests.post(url, data={"data": q}, timeout=180,
                              headers={"User-Agent": "shouldidive/0.1 +github.com/Michaelpjob/ShoudiDive"})
            r.raise_for_status()
            return r.json(), b
        except Exception as e:
            print(f"  {url} failed — {e}")
            last = e
            time.sleep(2)
    raise RuntimeError(f"all Overpass endpoints failed ({last})")


def main():
    data, b = overpass()
    lines = []
    for el in data.get("elements", []):
        if el.get("type") == "way" and len(el.get("geometry") or []) >= 2:
            lines.append(LineString([(p["lon"], p["lat"]) for p in el["geometry"]]))
    if not lines:
        print("no coastline ways — refusing", file=sys.stderr); sys.exit(1)
    print(f"  {len(lines)} coastline ways")
    ring = box(b["lng_min"], b["lat_min"], b["lng_max"], b["lat_max"]).boundary
    polys = list(polygonize(unary_union(lines + [ring])))
    print(f"  {len(polys)} regions")

    land_pts = [Point(s[1], s[2]) for s in LAND_SEEDS]
    ocean_pts = [Point(s[0], s[1]) for s in OCEAN_SEEDS]
    clip = box(BBOX["lng_min"], BBOX["lat_min"], BBOX["lng_max"], CLIP_LAT_MAX)
    feats = []
    for poly in sorted(polys, key=lambda p: -p.area):
        if any(poly.contains(p) for p in ocean_pts):
            continue
        if not any(poly.contains(p) for p in land_pts):
            continue  # only keep seeded Baja land (avoid slivers)
        c = poly.intersection(clip)
        if c.is_empty or c.area < MIN_AREA:
            continue
        c = c.simplify(SIMPLIFY, preserve_topology=True)
        if c.is_empty:
            continue
        feats.append({"type": "Feature", "properties": {"name": "baja-ext"},
                      "geometry": mapping(c)})
    if not feats:
        print("no Baja land polygons found — refusing", file=sys.stderr); sys.exit(1)
    print(f"  kept {len(feats)} Baja land features")

    land = json.loads(LAND.read_text())
    land["features"] = [f for f in land["features"]
                        if f.get("properties", {}).get("name") != "baja-ext"] + feats
    LAND.write_text(json.dumps(land, separators=(",", ":")))
    print(f"merged -> {LAND} ({len(land['features'])} features total)")


if __name__ == "__main__":
    main()
