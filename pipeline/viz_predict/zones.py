"""Zone classification: 3 latitude bands × 3 distance bands = 9 zones.

Plus nearest-Channel-Island lookup with a current-regime side ('east' = warm
SoCal Counter-Current side, 'west' = cold California Current side, 'open' =
neither).
"""
from __future__ import annotations
import numpy as np

from .config import (
    LAT_ZONE_BOUNDS, NEARSHORE_DIST_KM, NEARSHORE_MAX_DEPTH_M, ISLANDS_DIST_KM,
    CHANNEL_ISLAND_CENTROIDS,
)


def classify_zone(lat, dist_to_shore_km, dist_to_island_km, depth_m):
    """Return per-pixel zone strings like 'central_nearshore'."""
    lat = np.asarray(lat)
    dts = np.asarray(dist_to_shore_km)
    dti = np.asarray(dist_to_island_km)
    dpt = np.asarray(depth_m)

    # Latitude band
    central_lo, _ = LAT_ZONE_BOUNDS["central"]
    transition_lo, _ = LAT_ZONE_BOUNDS["transition"]
    lat_label = np.where(
        lat >= central_lo, "central",
        np.where(lat >= transition_lo, "transition", "bight"),
    )

    # Distance band: islands wins if within radius; else nearshore if close to
    # shore or shallow; else offshore.
    is_islands = dti < ISLANDS_DIST_KM
    is_nearshore = (dts < NEARSHORE_DIST_KM) | (dpt < NEARSHORE_MAX_DEPTH_M)
    dist_label = np.where(
        is_islands, "islands",
        np.where(is_nearshore, "nearshore", "offshore"),
    )

    # Concatenate "<lat>_<dist>" element-wise.
    out = np.char.add(np.char.add(lat_label.astype("U12"), "_"), dist_label.astype("U10"))
    return out


def nearest_channel_island(lat, lng):
    """Return per-point (name, distance_km, side).

    `side` is the island's current-regime label from config — 'east' /
    'west' / 'open'. Distance is approximate (great-circle equirectangular).
    """
    lat = np.asarray(lat, dtype=float)
    lng = np.asarray(lng, dtype=float)
    shape = lat.shape
    lat_f = lat.ravel()
    lng_f = lng.ravel()

    n = lat_f.size
    best_name = np.empty(n, dtype="U16")
    best_dist = np.full(n, np.inf, dtype=float)
    best_side = np.empty(n, dtype="U6")

    for name, (ilat, ilng, iside) in CHANNEL_ISLAND_CENTROIDS.items():
        d_lat = (lat_f - ilat) * 111.0
        d_lng = (lng_f - ilng) * 111.0 * np.cos(np.deg2rad((lat_f + ilat) * 0.5))
        d = np.sqrt(d_lat * d_lat + d_lng * d_lng)
        better = d < best_dist
        best_dist = np.where(better, d, best_dist)
        best_name = np.where(better, name, best_name)
        best_side = np.where(better, iside, best_side)

    return best_name.reshape(shape), best_dist.reshape(shape), best_side.reshape(shape)
