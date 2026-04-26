"""Fetch high-resolution land polygons from OpenStreetMap and clip to the
app bbox. Replaces Natural Earth 10 m which is too coarse to show harbour-
level detail (La Jolla cove, San Diego Bay, Mission Bay, etc.) when users
zoom in.

Pipeline:
  1. Hit Overpass API for every `natural=coastline` way in [bbox + buffer].
  2. Build LineStrings from those ways and add the buffered bbox rectangle
     as four extra lines so endpoints close cleanly at the edges.
  3. shapely.polygonize → all enclosed regions in the bbox.
  4. Keep regions that contain a known-land seed point (mainland + islands);
     drop the ocean polygon and any sliver artefacts.
  5. Round coords to 5 decimals (~1 m precision) and write as GeoJSON.

Run on demand (data barely changes; a yearly refresh is plenty):
  python pipeline/fetch_coastline.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Iterable

import requests
from shapely.geometry import (
    LineString, MultiPolygon, Point, Polygon, box, mapping, shape,
)
from shapely.ops import polygonize, unary_union

BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)

# Pad the Overpass query a little so coastline ways straddling the corners
# come back complete. We re-clip to the exact bbox at write time.
PAD_DEG = 0.20

# Drop polygons smaller than this. Below that they're sub-pixel rocks that
# consume vertices without rendering at any zoom we support. Seeded features
# (named islands like the Coronados) are always kept regardless of size.
MIN_FEATURE_AREA_DEG2 = 1e-6

# Douglas-Peucker tolerance, in degrees. ~10 m at 34°N — still well below
# the pixel size at any zoom level our SVG supports, but slashes vertex
# counts on the mainland enough to keep the file under ~400 KB.
SIMPLIFY_TOLERANCE_DEG = 1e-4

# Known land seed points used to label polygonize() outputs as land vs sea.
# Each (name, lng, lat) lies inside a real CA / Coronados land mass.
LAND_SEEDS = [
    ("ca-mainland",         -119.40, 35.50),  # Big Sur / inland CA
    ("santa-rosa-island",   -120.10, 33.97),
    ("santa-cruz-island",   -119.75, 33.99),
    ("anacapa-island",      -119.40, 34.00),
    ("san-miguel-island",   -120.37, 34.04),
    ("san-nicolas-island",  -119.50, 33.25),
    ("santa-barbara-island",-119.03, 33.48),
    ("santa-catalina",      -118.45, 33.39),
    ("san-clemente-island", -118.50, 32.90),
    # Las Coronadas — four small islands in MX waters off Tijuana. Centroids
    # are tight; if any of the smaller ones drift off centre OSM-side they
    # may show up as `unnamed-land` instead, but they'll still render.
    ("coronado-norte",      -117.298, 32.419),
    ("coronado-medio",      -117.273, 32.405),
    ("pilar-de-la-virgen",  -117.265, 32.395),
    ("coronado-sur",        -117.247, 32.378),
    ("baja-mainland",       -116.95, 32.10),
]

# Used to exclude the polygonize() face that represents the open Pacific.
# Each seed sits comfortably offshore; any polygon containing one of them
# is sea regardless of its size.
OCEAN_SEEDS = [
    (-123.50, 36.00),  # central offshore
    (-123.20, 33.50),  # SoCal offshore
    (-122.50, 32.00),  # Mexican offshore
]

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "data" / "land.geojson"


def overpass_query(buffered_bbox: dict) -> dict:
    """Pull every natural=coastline way (with full geometry) in the bbox.
    Tries each Overpass mirror in turn so a single dead host doesn't break
    the build."""
    q = (
        f'[out:json][timeout:120];'
        f'way["natural"="coastline"]'
        f'({buffered_bbox["lat_min"]},{buffered_bbox["lng_min"]},'
        f'{buffered_bbox["lat_max"]},{buffered_bbox["lng_max"]});'
        f'out geom;'
    )
    last_err = None
    for url in OVERPASS_ENDPOINTS:
        try:
            print(f"POST {url}")
            r = requests.post(url, data={"data": q}, timeout=180,
                              headers={"User-Agent": "shouldidive/0.1 +github.com/Michaelpjob/ShoudiDive"})
            r.raise_for_status()
            return r.json()
        except Exception as e:  # network / 429 / 504
            print(f"  {url} failed — {e!s}")
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"All Overpass endpoints failed (last: {last_err!s})")


def lines_from_overpass(data: dict) -> list[LineString]:
    """Convert each way's `geometry` (list of {lat, lon}) into a LineString.
    Skip degenerate ways with <2 points."""
    out: list[LineString] = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        coords = [(p["lon"], p["lat"]) for p in geom]
        out.append(LineString(coords))
    return out


def round_geom(geom: dict, decimals: int = 5) -> dict:
    def r(p):
        return [round(p[0], decimals), round(p[1], decimals)]
    if geom["type"] == "Polygon":
        geom["coordinates"] = [[r(p) for p in ring] for ring in geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        geom["coordinates"] = [
            [[r(p) for p in ring] for ring in poly] for poly in geom["coordinates"]
        ]
    return geom


def keep_land_polygons(polys: Iterable[Polygon], land_seeds, ocean_seeds):
    """Classify polygonize() outputs as land vs sea.

    Strategy:
      * The Pacific shows up as polygonize faces that contain offshore seed
        points; reject those outright.
      * Everything else is land — mainland, islands, and any tiny rocks
        OSM mapped that we don't have a named seed for.
      * Polygons matched to a known land seed get a friendly name; the rest
        are recorded as 'unnamed-land' so we don't lose tiny features.

    Naming-only — we keep all non-ocean polygons regardless of whether a
    seed matched, so the mainland (which is too big for any single seed to
    'guarantee') is preserved as long as it's not the ocean polygon.
    """
    polys = list(polys)
    if not polys:
        return []

    land_pts = [Point(s[1], s[2]) for s in land_seeds]
    land_names = [s[0] for s in land_seeds]
    ocean_pts = [Point(s[0], s[1]) for s in ocean_seeds]

    out: list[tuple[Polygon, list[str]]] = []
    for poly in polys:
        if any(poly.contains(p) for p in ocean_pts):
            continue
        names = [land_names[j] for j, pt in enumerate(land_pts) if poly.contains(pt)]
        if not names:
            names = ["unnamed-land"]
        out.append((poly, names))
    return out


def main() -> None:
    buffered = dict(
        lat_min=BBOX["lat_min"] - PAD_DEG,
        lat_max=BBOX["lat_max"] + PAD_DEG,
        lng_min=BBOX["lng_min"] - PAD_DEG,
        lng_max=BBOX["lng_max"] + PAD_DEG,
    )
    print(f"Buffered bbox: {buffered}")

    data = overpass_query(buffered)
    coast_lines = lines_from_overpass(data)
    if not coast_lines:
        print("No coastline ways returned by Overpass — refusing to overwrite", file=sys.stderr)
        sys.exit(1)
    print(f"  fetched {len(coast_lines)} coastline ways "
          f"({sum(len(l.coords) for l in coast_lines)} vertices total)")

    # Add the buffered bbox boundary as a closing rectangle so polygonize
    # can form closed regions where coastline ways exit the area.
    box_ring = box(
        buffered["lng_min"], buffered["lat_min"],
        buffered["lng_max"], buffered["lat_max"],
    ).boundary
    all_lines = coast_lines + [box_ring]

    # unary_union turns any line crossings into shared nodes so polygonize
    # can route through them. Necessary when an OSM way clips the bbox.
    print("  unioning + polygonizing...")
    merged = unary_union(all_lines)
    polygons = list(polygonize(merged))
    print(f"  polygonize yielded {len(polygons)} regions")

    keep = keep_land_polygons(polygons, LAND_SEEDS, OCEAN_SEEDS)
    print(f"  identified {len(keep)} land regions:")
    for poly, names in sorted(keep, key=lambda kv: -kv[0].area)[:25]:
        print(f"    {poly.area:.5f} deg2 -- {', '.join(names)}")
    if len(keep) > 25:
        print(f"    (+{len(keep) - 25} more, mostly tiny rocks/islets)")

    # Clip back to the exact app bbox so we don't store padding off-screen.
    clip_box = box(
        BBOX["lng_min"], BBOX["lat_min"],
        BBOX["lng_max"], BBOX["lat_max"],
    )
    feats = []
    dropped_tiny = 0
    for poly, names in sorted(keep, key=lambda kv: -kv[0].area):
        clipped = poly.intersection(clip_box)
        if clipped.is_empty:
            continue
        is_seeded = names != ["unnamed-land"]
        if not is_seeded and clipped.area < MIN_FEATURE_AREA_DEG2:
            dropped_tiny += 1
            continue
        # Douglas-Peucker simplify: ~3 m precision is plenty at any zoom we
        # support and slashes the vertex count for big features like the
        # mainland (which OSM tags down to single-metre detail).
        simplified = clipped.simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
        if simplified.is_empty:
            continue
        feats.append({
            "type": "Feature",
            "geometry": round_geom(mapping(simplified), decimals=5),
            "properties": {"name": names[0]},
        })
    if dropped_tiny:
        print(f"  dropped {dropped_tiny} sub-{MIN_FEATURE_AREA_DEG2:g} deg2 features")

    out = {"type": "FeatureCollection", "features": feats}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    size_kb = OUT.stat().st_size / 1024
    print(f"wrote {len(feats)} features, {size_kb:.1f} KB -> {OUT}")


if __name__ == "__main__":
    main()
