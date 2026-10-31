"""Where the fleet stopped on fish, from AIS and the landings' own dock counts,
for the Ocean Report's Hot Bites.

A sport boat that stops and holds a spot is usually fishing it; several boats
stopping within a mile of each other the same day is the stronger sign. AIS
says where and how long, never what was caught. Where a landing publishes its
own counts (dock_counts.py), a boat's trip is joined to its longest offshore
stop that day, which names the fish; the count is the whole trip's, and the
spot is where AIS heard the boat sit longest (it can miss stops beyond shore
range). Offshore stops only: the nearshore band is left out, as in the fleet
tool, and so are harbor mouths.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, timedelta

from .fish import SPOTS
from .zones import SHORT, ZONES, nm_between, zone_of

DAYS = 4
CLUSTER_NM = 1.0       # stops this close on the same day are one spot
LONE_MIN = 60          # a single boat with no dock count counts once it held a spot this long
SPOT_NM = 4.0          # a point this close to a named spot takes its name
WEEK = 7
# Harbor mouths: a boat holding here is waiting on bait, a slip or the tide,
# not fishing (the first live run put a 4-hour "spot" at the San Diego Bay
# entrance). Approximate entrance centers; also used to describe a point that
# has no named spot nearby ("6 nm SW of Newport").
HARBOR_NM = 1.0
HARBORS = [
    ("San Diego", (32.678, -117.236)), ("Mission Bay", (32.756, -117.253)), ("Oceanside", (33.204, -117.396)),
    ("Dana Point", (33.456, -117.697)), ("Newport", (33.594, -117.879)), ("LA Harbor", (33.709, -118.250)),
    ("King Harbor", (33.846, -118.400)), ("Marina del Rey", (33.966, -118.462)), ("Ensenada", (31.853, -116.630)),
]
HARBOR_MOUTHS = [h[1] for h in HARBORS]
# Longer trips first: when a boat ran two trips in a day, its offshore stops
# belong to the longer one.
TRIP_RANK = [("multi", 6), ("2 day", 6), ("overnight", 5), ("1.5", 5), ("full", 4), ("ext 3/4", 3.5), ("3/4", 3),
             ("1/2", 2), ("half", 2), ("twilight", 1)]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _trip_rank(t: dict) -> float:
    s = t.get("trip", "")
    return next((r for k, r in TRIP_RANK if k in s), 0)


def _compass8(lat0, lng0, lat1, lng1) -> str:
    dy = lat1 - lat0
    dx = (lng1 - lng0) * math.cos(math.radians((lat0 + lat1) / 2))
    deg = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((deg + 22.5) // 45) % 8]


def place_name(lat: float, lng: float) -> str:
    """The named spot within SPOT_NM, else distance and direction from the nearest harbor."""
    spot = min(SPOTS.items(), key=lambda kv: nm_between((lat, lng), kv[1][:2]))
    if nm_between((lat, lng), spot[1][:2]) <= SPOT_NM:
        name = spot[0]
        return "the " + name if name.isdigit() else name
    hname, (hl, hg) = min(HARBORS, key=lambda h: nm_between((lat, lng), h[1]))
    d = nm_between((lat, lng), (hl, hg))
    return f"{d:.0f} nm {_compass8(hl, hg, lat, lng)} of {hname}"


def _add(acc: dict, catch: dict) -> None:
    for k, n in (catch or {}).items():
        acc[k] = acc.get(k, 0) + n


def fleet_hot_bites(fleet: dict | None, today: date, trips: list[dict] | None = None, days: int = DAYS) -> dict:
    """{"fleet": points, "landed": trips with no AIS position}. A point is
    where one or more boats held a spot: 2+ boats within a mile, one boat for
    LONE_MIN+, or any boat whose dock count joins to it."""
    boats = (fleet or {}).get("boats", [])
    since = (today - timedelta(days=days - 1)).isoformat()
    by_day: dict[str, list] = defaultdict(list)
    for st in (fleet or {}).get("stops", []):
        if st[1] < since or st[1] > today.isoformat() or st[-1]:
            continue
        if any(nm_between((st[2], st[3]), h) <= HARBOR_NM for h in HARBOR_MOUTHS):
            continue
        by_day[st[1]].append(st)
    roster: dict[str, list[int]] = defaultdict(list)
    for i, b in enumerate(boats):
        roster[_norm(b.get("name"))].append(i)
    heard_days = {(st[0], d) for d, ss in by_day.items() for st in ss}

    # Dock trips -> (boat, day); the longest trip that day goes with the offshore stops.
    joined: dict[tuple, dict] = {}
    landed = []
    for t in sorted(trips or [], key=_trip_rank, reverse=True):
        if not (since <= t["date"] <= today.isoformat()):
            continue
        ids = [i for i in roster.get(_norm(t["boat"]), []) if not t.get("port") or boats[i].get("port") in (None, "", t["port"])]
        key = (ids[0], t["date"]) if ids else None
        if key and key in heard_days and key not in joined:
            joined[key] = t
        else:
            landed.append({k: t[k] for k in ("date", "boat", "landing", "port", "trip", "anglers", "catch", "url") if k in t})

    out = []
    for day, stops in sorted(by_day.items()):
        left = sorted(stops, key=lambda s: -s[4])
        groups = []
        while left:
            seed = left.pop(0)
            group = [seed] + [s for s in left if nm_between((s[2], s[3]), (seed[2], seed[3])) <= CLUSTER_NM]
            left = [s for s in left if s not in group]
            groups.append(group)
        # each joined trip sits with its boat's longest stop that day
        anchor = {}
        for gi, g in enumerate(groups):
            for s in g:
                if (s[0], day) in joined and (s[0] not in anchor or s[4] > anchor[s[0]][1]):
                    anchor[s[0]] = (gi, s[4])
        for gi, g in enumerate(groups):
            ids = sorted({s[0] for s in g})
            minutes = sum(s[4] for s in g)
            with_trip = [i for i in ids if anchor.get(i, (None,))[0] == gi]
            if len(ids) < 2 and minutes < LONE_MIN and not with_trip:
                continue
            lat = sum(s[2] * s[4] for s in g) / minutes
            lng = sum(s[3] * s[4] for s in g) / minutes
            catch: dict[str, int] = {}
            entries = []
            for i in ids:
                b = boats[i] if i < len(boats) else {}
                e = {"name": b.get("name") or "a boat", "landing": b.get("landing"), "port": b.get("port"),
                     "minutes": round(sum(s[4] for s in g if s[0] == i))}
                if i in with_trip:
                    t = joined[(i, day)]
                    e.update({"trip": t.get("trip"), "anglers": t.get("anglers"), "catch": t.get("catch"), "source": t.get("landing"), "url": t.get("url")})
                    _add(catch, t.get("catch"))
                entries.append(e)
            entries.sort(key=lambda e: (-sum((e.get("catch") or {}).values()), -e["minutes"]))
            z = zone_of(lat, lng)
            out.append({"date": day, "lat": round(lat, 2), "lng": round(lng, 2), "minutes": round(minutes), "n_boats": len(ids),
                        "boats": entries, "catch": catch, "place": place_name(lat, lng),
                        "zone": SHORT.get(ZONES[z][0], ZONES[z][0]) if z is not None else None})
    out.sort(key=lambda h: (h["date"], sum(h["catch"].values()), h["n_boats"], h["minutes"]), reverse=True)
    landed.sort(key=lambda t: (t["date"], sum((t.get("catch") or {}).values())), reverse=True)
    return {"fleet": out, "landed": landed}


def dock_week(dock: list[dict], today: date, days: int = WEEK) -> dict:
    """Fish landed in the last `days` days, {species: {port: count}}. Landing
    day totals where published (Dana Wharf), plus its trips on days the totals
    don't cover yet (the latest day appears per boat first)."""
    since = (today - timedelta(days=days - 1)).isoformat()
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for d in dock or []:
        totals = d.get("days") or []
        covered = {r["date"] for r in totals}
        rows = totals + [t for t in d.get("trips") or [] if t["date"] not in covered]
        for r in rows:
            if since <= r["date"] <= today.isoformat():
                for k, n in (r.get("catch") or {}).items():
                    out[k][d["port"]] = out[k].get(d["port"], 0) + n
    return {k: v for k, v in out.items()}


def dock_since(dock: list[dict]) -> dict:
    """The earliest day each port's counts reach back to, so a week-over-week
    comparison only uses ports whose posts cover both weeks."""
    out: dict[str, str] = {}
    for d in dock or []:
        dates = [r["date"] for r in (d.get("days") or []) + (d.get("trips") or [])]
        if dates:
            first = min(dates)
            out[d["port"]] = min(out.get(d["port"], first), first)
    return out
