"""Fetch CA Marine Protected Area boundaries from CDFW ArcGIS, clip to bbox,
slim properties, and write to public/data/mpa-boundaries.geojson.

Only the fields the UI needs are kept. Geometry coordinates get rounded to
4 decimal places (~11 m at this latitude) to keep the file small.

Run on demand or quarterly:
  python pipeline/fetch_mpa.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

# Bbox via pipeline/regions/ (PR-X-1). CA / PNW / tropical switch on
# SHOULDIDIVE_REGION; default `ca` preserves today's behavior.
try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

BBOX = active_region().bbox

# CDFW Open Data Portal — California Marine Protected Areas (ds582).
# Direct GeoJSON download in WGS84.
URL = (
    "https://data-cdfw.opendata.arcgis.com/datasets/"
    "117a99c8745a48c6a48bac70005b1b11_0.geojson"
)

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = active_region().data_output_dir(ROOT) / "mpa-boundaries.geojson"


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def feature_bounds(geom: dict) -> tuple[float, float, float, float]:
    """Compute (min_lng, min_lat, max_lng, max_lat) for a Polygon/MultiPolygon."""
    coords = geom["coordinates"]
    pts: list[tuple[float, float]] = []
    if geom["type"] == "Polygon":
        for ring in coords:
            pts.extend(ring)
    elif geom["type"] == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                pts.extend(ring)
    if not pts:
        return (0, 0, 0, 0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def round_coords(geom: dict, decimals: int = 3) -> dict:
    """Round all coordinates in-place to keep JSON small."""

    def r(p):
        return [round(p[0], decimals), round(p[1], decimals)]

    def round_ring(ring):
        return [r(p) for p in ring]

    if geom["type"] == "Polygon":
        geom["coordinates"] = [round_ring(ring) for ring in geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        geom["coordinates"] = [
            [round_ring(ring) for ring in poly] for poly in geom["coordinates"]
        ]
    return geom


def main() -> None:
    print(f"GET {URL}")
    r = requests.get(URL, timeout=120)
    r.raise_for_status()
    fc = r.json()

    keep = []
    for feat in fc.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue
        min_lng, min_lat, max_lng, max_lat = feature_bounds(geom)
        if max_lng < BBOX["lng_min"] or min_lng > BBOX["lng_max"]:
            continue
        if max_lat < BBOX["lat_min"] or min_lat > BBOX["lat_max"]:
            continue

        props = feat.get("properties", {}) or {}
        name = props.get("FULLNAME") or props.get("NAME") or props.get("SiteName")
        type_ = (
            props.get("Type")
            or props.get("TYPE")
            or props.get("DESIG_TYPE")
            or props.get("DESIGNATION")
            or "MPA"
        )
        short = props.get("SHORTNAME") or name
        ccr = (
            props.get("CCR_TITLE_14")
            or props.get("CCR_TITLE")
            or props.get("CCR_REF")
        )
        area_km2 = None
        if props.get("Shape_Area"):
            try:
                area_km2 = round(float(props["Shape_Area"]) / 1e6, 2)
            except (TypeError, ValueError):
                area_km2 = None

        if not name:
            continue

        slim_props = {
            "id": slugify(name),
            "name": name,
            "type": type_,
            "shortName": short,
            "areaKm2": area_km2,
            "ccrCitation": ccr,
        }

        keep.append(
            {
                "type": "Feature",
                "geometry": round_coords(geom),
                "properties": slim_props,
            }
        )

    out = {"type": "FeatureCollection", "features": keep}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"wrote {len(keep)} MPA features to {OUT_PATH.name} ({size_kb:.1f} KB)")
    # Print type distribution for visibility.
    types: dict[str, int] = {}
    for f in keep:
        t = f["properties"]["type"]
        types[t] = types.get(t, 0) + 1
    for t, n in sorted(types.items(), key=lambda kv: -kv[1]):
        print(f"  {t}: {n}")


if __name__ == "__main__":
    main()
