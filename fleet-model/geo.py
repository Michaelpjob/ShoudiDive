"""Small geodesy helpers (vectorised, numpy)."""
import numpy as np

R_KM = 6371.0088
NM_PER_KM = 1 / 1.852


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def min_dist_to_points_km(lat, lon, points):
    """Distance from each (lat, lon) to the nearest of `points` [(lat, lon), ...]."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    best = np.full(lat.shape, np.inf)
    idx = np.full(lat.shape, -1)
    for i, (plat, plon) in enumerate(points):
        d = haversine_km(lat, lon, plat, plon)
        m = d < best
        best[m] = d[m]
        idx[m] = i
    return best, idx
