"""Extract ranked 'go here' waypoints from the findability surface.

peaks() returns launch-independent maxima (lat/lng/strength) — used by the
dashboard, which computes distance+bearing per launch in the browser.
extract() adds distance/bearing from a specific launch (CLI use).
"""
from __future__ import annotations

import math

import config
import geo


def _offshore(landmask, lng, lat):
    r = config.OFFSHORE_MIN_KM
    if landmask.is_land(lng, lat):
        return False
    for ang in range(0, 360, 45):
        dlat = (r / 111.0) * math.cos(math.radians(ang))
        dlng = (r / (111.0 * max(math.cos(math.radians(lat)), 0.1))) * math.sin(math.radians(ang))
        if landmask.is_land(lng + dlng, lat + dlat):
            return False
    return True


def peaks(dens, landmask):
    """Launch-independent ranked maxima: [{lat, lng, strength}]."""
    grid, lats, lngs = dens["grid"], dens["lats"], dens["lngs"]
    H, W = grid.shape
    peak = grid.max() or 1.0
    thr = peak * config.HOTSPOT_MIN_FRAC

    cands = []
    for j in range(1, H - 1):
        for i in range(1, W - 1):
            v = grid[j, i]
            if v < thr:
                continue
            if v < grid[j - 1:j + 2, i - 1:i + 2].max():
                continue
            lng, lat = float(lngs[i]), float(lats[j])
            if _offshore(landmask, lng, lat):
                cands.append((v, lng, lat))
    cands.sort(reverse=True)

    kept = []
    for v, lng, lat in cands:
        if all(geo.haversine_km(lng, lat, k[1], k[2]) > config.HOTSPOT_NMS_KM for k in kept):
            kept.append((v, lng, lat))
        if len(kept) >= config.N_HOTSPOTS:
            break
    return [{"lat": round(lat, 3), "lng": round(lng, 3), "strength": round(v / peak, 2)}
            for (v, lng, lat) in kept]


def extract(dens, landmask, launch_latlng):
    lla_lat, lla_lng = launch_latlng
    out = []
    for rank, p in enumerate(peaks(dens, landmask), 1):
        d_nm = geo.haversine_km(lla_lng, lla_lat, p["lng"], p["lat"]) / 1.852
        brg = geo.bearing_deg(lla_lng, lla_lat, p["lng"], p["lat"])
        out.append({**p, "rank": rank, "distance_nm": round(d_nm, 1),
                    "bearing_deg": round(brg), "compass": geo.compass(brg),
                    "reachable": d_nm <= config.REACHABLE_NM})
    return out
