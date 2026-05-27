"""Fetch observed California Kelp canopy polygons (Kelp Forests
California 2002-2016 service, BIO_CA_Kelp2016 layer) — the actual
aerial-survey canopy footprints, NOT the CDFW Administrative Kelp
Beds (which are management rectangles, kept separately).

Replaces (visually) the admin-bed overlay for "where is the kelp?"
since admin beds are leasing boundaries, not actual canopy. Each
feature carries:

  * geometry: WGS84 Polygon (clipped to active region bbox)
  * properties.bedNumber: CDFW bed number (1..87 — same numbering as
    admin beds, so callers can cross-reference)
  * properties.className: "Kelp Canopy" (surface) | "Kelp Subsurface"
  * properties.areaKm2: feature area, rounded
  * properties.name: curated name when known (Point Loma, La Jolla,
    Catalina West End, ...) else "Bed <N>"
  * properties.id: slugified name

The 2016 survey is the most recent statewide pass in this service.
Year-by-year layers 1..9 cover 2015, 2013, 2011 (SCSR), 2010
(SCSR), 2009, 2008, 2006, 2005, 2003. We pull layer 0 (2016) only
for v1 — Phase 3 of the kelp roadmap can layer in the time series
later via the existing timeline machinery.

Source verified 2026-05-27. CDFW publishes via:
https://www.arcgis.com/home/item.html?id=<service-item-id>

Run on demand or as a daily refresh step:
  python pipeline/fetch_kelp_canopy.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

BBOX = active_region().bbox

# 2016 statewide CA kelp canopy aerial survey — most recent in this
# series. WGS84 output via outSR=4326.
BASE_URL = (
    "https://services1.arcgis.com/jOKMO9vKmc98J4JC/arcgis/rest/services/"
    "Kelp_Forests_California_2002_to_2016/FeatureServer/0/query"
)
PAGE_SIZE = 1000

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = active_region().data_output_dir(ROOT) / "kelp-canopy.geojson"

# Curated names for well-known established kelp forests. Lookup by
# CDFW kelp-bed number (matches admin beds ds3135 numbering). Beds
# without a curated name fall back to "Bed <N>".
#
# Numbers verified against the CDFW Administrative Kelp Beds map
# (Pacific Fishery Management Council appendix). Bed 1 is Coronado
# (Mexico border); numbering runs north up the SoCal coast then
# jumps to the Channel Islands and Central California.
BED_NAMES = {
    1:  "Coronado",
    2:  "Imperial Beach",
    3:  "Point Loma",
    4:  "La Jolla",
    5:  "Del Mar",
    6:  "Solana Beach / Cardiff",
    7:  "Carlsbad",
    8:  "Oceanside",
    9:  "Camp Pendleton",
    10: "San Onofre",
    11: "San Clemente",
    12: "Dana Point / Salt Creek",
    13: "Laguna Beach",
    14: "Newport Beach",
    15: "Huntington / Bolsa Chica",
    16: "Long Beach / San Pedro",
    17: "Palos Verdes - South",
    18: "Palos Verdes - West",
    19: "Palos Verdes - North",
    20: "Santa Monica Bay",
    21: "Malibu",
    22: "Point Dume",
    23: "Point Mugu",
    24: "Ventura - Pierpont",
    25: "Carpinteria",
    26: "Santa Barbara - East",
    27: "Santa Barbara - Mesa",
    28: "Goleta / Ellwood",
    29: "Refugio",
    30: "Gaviota / Tajiguas",
    31: "Point Conception - East",
    32: "Point Conception - West",
    33: "Cojo / Government Point",
    34: "Vandenberg South",
    35: "Vandenberg North",
    40: "San Miguel Island - South",
    41: "San Miguel Island - North",
    42: "Santa Rosa Island - South",
    43: "Santa Rosa Island - North",
    44: "Santa Cruz Island - West",
    45: "Santa Cruz Island - South",
    46: "Santa Cruz Island - North",
    47: "Santa Cruz Island - East",
    48: "Anacapa Island",
    49: "Santa Barbara Island",
    50: "San Nicolas Island",
    51: "Catalina Island - West End",
    52: "Catalina Island - North",
    53: "Catalina Island - Avalon",
    54: "Catalina Island - South",
    55: "San Clemente Island - West",
    56: "San Clemente Island - South",
    57: "San Clemente Island - East",
    58: "San Clemente Island - North",
    66: "Point Estero",
    67: "Cambria",
    68: "San Simeon",
    69: "Big Sur - South",
    70: "Big Sur - Central",
    71: "Big Sur - North",
    72: "Carmel Bay",
    73: "Monterey Peninsula",
    74: "Pacific Grove / Lover's Point",
    75: "Monterey Bay - South",
    76: "Santa Cruz County",
    77: "San Mateo Coast",
    78: "Half Moon Bay",
    79: "Pillar Point",
}


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def feature_bounds(geom: dict) -> tuple[float, float, float, float]:
    coords = geom["coordinates"]
    pts: list[tuple[float, float]] = []
    if geom["type"] == "Polygon":
        for ring in coords: pts.extend(ring)
    elif geom["type"] == "MultiPolygon":
        for poly in coords:
            for ring in poly: pts.extend(ring)
    if not pts: return (0, 0, 0, 0)
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def round_coords(geom: dict, decimals: int = 4) -> dict:
    """4 decimals (~11 m) — same precision as the admin-bed and MPA
    layers post-bump. Smooth at deep zoom (16×) in the spot detail."""
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


def _get_with_retry(url, params, *, max_attempts=4):
    """GET with bounded backoff. The kelp-2016 service hits 504
    Gateway Timeout under modest load. Backoff stays small (2/5/10/20 s)
    so the total worst-case for a single call sits at ~37 s of waits
    + 4 × 90 s requests = ~7 min, comfortably inside the 15-min step
    timeout the workflow now grants this step.
    """
    import time
    last_err = None
    delays = [2, 5, 10, 20]
    for attempt in range(max_attempts):
        try:
            r = requests.get(url, params=params, timeout=90)
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {r.status_code}"
                if attempt < max_attempts - 1:
                    delay = delays[attempt]
                    print(f"  {last_err} — retry {attempt + 1}/{max_attempts} in {delay}s")
                    time.sleep(delay)
                    continue
            r.raise_for_status()
            return r
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = type(e).__name__
            if attempt < max_attempts - 1:
                delay = delays[attempt]
                print(f"  {last_err}: {e} — retry {attempt + 1}/{max_attempts} in {delay}s")
                time.sleep(delay)
                continue
            raise
    raise requests.HTTPError(f"exhausted retries; last={last_err}")


def fetch_all_features() -> list[dict]:
    """Page through the FeatureServer query endpoint with retries.

    The kelp-2016 service is heavier than the admin-bed service —
    each polygon has 100s-1000s of vertices, and the service node
    routinely 504s on default page sizes. We use a smaller page size
    (300) + retry-with-backoff (in _get_with_retry) to ride out the
    flakiness.
    """
    features: list[dict] = []
    offset = 0
    # 2026-05-27: page_size 1000 → 300 → 100. First refresh's 504s
    # showed the service node can't reliably serialize 300 high-
    # vertex polygons inside the 60-90s soft cap. 100 lands in <10s
    # on a healthy node, ~30s on a slow one. 155 total features →
    # 2 pages plus the empty closer.
    page_size = 100
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }
        print(f"GET BIO_CA_Kelp2016?resultOffset={offset}&resultRecordCount={page_size}")
        r = _get_with_retry(BASE_URL, params)
        page = r.json()
        page_feats = page.get("features", []) or []
        features.extend(page_feats)
        more = bool(
            page.get("exceededTransferLimit")
            or (page.get("properties") or {}).get("exceededTransferLimit")
        )
        if not more or not page_feats:
            break
        offset += len(page_feats)
        if offset > 100_000:
            print(f"WARN: aborting pagination at offset={offset}")
            break
    return features


def main() -> None:
    raw_features = fetch_all_features()
    print(f"fetched {len(raw_features)} canopy features from BIO_CA_Kelp2016")

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
        bed_number = props.get("KelpBed")
        class_name = props.get("Class_Name") or "Kelp Canopy"
        area_raw = props.get("Shape__Area") or props.get("Shape_Area")
        area_km2 = None
        if area_raw is not None:
            try:
                # Service publishes area in m² (esriMeters units).
                area_km2 = round(float(area_raw) / 1e6, 4)
            except (TypeError, ValueError):
                area_km2 = None

        name = BED_NAMES.get(bed_number) if isinstance(bed_number, int) else None
        if not name and bed_number is not None:
            name = f"Bed {bed_number}"
        if not name:
            # Anonymous feature — skip rather than emit a junk id
            continue
        canopy_id = slugify(name)
        if class_name == "Kelp Subsurface":
            canopy_id += "-subsurface"

        slim_props = {
            "id": canopy_id,
            "name": name,
            "bedNumber": bed_number,
            "className": class_name,
            "areaKm2": area_km2,
            "year": 2016,
        }
        keep.append({
            "type": "Feature",
            "geometry": round_coords(geom, decimals=4),
            "properties": slim_props,
        })

    out = {"type": "FeatureCollection", "features": keep}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    size_kb = OUT_PATH.stat().st_size / 1024
    print(
        f"wrote {len(keep)} canopy features to {OUT_PATH.name} "
        f"({size_kb:.1f} KB; raw fetched: {len(raw_features)})"
    )
    # Class distribution
    classes: dict[str, int] = {}
    for f in keep:
        c = f["properties"]["className"] or "unknown"
        classes[c] = classes.get(c, 0) + 1
    for c, n in sorted(classes.items(), key=lambda kv: -kv[1]):
        print(f"  {c}: {n}")
    # Top 10 by area
    print("\nTop 10 by area:")
    by_area = sorted(keep, key=lambda f: f["properties"]["areaKm2"] or 0, reverse=True)[:10]
    for f in by_area:
        p = f["properties"]
        print(f"  {p['name']:30s} {p['className']:15s} {p['areaKm2']} km²")


if __name__ == "__main__":
    main()
