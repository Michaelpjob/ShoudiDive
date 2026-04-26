"""Fetch high-resolution land polygons from Natural Earth (10 m), clip to
the app bbox, and write to public/data/land.geojson.

The hand-drawn coastline polyline in src/lib/mapData.js was a stylized
approximation. This pulls real public-domain shoreline data (Natural Earth
10 m, ~1:10,000,000) and clips it to our area. Channel Islands and the
Coronados land mass appear as separate polygons.

Run on demand (the data barely changes; quarterly is plenty):
  python pipeline/fetch_coastline.py
"""
from __future__ import annotations

import json
from pathlib import Path

import requests
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)

# martynafford/natural-earth-geojson mirrors NE shapefiles as GeoJSON.
LAND_URL = (
    "https://raw.githubusercontent.com/martynafford/natural-earth-geojson"
    "/master/10m/physical/ne_10m_land.json"
)
ISLANDS_URL = (
    "https://raw.githubusercontent.com/martynafford/natural-earth-geojson"
    "/master/10m/physical/ne_10m_minor_islands.json"
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "data" / "land.geojson"


def round_geom(geom: dict, decimals: int = 4) -> dict:
    def r(p):
        return [round(p[0], decimals), round(p[1], decimals)]

    if geom["type"] == "Polygon":
        geom["coordinates"] = [[r(p) for p in ring] for ring in geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        geom["coordinates"] = [
            [[r(p) for p in ring] for ring in poly] for poly in geom["coordinates"]
        ]
    return geom


def fetch_clipped(url: str, clip_box) -> list[dict]:
    """Pull a NE GeoJSON FeatureCollection and clip each feature's geometry
    to the bbox. Returns a list of (already-clipped) features whose geometry
    intersects the bbox."""
    print(f"GET {url}")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    fc = r.json()
    out = []
    for feat in fc.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            s = shape(geom)
        except Exception:
            continue
        if not s.intersects(clip_box):
            continue
        clipped = s.intersection(clip_box)
        if clipped.is_empty:
            continue
        out.append(
            {
                "type": "Feature",
                "geometry": round_geom(mapping(clipped)),
                "properties": feat.get("properties") or {},
            }
        )
    return out


def main() -> None:
    clip_box = box(
        BBOX["lng_min"],
        BBOX["lat_min"],
        BBOX["lng_max"],
        BBOX["lat_max"],
    )

    feats = []
    feats.extend(fetch_clipped(LAND_URL, clip_box))
    feats.extend(fetch_clipped(ISLANDS_URL, clip_box))

    out = {"type": "FeatureCollection", "features": feats}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    size_kb = OUT.stat().st_size / 1024
    print(f"wrote {len(feats)} land/island features ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
