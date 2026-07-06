"""Small geodesy helpers shared across modules."""
from __future__ import annotations

import math


def km_per_deg(lat):
    return 111.0, 111.0 * math.cos(math.radians(lat))


def haversine_km(lng1, lat1, lng2, lat2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lng1, lat1, lng2, lat2):
    """Initial compass bearing from point 1 -> point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass(b):
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((b + 11.25) // 22.5) % 16]


def dest_point(lng, lat, bearing, dist_km):
    """Point reached from (lng,lat) on `bearing` after `dist_km`."""
    R = 6371.0
    br, d = math.radians(bearing), dist_km / R
    p1, l1 = math.radians(lat), math.radians(lng)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(br))
    l2 = l1 + math.atan2(math.sin(br) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(l2), math.degrees(p2)


def circular_mean_deg(degs):
    s = sum(math.sin(math.radians(d)) for d in degs)
    c = sum(math.cos(math.radians(d)) for d in degs)
    return (math.degrees(math.atan2(s, c)) + 360) % 360


def ang_diff(a, b):
    """Smallest absolute angular difference in degrees (0..180)."""
    return abs((a - b + 180) % 360 - 180)
