"""Where the fleet stopped on fish, from AIS, for the Ocean Report's Hot Bites.

A sport boat that stops and holds a spot is usually fishing it; several boats
stopping within a mile of each other the same day is the stronger sign. AIS
says where and how long, never what was caught, so these points carry boats
and minutes, not species. Offshore stops only: the nearshore band (bait
grounds, harbor approaches) is left out, as in the fleet tool.
"""
from __future__ import annotations

from datetime import date, timedelta

from .zones import SHORT, ZONES, nm_between, zone_of

DAYS = 4
CLUSTER_NM = 1.0       # stops this close on the same day are one spot
LONE_MIN = 60          # a single boat counts once it held a spot this long
# Harbor mouths: a boat holding here is waiting on bait, a slip or the tide,
# not fishing (the first live run put a 4-hour "spot" at the San Diego Bay
# entrance). Approximate entrance centers.
HARBOR_NM = 1.0
HARBOR_MOUTHS = [
    (32.678, -117.236),   # San Diego Bay
    (32.756, -117.253),   # Mission Bay
    (33.204, -117.396),   # Oceanside
    (33.456, -117.697),   # Dana Point
    (33.594, -117.879),   # Newport
    (33.709, -118.250),   # Angels Gate, LA Harbor
    (33.846, -118.400),   # King Harbor, Redondo
    (33.966, -118.462),   # Marina del Rey
    (31.853, -116.630),   # Ensenada
]


def fleet_hot_bites(fleet: dict | None, today: date, days: int = DAYS) -> list[dict]:
    if not fleet:
        return []
    boats = fleet.get("boats", [])
    since = (today - timedelta(days=days - 1)).isoformat()
    by_day: dict[str, list] = {}
    for st in fleet.get("stops", []):
        if st[1] < since or st[1] > today.isoformat() or st[-1]:
            continue
        if any(nm_between((st[2], st[3]), h) <= HARBOR_NM for h in HARBOR_MOUTHS):
            continue
        by_day.setdefault(st[1], []).append(st)
    out = []
    for day, stops in sorted(by_day.items()):
        left = sorted(stops, key=lambda s: -s[4])
        while left:
            seed = left.pop(0)
            group = [seed] + [s for s in left if nm_between((s[2], s[3]), (seed[2], seed[3])) <= CLUSTER_NM]
            left = [s for s in left if s not in group]
            boat_ids = sorted({s[0] for s in group})
            minutes = sum(s[4] for s in group)
            if len(boat_ids) < 2 and minutes < LONE_MIN:
                continue
            lat = sum(s[2] * s[4] for s in group) / minutes
            lng = sum(s[3] * s[4] for s in group) / minutes
            z = zone_of(lat, lng)
            out.append({"date": day, "lat": round(lat, 2), "lng": round(lng, 2), "boats": len(boat_ids),
                        "names": [boats[i]["name"] for i in boat_ids if i < len(boats) and boats[i].get("name")],
                        "minutes": round(minutes), "zone": SHORT.get(ZONES[z][0], ZONES[z][0]) if z is not None else None})
    out.sort(key=lambda h: (h["date"], h["boats"], h["minutes"]), reverse=True)
    return out
