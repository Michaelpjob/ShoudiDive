"""Named offshore fishing features for the feature-snap (HANDOFF addendum P3).

People don't run to a disc — they run to an island edge or a named bank.
We snap the headline waypoint to the nearest of these and phrase guidance as
a line to work. (name, lng, lat, type). Type "island" -> "the SW edge of X";
"bank" -> "work X". Bank coordinates are APPROXIMATE — refine before prod.
"""
from __future__ import annotations

import geo

OFFSHORE_FEATURES = [
    # Channel Islands
    ("Catalina Island", -118.42, 33.39, "island"),
    ("San Clemente Island", -118.55, 32.90, "island"),
    ("San Nicolas Island", -119.50, 33.25, "island"),
    ("Santa Barbara Island", -119.04, 33.48, "island"),
    ("Santa Cruz Island", -119.75, 34.00, "island"),
    ("Santa Rosa Island", -120.10, 33.97, "island"),
    ("San Miguel Island", -120.37, 34.05, "island"),
    ("Anacapa Island", -119.40, 34.00, "island"),
    ("the Coronado Islands", -117.25, 32.42, "island"),
    # Offshore banks / spots (approximate)
    ("Cortes Bank", -119.18, 32.44, "bank"),
    ("Tanner Bank", -119.13, 32.69, "bank"),
    ("the 9-Mile Bank", -117.42, 32.62, "bank"),
    ("the 43-Fathom Spot", -117.47, 32.55, "bank"),
    # San Pedro Channel 14-Mile Bank (~14 nm off Newport, between the coast and
    # Catalina's east end). Was mis-placed off San Diego (-117.67, 32.77), where
    # there is no such bank — the famous "14" is this one, by Catalina.
    ("the 14-Mile Bank", -118.03, 33.41, "bank"),
    ("the 182 Spot", -117.43, 32.43, "bank"),
    ("the 302 Spot", -117.55, 32.60, "bank"),
    ("the 277", -117.47, 32.43, "bank"),
]


def nearest(lat, lng):
    """Return (name, type, edge_compass, dist_nm) of the nearest feature to a point."""
    best = min(OFFSHORE_FEATURES, key=lambda f: geo.haversine_km(lng, lat, f[1], f[2]))
    name, flng, flat, ftype = best
    dist_nm = geo.haversine_km(flng, flat, lng, lat) / 1.852
    edge = geo.compass(geo.bearing_deg(flng, flat, lng, lat))
    return {"name": name, "type": ftype, "edge": edge, "dist_nm": round(dist_nm, 1)}


def describe(lat, lng, max_nm):
    """Plain-language 'work here' phrase, or None if nothing is reasonably close."""
    f = nearest(lat, lng)
    if f["type"] == "island":
        if f["dist_nm"] <= max_nm:
            return f"the {f['edge']} edge of {f['name']}"
        if f["dist_nm"] <= max_nm * 2:
            return f"toward {f['name']}"
    elif f["dist_nm"] <= max_nm * 1.5:
        return f"near {f['name']}"
    return None
