"""Directional drift cones — the boater-facing layer.

For each source bed, build a wedge that points DOWNDRIFT from the bed
along the mean heading of its currently-floating paddies, opening to the
angular spread, extending to the leading edge. Shaded by how much
floating kelp rides that corridor (the probability/amount). This restores
the "run this heading to intercept the kelp" value the heatmap lost —
now fate-aware (floating only) and amount-weighted.

Cones (and their centerlines) are CLIPPED to open water — the part of a
wedge that would sweep over the coast or an island is removed, since
kelp doesn't drift across land.
"""
from __future__ import annotations

from collections import defaultdict

from shapely.geometry import Polygon, LineString, shape, mapping
from shapely.ops import unary_union

import config
import geo
import landmask as landmask_mod

MIN_PARTICLES = 4
MIN_REACH_KM = 3.0      # below this it hasn't really drifted -> no cone
ARC_STEPS = 12
SIMPLIFY_DEG = 0.003    # ~330 m — keep the coastline-clipped edge light

_LAND = None
_LAND_LOADED = False


def _land_union():
    """Shapely union of all land polygons (cached); None if unavailable."""
    global _LAND, _LAND_LOADED
    if _LAND_LOADED:
        return _LAND
    _LAND_LOADED = True
    gj = landmask_mod._load_land()
    if not gj:
        return None
    geoms = []
    for feat in gj.get("features", []):
        try:
            g = shape(feat.get("geometry"))
            if g and not g.is_empty:
                geoms.append(g)
        except Exception:
            pass
    if not geoms:
        return None
    u = unary_union(geoms)
    _LAND = u if u.is_valid else u.buffer(0)
    return _LAND


def _clip(geom_shapely, land):
    if land is None:
        return geom_shapely
    out = geom_shapely.difference(land)
    if out.is_empty:
        return out
    return out.simplify(SIMPLIFY_DEG, preserve_topology=True)


def build(floating, beds):
    bed_lookup = {b[0]: (b[1], b[2], b[4]) for b in beds}   # name -> (lng, lat, island)
    by_bed = defaultdict(list)
    for p in floating:
        by_bed[p["bed"]].append(p)

    raw = []
    for name, pts in by_bed.items():
        blng, blat, island = bed_lookup[name]
        amt = sum(p["float_w"] for p in pts)
        if len(pts) < MIN_PARTICLES or amt <= 0:
            continue
        bearings = [geo.bearing_deg(blng, blat, p["lng"], p["lat"]) for p in pts]
        dists = sorted(geo.haversine_km(blng, blat, p["lng"], p["lat"]) for p in pts)
        mb = geo.circular_mean_deg(bearings)
        devs = sorted(geo.ang_diff(b, mb) for b in bearings)
        half = min(55.0, max(8.0, devs[int(0.8 * (len(devs) - 1))]))
        reach = dists[int(0.85 * (len(dists) - 1))]
        if reach < MIN_REACH_KM:
            continue
        raw.append((name, island, blng, blat, amt, mb, half, reach))

    if not raw:
        return [], []
    raw.sort(key=lambda r: -r[4])               # biggest drift corridors (by floating amount) first
    raw = raw[:getattr(config, "N_CONES", 8)]   # cap so the real-kelp cells don't throw dozens of busy fans

    land = _land_union()
    max_amt = max(r[4] for r in raw)
    cones, lines = [], []
    for (name, island, blng, blat, amt, mb, half, reach) in raw:
        op = round(min(1.0, amt / max_amt) ** 0.6, 2)
        ring = [(blng, blat)]
        for k in range(ARC_STEPS + 1):
            a = mb - half + (2 * half) * k / ARC_STEPS
            ring.append(geo.dest_point(blng, blat, a, reach))
        wedge = Polygon(ring)
        if not wedge.is_valid:
            wedge = wedge.buffer(0)
        wedge = _clip(wedge, land)
        if wedge.is_empty or wedge.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        cones.append({"type": "Feature", "geometry": mapping(wedge),
                      "properties": {"bed": name, "island": island, "opacity": op,
                                     "bearing": round(mb), "compass": geo.compass(mb),
                                     "reach_nm": round(reach / 1.852)}})

        tip = geo.dest_point(blng, blat, mb, reach)
        ln = _clip(LineString([(blng, blat), tip]), land)
        if not ln.is_empty and ln.geom_type in ("LineString", "MultiLineString"):
            lines.append({"type": "Feature", "geometry": mapping(ln),
                          "properties": {"bed": name}})
    return cones, lines
