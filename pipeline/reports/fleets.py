"""Fleet log for the Ocean Report: each landing's boats over the last week.

AIS (public/fleet/data.json and the per-boat tracks in public/fleet/trips/,
both built by fleet-model/) says when a boat left and came back, how far it
ran and where it held a spot. The landings that publish their own counts
(dock_counts.py: Dana Wharf, Davey's Locker) say what a trip caught. The fleet
data's count columns (trips[9:13]) come from a network whose terms forbid
reuse and are never read here.

Spots are named only offshore, as in the fleet tool; time inside the nearshore
band is one total per trip. "cov" is the share of the trip AIS heard: below
about half, departure and return times are where AIS first and last heard the
boat, not the dock times. Distances are straight-line from the landing. A dock
trip with no AIS trip that day is listed as
not heard (most boats under 65 ft carry no AIS, and shore receivers lose a
boat 30-50 nm out).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import hot_bites, regions
from .zones import nm_between

TZ = ZoneInfo("America/Los_Angeles")
DAYS = 7
TRACK_PTS = 80
PLACES_PER_TRIP = 3


def _local(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.strptime(iso, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(TZ)


def _clock(dt: datetime | None) -> str | None:
    return dt.strftime("%I:%M %p").lstrip("0") if dt else None


def _thin(pts: list, n: int = TRACK_PTS) -> list:
    if len(pts) > n:
        step = (len(pts) - 1) / (n - 1)
        pts = [pts[round(i * step)] for i in range(n)]
    return [[round(p[1], 4), round(p[2], 4)] for p in pts]


def _same_landing(a: str | None, b: str | None) -> bool:
    ka, kb = hot_bites._norm(a), hot_bites._norm(b)
    return bool(ka and kb and (ka in kb or kb in ka))


def _places(stops: list) -> tuple[list[dict], int]:
    """Offshore stops merged by place name (longest first), and nearshore minutes."""
    acc: dict[str, dict] = {}
    near = 0.0
    for s in stops:
        if s[8]:
            near += s[4]
            continue
        if any(nm_between((s[2], s[3]), h) <= hot_bites.HARBOR_NM for h in hot_bites.HARBOR_MOUTHS):
            continue
        name = hot_bites.place_name(s[2], s[3])
        p = acc.setdefault(name, {"place": name, "minutes": 0.0, "lat": 0.0, "lng": 0.0})
        p["minutes"] += s[4]
        p["lat"] += s[2] * s[4]
        p["lng"] += s[3] * s[4]
    out = []
    for p in sorted(acc.values(), key=lambda p: -p["minutes"])[:PLACES_PER_TRIP]:
        w = p["minutes"] or 1
        out.append({"place": p["place"], "minutes": round(p["minutes"]), "lat": round(p["lat"] / w, 3), "lng": round(p["lng"] / w, 3)})
    return out, round(near)


def build(fleet: dict | None, track_dir: Path, dock: list[dict] | None, today: date, days: int = DAYS) -> dict:
    fleet = fleet or {}
    boats = fleet.get("boats", [])
    landings = fleet.get("landings", {})
    since, until = (today - timedelta(days=days - 1)).isoformat(), today.isoformat()
    trips = [t for t in fleet.get("trips", []) if since <= t[1] <= until]
    keys = {(t[0], t[13]) for t in trips}
    stops: dict[tuple, list] = defaultdict(list)
    for s in fleet.get("stops", []):
        if (s[0], s[7]) in keys:
            stops[(s[0], s[7])].append(s)

    tracks: dict[int, dict] = {}

    def track_of(mmsi, tid):
        if mmsi not in tracks:
            try:
                data = json.loads((Path(track_dir) / f"{mmsi}.json").read_text(encoding="utf-8"))
                tracks[mmsi] = {r["id"]: r for r in data.get("trips", [])}
            except (OSError, ValueError):
                tracks[mmsi] = {}
        return tracks[mmsi].get(tid)

    by_landing: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    meta: dict[tuple, dict] = {}
    for b in boats:
        meta[(b.get("landing"), b.get("name"))] = {"length_ft": b.get("length_ft"), "usual_trip": b.get("trip_type")}
    for t in trips:
        b = boats[t[0]] if t[0] < len(boats) else {}
        rec = track_of(b.get("mmsi"), t[13]) or {}
        places, near = _places(stops.get((t[0], t[13]), []))
        dep, ret = _local(rec.get("dep")), _local(rec.get("ret"))
        by_landing[b.get("landing")][b.get("name")].append({
            "date": t[1], "ret_date": ret.date().isoformat() if ret else None, "dep": _clock(dep), "ret": _clock(ret),
            "hours": t[2], "max_nm": round(t[3] / 1.852, 1) if t[3] is not None else None, "cov": t[4],
            "places": places, "near_min": near, "heard": True, "track": _thin(rec.get("pts") or []),
        })

    # The landing's own counts: each dock trip joins its boat's AIS trip that day
    # (longest dock trip with the longest AIS trip); the rest are listed unheard.
    for d in dock or []:
        lname = next((n for n in landings if _same_landing(n, d["landing"])), d["landing"])
        names = {hot_bites._norm(n): n for n in by_landing[lname]}
        for dt in sorted([x for x in d.get("trips", []) if since <= x["date"] <= until], key=hot_bites._trip_rank, reverse=True):
            boat = names.get(hot_bites._norm(dt["boat"]), dt["boat"])
            cands = [tr for tr in by_landing[lname].get(boat, []) if tr["heard"] and "catch" not in tr and dt["date"] in (tr["date"], tr["ret_date"])]
            extra = {"trip": dt.get("trip"), "anglers": dt.get("anglers"), "catch": dt.get("catch") or {}}
            if cands:
                max(cands, key=lambda tr: tr["hours"] or 0).update(extra)
            else:
                by_landing[lname][boat].append({"date": dt["date"], "heard": False, "places": [], "near_min": 0, "track": [], **extra})

    port_of = {n: v[2] for n, v in landings.items()}
    for d in dock or []:
        port_of.setdefault(d["landing"], d["port"])
    out = []
    for lname in sorted(set(landings) | set(by_landing)):
        if lname is None:
            continue
        port = port_of.get(lname)
        src = next((d for d in dock or [] if _same_landing(lname, d["landing"])), None)
        boat_rows = []
        for bname, trs in by_landing.get(lname, {}).items():
            trs.sort(key=lambda tr: (tr["date"], tr.get("dep") or ""), reverse=True)
            boat_rows.append({"name": bname, **meta.get((lname, bname), {}), "trips": trs})
        boat_rows.sort(key=lambda b: (-sum(sum((tr.get("catch") or {}).values()) for tr in b["trips"]), -len(b["trips"]), b["name"] or ""))
        all_trips = [tr for b in boat_rows for tr in b["trips"]]
        heard = [tr for tr in all_trips if tr["heard"]]
        catch: dict[str, int] = {}
        for tr in all_trips:
            hot_bites._add(catch, tr.get("catch"))
        pl: dict[str, dict] = defaultdict(lambda: {"minutes": 0, "trips": 0})
        for tr in heard:
            for p in tr["places"]:
                pl[p["place"]]["minutes"] += p["minutes"]
                pl[p["place"]]["trips"] += 1
        top = sorted(({"place": k, **v} for k, v in pl.items()), key=lambda p: (-p["trips"], -p["minutes"]))[:3]
        far = max(heard, key=lambda tr: tr["max_nm"] or 0, default=None)
        far_boat = next((b["name"] for b in boat_rows if far in b["trips"]), None) if far else None
        info = landings.get(lname) or [None, None, port]
        out.append({
            "name": lname, "port": port, "region": regions.region_of_port(port), "lat": info[0], "lng": info[1],
            "counts": {"landing": src["landing"], "url": src["url"]} if src else None,
            "n_boats": len(boat_rows), "n_trips": len(all_trips), "n_heard": len(heard), "catch": catch, "top_places": top,
            "farthest": {"boat": far_boat, "nm": far["max_nm"], "date": far["date"], "place": (far["places"] or [{}])[0].get("place")} if far and far["max_nm"] else None,
            "boats": boat_rows,
        })
    order = {r["id"]: i for i, r in enumerate(regions.REGIONS)}
    out.sort(key=lambda L: (order.get(L["region"], 99), -L["n_trips"], L["name"]))

    summary = []
    for r in regions.REGIONS:
        Ls = [L for L in out if L["region"] == r["id"]]
        heard = [(b["name"], tr) for L in Ls for b in L["boats"] for tr in b["trips"] if tr["heard"]]
        far = max(heard, key=lambda bt: bt[1]["max_nm"] or 0, default=None)
        catch: dict[str, int] = {}
        for L in Ls:
            hot_bites._add(catch, L["catch"])
        pl: dict[str, dict] = defaultdict(lambda: {"minutes": 0, "trips": 0, "lat": 0.0, "lng": 0.0})
        for _, tr in heard:
            for p in tr["places"]:
                acc = pl[p["place"]]
                acc["minutes"] += p["minutes"]
                acc["trips"] += 1
                acc["lat"], acc["lng"] = p["lat"], p["lng"]
        top_places = sorted(({"place": k, **v} for k, v in pl.items()), key=lambda p: (-p["minutes"], -p["trips"]))[:3]
        summary.append({"id": r["id"], "name": r["name"], "box": r["box"], "fleet_only": bool(r.get("fleet_only")), "n_landings": len(Ls), "n_active": sum(1 for L in Ls if L["n_trips"]),
                        "n_trips": sum(L["n_trips"] for L in Ls), "n_heard": len(heard),
                        "n_boats": len({(L["name"], b["name"]) for L in Ls for b in L["boats"] if b["trips"]}),
                        "offshore_min": round(sum(p["minutes"] for _, tr in heard for p in tr["places"])),
                        "catch": catch, "top_places": top_places,
                        "farthest": {"boat": far[0], "nm": far[1]["max_nm"], "date": far[1]["date"]} if far and far[1]["max_nm"] else None})
    return {"days": days, "since": since, "until": until, "live_through": (fleet.get("meta") or {}).get("live_through"),
            "regions": summary, "landings": out}
