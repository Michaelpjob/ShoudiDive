"""Fetch CDFW Administrative Kelp Beds (R7, ds3135) for the active region,
clip to bbox, slim properties, and write to
public/data/<region>/kelp-beds.geojson (or `public/data/kelp-beds.geojson`
for CA).

The 87 administrative kelp beds are management/reference boundaries for
commercial kelp harvest — *not* observed canopy. They rarely change, so
this fetcher runs in the daily refresh workflow but emits identical output
most days.

Differences from fetch_mpa.py (worth knowing):
  * Source endpoint is an ArcGIS FeatureServer query (paginated) rather
    than a static .geojson download.
  * Field names aren't fully standardised — we discover them via a
    fallback chain like fetch_mpa.py does.
  * Coordinates are rounded to 4 decimals (~11 m) instead of 3, since
    nearshore edge precision is the whole point of this overlay.

Run on demand or as a daily refresh step:
  python pipeline/fetch_kelp.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

# Bbox via pipeline/regions/ (PR-X-1). CA / PNW / tropical / baja switch
# on SHOULDIDIVE_REGION; default `ca` preserves today's behavior. CDFW
# Administrative Kelp Beds is California-only today — for non-CA
# regions the bbox-clip step will keep 0 features and we write an empty
# FeatureCollection (KelpLayer.jsx no-ops on missing data).
try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

BBOX = active_region().bbox

# CDFW ArcGIS FeatureServer — Administrative Kelp Beds (ds3135),
# layer 0, GeoJSON output in WGS84 (outSR=4326).
BASE_URL = (
    "https://services2.arcgis.com/Uq9r85Potqm3MfRV/arcgis/rest/services/"
    "biosds3135_fpu/FeatureServer/0/query"
)
PAGE_SIZE = 1000  # 87 features fit one page, but pagination is defensive.

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = active_region().data_output_dir(ROOT) / "kelp-beds.geojson"


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


def round_coords(geom: dict, decimals: int = 4) -> dict:
    """Round all coordinates in-place to keep JSON small. 4 decimals ~ 11 m."""

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


def fetch_all_features() -> list[dict]:
    """Page through the FeatureServer query endpoint until exhausted.

    The CDFW service returns `properties.exceededTransferLimit = true`
    (or `exceededTransferLimit = true` at the top level on older
    versions) when more pages remain. We defensively check both.
    """
    features: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
        }
        print(f"GET {BASE_URL}?resultOffset={offset}")
        r = requests.get(BASE_URL, params=params, timeout=120)
        r.raise_for_status()
        page = r.json()
        page_feats = page.get("features", []) or []
        features.extend(page_feats)
        # Server can flag continuation at the top level OR inside properties.
        more = bool(
            page.get("exceededTransferLimit")
            or (page.get("properties") or {}).get("exceededTransferLimit")
        )
        if not more or not page_feats:
            break
        offset += len(page_feats)
        if offset > 100_000:
            # Safety fuse — kelp beds are tiny, runaway pagination = bug.
            print(f"WARN: aborting pagination at offset={offset}")
            break
    return features


def main() -> None:
    raw_features = fetch_all_features()

    keep = []
    for feat in raw_features:
        geom = feat.get("geometry")
        if not geom:
            continue
        min_lng, min_lat, max_lng, max_lat = feature_bounds(geom)
        if max_lng < BBOX["lng_min"] or min_lng > BBOX["lng_max"]:
            continue
        if max_lat < BBOX["lat_min"] or min_lat > BBOX["lat_max"]:
            continue

        props = feat.get("properties", {}) or {}
        # Bed name — common ArcGIS field variants.
        name = (
            props.get("BED_NAME")
            or props.get("BedName")
            or props.get("NAME")
            or props.get("Name")
            or props.get("KELP_BED_NAME")
        )
        # Bed number — try the documented variants.
        bed_number = (
            props.get("BED_NUMBER")
            or props.get("BedNumber")
            or props.get("Bed_Number")
            or props.get("BED_NO")
            or props.get("KelpBedNo")
        )
        # Status — open / closed / leasable / leased.
        status_raw = (
            props.get("STATUS")
            or props.get("Status")
            or props.get("BED_STATUS")
            or props.get("BedStatus")
            or props.get("LEASE_STATUS")
        )
        status = str(status_raw).strip().lower() if status_raw else None

        area_km2 = None
        if props.get("Shape_Area") is not None:
            try:
                # Shape_Area on ArcGIS Online (WGS84) is in m² when the
                # service is configured with a geometric area calc.
                area_km2 = round(float(props["Shape_Area"]) / 1e6, 3)
            except (TypeError, ValueError):
                area_km2 = None

        # Derive an id: prefer slugified name; fall back to bed number.
        if name:
            kelp_id = slugify(str(name))
        elif bed_number is not None:
            kelp_id = f"kelp-bed-{bed_number}"
        else:
            # Truly anonymous feature — skip rather than emit a junk id.
            continue

        slim_props = {
            "id": kelp_id,
            "name": name or f"Kelp Bed {bed_number}",
            "bedNumber": bed_number,
            "status": status,
            "areaKm2": area_km2,
        }

        keep.append(
            {
                "type": "Feature",
                "geometry": round_coords(geom, decimals=4),
                "properties": slim_props,
            }
        )

    out = {"type": "FeatureCollection", "features": keep}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    size_kb = OUT_PATH.stat().st_size / 1024
    print(
        f"wrote {len(keep)} kelp bed features to {OUT_PATH.name} "
        f"({size_kb:.1f} KB; raw fetched: {len(raw_features)})"
    )
    # Print status distribution for visibility — matches the type
    # distribution print in fetch_mpa.py.
    statuses: dict[str, int] = {}
    for f in keep:
        s = f["properties"]["status"] or "unknown"
        statuses[s] = statuses.get(s, 0) + 1
    for s, n in sorted(statuses.items(), key=lambda kv: -kv[1]):
        print(f"  {s}: {n}")


if __name__ == "__main__":
    main()
