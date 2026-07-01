"""Per-bed directional WAVE EXPOSURE (canopy-dynamics Phase 0).

Each kelp bed only feels a swell it has open-ocean fetch toward. A SW
groundswell hammers SW-facing outer-island and exposed-point beds and does
almost nothing to a bed tucked in an island lee. We capture that by ray-casting
each bed against ShoudiDive's coastline (land.geojson, already loaded) in 36
compass directions and recording how far the open ocean reaches before land
blocks it — the bed's "fetch profile". For an incoming swell FROM bearing Dp,
exposure(bed, Dp) in [0,1] is the (angularly-spread) openness toward Dp.

Grounding: island shadowing produced 2.8->6.7 m Hs over 150 km in ONE storm
(Seymour et al. 1989, ECSS 28:277); fetch/exposure indices are a validated
standard in kelp/rocky-shore ecology (Burrows et al.; NOAA WEMo).
"""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

import cones
import geo

N_DIRS = 36                    # 10-degree resolution
DIR_STEP = 360.0 / N_DIRS
MAX_FETCH_KM = 150.0           # beyond this, treat as fully open ocean
SWELL_SPREAD_DEG = 25.0        # angular half-spread of a real swell train

_PROFILE_CACHE = {}


def _snap_to_water(lng, lat, land):
    """Kelp beds sit AT the coast/island edge, so a bed centroid often falls
    inside the (simplified) land polygon -> every ray would start in land and
    read 0 exposure. Nudge the origin to the nearest open water (smallest ring
    that clears land) before ray-casting. Mirrors drift._nearest_water."""
    if land is None or not land.contains(Point(lng, lat)):
        return lng, lat
    for r in (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0):
        for k in range(N_DIRS):
            tl, ta = geo.dest_point(lng, lat, k * DIR_STEP, r)
            if not land.contains(Point(tl, ta)):
                return tl, ta
    return lng, lat


def _fetch_profile(lng, lat, land):
    """Openness in each of N_DIRS directions: distance to the first land the
    ray hits (km) / MAX_FETCH, capped at 1. 1.0 = open ocean all the way (fully
    exposed to swell FROM that bearing); ~0 = land right there (sheltered)."""
    prof = np.ones(N_DIRS)
    if land is None:
        return prof
    lng, lat = _snap_to_water(lng, lat, land)
    origin = Point(lng, lat)
    for k in range(N_DIRS):
        bearing = k * DIR_STEP
        elng, elat = geo.dest_point(lng, lat, bearing, MAX_FETCH_KM)
        ray = LineString([(lng, lat), (elng, elat)])
        inter = ray.intersection(land)
        if inter.is_empty:
            prof[k] = 1.0
            continue
        # nearest point of the (possibly multi-part) land intersection
        try:
            near = nearest_points(origin, inter)[1]
        except Exception:
            prof[k] = 0.0
            continue
        d_km = geo.haversine_km(lng, lat, near.x, near.y)
        prof[k] = min(1.0, d_km / MAX_FETCH_KM)
    return prof


def build_profiles(beds, land=None):
    """{bed_name: fetch_profile(np[N_DIRS])} for every bed (cached by name+coords)."""
    if land is None:
        land = cones._land_union()
        if land is not None:
            # a light simplify keeps the ray-casts fast without moving the coast
            land = land.simplify(0.005, preserve_topology=True)
    out = {}
    for b in beds:
        name, lng, lat = b[0], b[1], b[2]
        key = (name, round(lng, 4), round(lat, 4))
        prof = _PROFILE_CACHE.get(key)
        if prof is None:
            prof = _fetch_profile(lng, lat, land)
            _PROFILE_CACHE[key] = prof
        out[name] = prof
    return out


def exposure(profile, dp_from_deg, spread=SWELL_SPREAD_DEG):
    """Exposure [0,1] of a bed (its fetch `profile`) to a swell coming FROM
    bearing `dp_from_deg`, cosine-weighted over the swell's angular spread."""
    if profile is None:
        return 1.0
    w_sum = e_sum = 0.0
    for k in range(N_DIRS):
        bearing = k * DIR_STEP
        ad = abs(geo.ang_diff(bearing, dp_from_deg))
        if ad <= spread:
            w = math.cos(0.5 * math.pi * ad / spread)
            w_sum += w
            e_sum += w * profile[k]
    return float(e_sum / w_sum) if w_sum > 0 else float(profile[int(round(dp_from_deg / DIR_STEP)) % N_DIRS])
