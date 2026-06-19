"""Fetch Marine Protected Area boundaries, slim properties, and write to
<region>/mpa-boundaries.geojson (the statewide source the spot-bundle builder
clips per spot).

  ca   — California MPAs from CDFW ArcGIS (ds582), direct GeoJSON download.
  baja — Mexican federal ANPs (marine + island) from the open CONANP-2020
         polygon layer (SEMARNAT / Proyecto Mesoamérica ArcGIS REST, no token),
         queried to the Baja bbox. The CONANP coastline-following polygons run
         10k-80k vertices, so they're simplified (~40 m) before rounding.

Only the fields the UI needs are kept. Coordinates round to 3 decimals
(~100 m at these latitudes) to keep the file small.

Run on demand or quarterly:
  python pipeline/fetch_mpa.py                       # ca (default)
  SHOULDIDIVE_REGION=baja python pipeline/fetch_mpa.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

# Bbox via pipeline/regions/ (PR-X-1). CA / PNW / tropical / baja switch on
# SHOULDIDIVE_REGION; default `ca` preserves today's behavior.
try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

REGION = active_region()
BBOX = active_region().bbox  # PR-X-2 contract literal (see fetch.py)

# CDFW Open Data Portal — California Marine Protected Areas (ds582).
# Direct GeoJSON download in WGS84.
CA_URL = (
    "https://data-cdfw.opendata.arcgis.com/datasets/"
    "117a99c8745a48c6a48bac70005b1b11_0.geojson"
)

# Mexican federal protected areas — "Áreas Naturales Protegidas (CONANP, 2020)"
# polygon layer, served openly (no token) on the Proyecto Mesoamérica ArcGIS.
# We query marine ANPs intersecting the Baja bbox; the nationwide "Islas del
# Golfo de California" umbrella (132k vertices, all Gulf islands) is excluded —
# every Baja dive area is already inside a specific Zona Marina / island park.
CONANP_URL = (
    "https://rmgir.proyectomesoamerica.org/server/rest/services/"
    "Aplicativos/SEDATU_TABASCO/MapServer/47/query"
)
CONANP_CAT = {
    "PN":   "Parque Nacional",
    "RB":   "Reserva de la Biosfera",
    "APFF": "Área de Protección de Flora y Fauna",
    "APRN": "Área de Protección de Recursos Naturales",
    "SANT": "Santuario",
    "MN":   "Monumento Natural",
}

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REGION.data_output_dir(ROOT) / "mpa-boundaries.geojson"


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
    """Round all coordinates in-place to keep JSON small.

    2026-05-27: bumped from 3 → 4 decimals (~110 m → ~11 m). At the
    wide-map zoom (8× max), 3-decimal precision was indistinguishable
    from 4-decimal because each pixel covered ~10-50 m anyway. But
    the new Spot Detail view (Phase 1B) zooms to 16× over 4-8 km
    bboxes — at that scale, 3-decimal polygons looked visibly
    staircased ("CAD outline" effect — the user QA flagged it as
    'rectangles' on Catalina MPAs). 4 decimals adds ~12% to the
    file size but makes edges smooth at deep zoom.
    """

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


def fetch_ca() -> list[dict]:
    """California — CDFW ds582 direct GeoJSON, bbox-filtered + slimmed."""
    print(f"GET {CA_URL}")
    r = requests.get(CA_URL, timeout=120)
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

        keep.append(
            {
                "type": "Feature",
                "geometry": round_coords(geom),
                "properties": {
                    "id": slugify(name),
                    "name": name,
                    "type": type_,
                    "shortName": short,
                    "areaKm2": area_km2,
                    "ccrCitation": ccr,
                },
            }
        )
    return keep


# Deep-sea / non-Baja ANPs that the bbox envelope catches but no diver visits:
# abyssal hydrothermal-vent + deep-Pacific sanctuaries, and the Nayarit/Pacific
# island parks whose southern bbox edge clips the Baja envelope corner.
BAJA_MPA_SKIP = ("Ventilas", "Profundo", "Islas Marías", "Islas Marias", "Marietas")


def fetch_baja() -> list[dict]:
    """Baja — CONANP-2020 marine + island ANPs, clipped to the Baja bbox.

    We include the "Islas del Golfo de California" umbrella (it's the only ANP
    protecting Gulf islands like Cerralvo / San Francisco / Las Ánimas that the
    specific Zona Marina parks don't cover) but clip every feature to the region
    bbox via a shapely intersection — that drops its Sonora/Sinaloa half (and
    trims the 132k-vertex nationwide geometry) so only the Baja side is stored.
    """
    from shapely.geometry import shape, mapping, box  # only needed here

    params = {
        "where": "S_MARINA > 0 OR NOMBRE LIKE '%Islas del Golfo de California%'",
        "geometry": (
            f"{BBOX['lng_min']},{BBOX['lat_min']},"
            f"{BBOX['lng_max']},{BBOX['lat_max']}"
        ),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "NOMBRE,CAT_MANEJO,S_MARINA,SUPERFICIE,ID_ANP",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    print(f"GET {CONANP_URL} (baja ANPs, bbox-filtered)")
    r = requests.get(CONANP_URL, params=params, timeout=120)
    r.raise_for_status()
    fc = r.json()

    clip_box = box(BBOX["lng_min"], BBOX["lat_min"], BBOX["lng_max"], BBOX["lat_max"])
    keep = []
    for feat in fc.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue
        props = feat.get("properties", {}) or {}
        nombre = props.get("NOMBRE")
        if not nombre or any(s in nombre for s in BAJA_MPA_SKIP):
            continue
        cat = props.get("CAT_MANEJO")
        sup = props.get("SUPERFICIE")
        try:
            area_km2 = round(float(sup) / 100.0, 1) if sup else None  # ha → km²
        except (TypeError, ValueError):
            area_km2 = None

        # Clip to the region bbox, then simplify (~40 m) — CONANP polygons trace
        # the coastline at 10k-80k vertices, far finer than a spot overlay needs.
        try:
            g = shape(geom).intersection(clip_box)
            if g.is_empty:
                continue
            g = g.simplify(0.0004, preserve_topology=True)
            geom = mapping(g)
        except Exception:  # noqa: BLE001 — keep raw geom if shapely chokes
            pass
        if geom["type"] not in ("Polygon", "MultiPolygon"):
            continue  # a point/line slice from the clip — not a usable overlay

        geom = round_coords(geom)
        base_props = {
            "id": slugify(nombre),
            "name": nombre,
            "type": CONANP_CAT.get(cat, cat or "ANP"),
            "shortName": nombre,
            "areaKm2": area_km2,
            "ccrCitation": None,
        }
        # Explode multi-part ANPs into per-polygon features. The spot-bundle
        # builder clips MPA with a *bbox-prefilter that keeps whole features*
        # (build_spot_bundles.clip_geojson_to_bbox), so the Gulf-spanning
        # "Islas del Golfo de California" umbrella kept whole would drop all
        # ~30 Baja islands into every Gulf bundle. One feature per island lets
        # the prefilter keep only the island(s) near each spot.
        if geom["type"] == "MultiPolygon" and len(geom["coordinates"]) > 1:
            for i, poly in enumerate(geom["coordinates"]):
                keep.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": poly},
                        "properties": {**base_props, "id": f"{base_props['id']}-{i}"},
                    }
                )
        else:
            keep.append(
                {"type": "Feature", "geometry": geom, "properties": base_props}
            )
    return keep


def main() -> None:
    region = REGION.name
    if region == "ca":
        keep = fetch_ca()
    elif region == "baja":
        keep = fetch_baja()
    else:
        print(f"no MPA source configured for region={region}; nothing to do")
        return

    out = {"type": "FeatureCollection", "features": keep}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"wrote {len(keep)} MPA features to {OUT_PATH} ({size_kb:.1f} KB)")
    # Print type distribution for visibility.
    types: dict[str, int] = {}
    for f in keep:
        t = f["properties"]["type"]
        types[t] = types.get(t, 0) + 1
    for t, n in sorted(types.items(), key=lambda kv: -kv[1]):
        print(f"  {t}: {n}")


if __name__ == "__main__":
    main()
