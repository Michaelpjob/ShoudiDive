"""Named offshore fishing features for the feature-snap (HANDOFF addendum P3).

People don't run to a disc — they run to an island edge or a named bank.
We snap the headline waypoint to the nearest of these and phrase guidance as
a line to work. (name, lng, lat, type). Type "island" -> "the SW edge of X";
"bank" -> "work X". Bank positions are chart-sourced and bathymetry-checked
(see the comment on the bank list).
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
    # Offshore banks / spots. Positions from the BD Outdoors SoCal offshore
    # chart (as transcribed in pelagic-lab's gazetteer) and the NOAA Coast
    # Pilot, each checked against GMRT bathymetry: banks sit on their tops,
    # and numbered spots sit where the depth matches their name in FATHOMS
    # (the 277 is a 277-fathom sounding, ~507 m). The previous numbered-spot
    # positions all sat in 1,000-1,300 m of water (2026-10-07 audit).
    # tests/reefLocations.test.js keeps these in step with the main map.
    ("Cortes Bank", -119.1256, 32.4461, "bank"),        # Bishop Rock, CP7
    ("Tanner Bank", -119.1333, 32.70, "bank"),
    ("the 9-Mile Bank", -117.4333, 32.6333, "bank"),
    ("the 43-Fathom Spot", -117.9736, 32.6555, "bank"),
    # San Pedro Channel 14-Mile Bank (~14 mi SSW of Newport Harbor, toward
    # Catalina's east end). There is no 14-Mile Bank off San Diego.
    ("the 14-Mile Bank", -117.9972, 33.40, "bank"),
    ("the 182 Spot", -117.7139, 32.6972, "bank"),
    ("the 302 Spot", -117.5833, 32.4472, "bank"),
    ("the 277", -118.0889, 33.20, "bank"),
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
