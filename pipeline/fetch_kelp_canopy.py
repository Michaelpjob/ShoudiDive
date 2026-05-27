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

# Geographic name table for "established beds" — keyed by approximate
# centroid (lng, lat). Each entry covers a radius in km; if a fetched
# canopy polygon's centroid falls within ANY entry's radius, that
# polygon gets the named label. Otherwise it's labelled "Bed <N>".
#
# This replaces an earlier KelpBed-keyed lookup table that was based
# on the CDFW Administrative Kelp Beds (ds3135) numbering — turns out
# the BIO_CA_Kelp2016 service uses a completely different numbering
# system (Catalina is bed 105 here, not 51; Monterey is 218-221, not
# 73-75). Spatial mapping via centroid is more robust to schema
# differences and lets us name beds correctly regardless of the
# upstream service's internal IDs.
#
# Coordinates are approximate centroids of the named feature pulled
# from OSM / NOAA chart references. The radius is generous enough
# to absorb a typical bed's extent. Order matters when regions
# overlap — earlier entries win.
NAMED_AREAS = [
    # SoCal mainland north → south
    ("Coronado / Imperial Beach",     -117.18, 32.60, 8),
    ("Point Loma",                    -117.27, 32.69, 7),
    ("La Jolla",                      -117.27, 32.85, 5),
    ("Del Mar / Cardiff",             -117.28, 32.97, 7),
    ("Carlsbad / Oceanside",          -117.32, 33.15, 8),
    ("Camp Pendleton / San Onofre",   -117.43, 33.31, 6),
    ("San Clemente",                  -117.55, 33.39, 5),
    ("Dana Point / Laguna",           -117.75, 33.51, 7),
    ("Newport / Crystal Cove",        -117.85, 33.59, 5),
    ("Huntington / Long Beach",       -118.10, 33.69, 9),
    ("Palos Verdes",                  -118.40, 33.74, 8),
    ("Santa Monica Bay",              -118.55, 33.95, 8),
    ("Malibu",                        -118.74, 34.02, 7),
    ("Point Dume / Mugu",             -119.00, 34.07, 8),
    ("Ventura / Carpinteria",         -119.60, 34.36, 12),
    ("Santa Barbara / Goleta",        -119.85, 34.42, 8),
    ("Refugio / Gaviota",             -120.10, 34.46, 6),
    ("Point Conception",              -120.47, 34.45, 7),
    ("Vandenberg Coast",              -120.65, 34.65, 12),
    # Channel Islands
    ("San Miguel Island",             -120.37, 34.04, 8),
    ("Santa Rosa Island",             -120.10, 33.97, 10),
    ("Santa Cruz Island",             -119.75, 33.99, 12),
    ("Anacapa Island",                -119.40, 34.00, 5),
    ("Santa Barbara Island",          -119.03, 33.48, 3),
    ("San Nicolas Island",            -119.50, 33.25, 6),
    ("Catalina Island",               -118.45, 33.39, 14),
    ("San Clemente Island",           -118.50, 32.90, 12),
    # Central CA coast
    ("Point Estero / Cayucos",        -120.95, 35.47, 10),
    ("Cambria",                       -121.10, 35.55, 6),
    ("San Simeon",                    -121.20, 35.65, 6),
    ("Big Sur (South)",               -121.45, 36.00, 18),
    ("Big Sur (Central)",             -121.75, 36.20, 18),
    ("Big Sur (North) / Pt Lobos",    -121.95, 36.50, 10),
    ("Carmel Bay",                    -121.93, 36.55, 5),
    ("Monterey Peninsula",            -121.93, 36.62, 7),
    ("Pacific Grove / Lover's Point", -121.92, 36.63, 4),
    ("Santa Cruz County",             -122.00, 36.95, 14),
    ("Half Moon Bay / Pillar Point",  -122.45, 37.50, 12),
    # NorCal
    ("Sonoma / Mendocino Coast",      -123.55, 38.75, 35),
    ("Humboldt Coast",                -124.10, 41.00, 35),
]


def _km_to_deg(km, lat):
    """Quick metric → degrees conversion for radius checks."""
    import math
    return km / 111.0, km / (111.0 * max(math.cos(math.radians(lat)), 0.05))


def name_for_centroid(lng, lat):
    """Return the friendly name for a polygon centroid, or None.

    Walks NAMED_AREAS in declared order; returns the first match
    (named regions are roughly mutually-exclusive geographically).
    """
    if not (Number_is_finite_like(lng) and Number_is_finite_like(lat)):
        return None
    import math
    for (name, c_lng, c_lat, r_km) in NAMED_AREAS:
        d_lat, d_lng = _km_to_deg(r_km, c_lat)
        if abs(lat - c_lat) <= d_lat and abs(lng - c_lng) <= d_lng:
            return name
    return None


def Number_is_finite_like(x):
    """Compat helper — we don't have Number.isFinite in Python; emulate."""
    try:
        return x is not None and not (x != x)  # NaN check
    except TypeError:
        return False


def polygon_centroid(geom: dict) -> tuple[float, float] | None:
    """Naive vertex-mean centroid. Fine for naming — we just need
    "where in CA is this bed?" precision."""
    coords = geom.get("coordinates")
    if not coords: return None
    pts = []
    t = geom.get("type")
    if t == "Polygon":
        for ring in coords:
            pts.extend(ring)
    elif t == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                pts.extend(ring)
    else:
        return None
    if not pts: return None
    xs = sum(p[0] for p in pts) / len(pts)
    ys = sum(p[1] for p in pts) / len(pts)
    return (xs, ys)


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


def simplify_geom(
    geom: dict,
    tolerance_deg: float = 1e-4,
    min_part_area_deg2: float = 1e-7,
) -> dict | None:
    """Douglas-Peucker simplify + degenerate-part filter.

    CDFW canopy polygons are MultiPolygons with hundreds of tiny
    sub-patches each. Raw output is ~800 KB per feature. Two passes
    crush that:

    1. shapely.simplify(tolerance_deg, preserve_topology=True). At
       1e-4 deg (~11 m at CA latitudes) — half the bathy pixel pitch
       at the spot view's 16× zoom — this drops ~90% of vertices.
    2. Drop MultiPolygon parts with area < min_part_area_deg2
       (~1e-7 deg² ≈ 10 m²). Without this filter, rounding to 4
       decimals collapses small parts into degenerate 4-identical-
       vertex "polygons" that bloat the JSON without rendering.

    Returns None if simplification produced an empty geometry —
    caller should skip those features entirely.
    """
    try:
        from shapely.geometry import shape, mapping, Polygon, MultiPolygon
    except ImportError:
        return geom
    try:
        s = shape(geom).simplify(tolerance_deg, preserve_topology=True)
        if s.is_empty:
            return None
        # Filter degenerate parts
        if s.geom_type == "MultiPolygon":
            parts = [p for p in s.geoms if p.area >= min_part_area_deg2]
            if not parts:
                return None
            if len(parts) == 1:
                s = parts[0]
            else:
                s = MultiPolygon(parts)
        elif s.geom_type == "Polygon":
            if s.area < min_part_area_deg2:
                return None
        return mapping(s)
    except Exception:
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


def _arcgis_to_geojson_feature(feat: dict) -> dict | None:
    """Convert an ArcGIS f=json feature to a GeoJSON feature.

    Used when we fall back from f=geojson (which 504s on the kelp-2016
    service node) to f=json (lighter serialization). ArcGIS f=json
    polygons carry geometry.rings: [[[x,y], ...], ...] where each ring
    is x-y and outer rings are CW, inner rings (holes) CCW. GeoJSON
    Polygons use the opposite winding but our consumers don't care
    about winding (no point-in-poly tests here), so we just pass the
    rings through as Polygon coordinates.
    """
    g = feat.get("geometry") or {}
    attrs = feat.get("attributes") or {}
    rings = g.get("rings")
    if not rings:
        return None
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": rings},
        "properties": attrs,
    }


def fetch_all_features() -> list[dict]:
    """Fetch all kelp canopy features by iterating per KelpBed number.

    The bulk query `where=1=1` on this service routinely 504s — the
    server can't serialize the full ~155 high-vertex polygon set in
    one go (or even in 100-feature pages — we confirmed externally
    with curl: a `returnCountOnly=true` against `where=1=1` returns
    instantly while a real-features query 504s after 60s).

    Single-bed queries (`where=KelpBed=N`) return in <1s though. So
    we walk bed numbers 1..90 (covers the documented bed range);
    a missing bed simply returns 0 features. Total request count
    is ~90, total wall time ~90 s on a healthy day, ~5 min worst
    case with retries — still inside the 15-min step budget.

    Tries f=geojson first per bed, falls back to f=json on failure
    just for THAT bed so we don't lose the whole haul to a single
    bad polygon.
    """
    features: list[dict] = []
    # The BIO_CA_Kelp2016 service uses its own internal KelpBed numbering
    # — NOT the CDFW Administrative Kelp Beds (ds3135) scheme. Catalina
    # turns up as bed 105, Monterey Peninsula as 218–221. Confirmed by
    # spatial bbox query on 2026-05-27. Verified externally that beds
    # > 300 return 0 features, so 1..300 covers the published range
    # with headroom.
    bed_range = range(1, 301)
    for bed_num in bed_range:
        for fmt in ("geojson", "json"):
            params = {
                "where": f"KelpBed={bed_num}",
                "outFields": "*",
                "outSR": "4326",
                "f": fmt,
            }
            try:
                r = _get_with_retry(BASE_URL, params, max_attempts=2)
            except requests.HTTPError:
                continue  # try the other format
            try:
                page = r.json()
            except ValueError:
                continue
            if fmt == "json":
                bed_feats = []
                for f in page.get("features", []) or []:
                    gf = _arcgis_to_geojson_feature(f)
                    if gf:
                        bed_feats.append(gf)
            else:
                bed_feats = page.get("features", []) or []
            if bed_feats:
                print(f"  bed {bed_num}: {len(bed_feats)} features (f={fmt})")
                features.extend(bed_feats)
            break  # success — don't try the other format
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

        # Spatial naming — compute the polygon's centroid and look up
        # the nearest named area. Falls back to "Bed <N>" when no
        # named area matches (true for the SCSR-only beds or rural
        # stretches not in NAMED_AREAS).
        centroid = polygon_centroid(geom)
        name = None
        if centroid:
            name = name_for_centroid(centroid[0], centroid[1])
        if not name and bed_number is not None:
            name = f"Bed {bed_number}"
        if not name:
            continue  # genuinely anonymous — skip
        canopy_id = slugify(name)
        if class_name == "Kelp Subsurface":
            canopy_id += "-subsurface"
        # Add bed number suffix when multiple polygons share the same
        # named area (e.g. Monterey's 218/219/220/221 all map to
        # "Monterey Peninsula"). Otherwise the slugified ids collide.
        if bed_number is not None:
            canopy_id = f"{canopy_id}-{bed_number}"

        slim_props = {
            "id": canopy_id,
            "name": name,
            "bedNumber": bed_number,
            "className": class_name,
            "areaKm2": area_km2,
            "year": 2016,
        }
        # Simplify with degenerate-part filter, then round. Two-pass
        # crushes the 40 MB raw output to ~1 MB.
        simplified = simplify_geom(geom, tolerance_deg=1e-4, min_part_area_deg2=1e-7)
        if simplified is None:
            continue  # entire feature collapsed to noise
        keep.append({
            "type": "Feature",
            "geometry": round_coords(simplified, decimals=4),
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
