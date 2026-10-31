"""Zones, the way San Diego anglers talk about the water, and what each one is
doing: water temperature now and over the week, breaks, color, paddies,
fleet activity against last week and last year, the next three days of wind
and seas, and GPS numbers for the features worth a look.

Nothing here rates a zone. It reports what the data shows and how it moved.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

from . import evidence, fish

# name, group, center (lat, lng), radius nm, the spots anglers name in it
ZONES = [
    ("La Jolla / Point Loma / Imperial Beach", "inshore", (32.74, -117.28), 9, "La Jolla kelp, Point Loma kelp, IB Pipe, Whistler Buoy"),
    ("Del Mar / Oceanside", "inshore", (33.05, -117.33), 11, "Del Mar hardbottom, Carlsbad and Leucadia kelp, Oceanside red buoy"),
    ("Dana Point / San Clemente coast", "inshore", (33.40, -117.66), 8, "Barn kelp, San Onofre, Dana Point"),
    ("Coronado Islands", "mexico", (32.41, -117.26), 6, "North and South Island, Middle Grounds, SKR"),
    ("Catalina Island", "inshore", (33.38, -118.42), 12, "Frontside, backside, Farnsworth Bank, East End"),
    ("San Clemente Island", "inshore", (32.90, -118.50), 14, "Pyramid Head, China Point (check Navy closures)"),
    ("9 Mile Bank / 178 / 182 / 43", "offshore", (32.66, -117.72), 15, "North and South 9 Mile, 178, 182, The Corner, 43"),
    ("267 / 209-312 / off Oceanside and Dana", "offshore", (33.12, -117.82), 14, "267, 209, 312, 181, 4-10 miles off the coast"),
    ("Catalina Channel / 14 Mile / Avalon Bank", "offshore", (33.36, -118.08), 13, "14 Mile Bank, 152, 277, Slide, Avalon Bank"),
    ("Tanner / Cortes / Cherry", "offshore", (32.60, -119.20), 30, "Tanner Bank, Cortes Bank, Cherry Banks"),
    ("South 9 / 226-302 / 425 / 371 / Upper Hidden", "mexico", (32.33, -117.55), 18, "South 9, 226, 302, Coronado Canyon, 425, 371, Upper Hidden"),
    ("Rockpile / Ensenada / Upper 500", "mexico", (31.85, -117.45), 22, "Rockpile, Salsipuedes, Upper 500, 1010 Trench"),
]

SHORT = {
    "La Jolla / Point Loma / Imperial Beach": "La Jolla to IB", "Del Mar / Oceanside": "Del Mar to Oceanside",
    "Dana Point / San Clemente coast": "Dana Point coast", "Coronado Islands": "Coronados", "Catalina Island": "Catalina",
    "San Clemente Island": "San Clemente Is.", "9 Mile Bank / 178 / 182 / 43": "9 Mile / 43",
    "267 / 209-312 / off Oceanside and Dana": "267 / 209-312", "Catalina Channel / 14 Mile / Avalon Bank": "14 Mile / Avalon Bank",
    "Tanner / Cortes / Cherry": "Tanner / Cortes", "South 9 / 226-302 / 425 / 371 / Upper Hidden": "South 9 / 302 / 425",
    "Rockpile / Ensenada / Upper 500": "Rockpile / Upper 500",
}

GROUPS = [("offshore", "Offshore, US waters"), ("mexico", "Mexican waters"), ("inshore", "Inshore and islands")]

# Standing notices that belong to a zone (verified sources only).
ZONE_NOTES = {
    "San Clemente Island": {"text": "Navy range: check the San Clemente Island closure schedule and monitor VHF 16.",
                            "url": "https://www.scisland.org/"},
}
MEXICO_NOTE = {"text": "Mexican waters: a Mexican fishing license for everyone aboard, and passports.", "url": None}


def nm_between(a, b) -> float:
    dy = (a[0] - b[0]) * 111.0
    dx = (a[1] - b[1]) * 111.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy) / 1.852


def zone_of(lat: float, lng: float) -> int | None:
    best = None
    for i, (_, _, c, r, _) in enumerate(ZONES):
        d = nm_between((lat, lng), c)
        if d <= r and (best is None or d / r < best[0]):
            best = (d / r, i)
    return best[1] if best else None


def ddm(lat: float, lng: float) -> str:
    """'33 17 x 117 52' style (whole minutes), the way reports write GPS."""
    def part(v):
        v = abs(v)
        d = int(v)
        m = round((v - d) * 60)
        if m == 60:
            d, m = d + 1, 0
        return f"{d} {m:02d}"
    return f"{part(lat)} x {part(lng)}"


def _cells(g, center, radius_nm):
    """Grid cells inside a zone circle that belong to this zone: where circles
    overlap, a cell counts for the zone whose center it is relatively nearest."""
    own = next((i for i, z in enumerate(ZONES) if tuple(z[2]) == tuple(center)), None)
    lat0, lng0 = center
    r_km = radius_nm * 1.852
    i0, j0 = g.ij(lat0, lng0)
    ri = int(r_km / (g.dlat * 111.0)) + 1
    rj = int(r_km / (g.dlng * 111.0 * math.cos(math.radians(lat0)))) + 1
    for i in range(max(0, i0 - ri), min(g.h, i0 + ri + 1)):
        for j in range(max(0, j0 - rj), min(g.w, j0 + rj + 1)):
            la, lo = g.latlng(i, j)
            if nm_between((la, lo), center) <= radius_nm and (own is None or zone_of(la, lo) == own):
                yield i, j, la, lo


def zone_mean(g, center, radius_nm, transform=None):
    np = g.np
    vals = [float(g.a[i, j]) for i, j, _, _ in _cells(g, center, radius_nm) if np.isfinite(g.a[i, j])]
    if not vals:
        return None, 0.0
    vals = [transform(v) for v in vals] if transform else vals
    n_all = sum(1 for _ in _cells(g, center, radius_nm))
    return sum(vals) / len(vals), len(vals) / max(n_all, 1)


def zone_break(g, grads, center, radius_nm):
    """Strongest break inside the zone today, as an angler reads it."""
    np = g.np
    sm, mag, gx, gy = grads
    best = None
    for i, j, la, lo in _cells(g, center, radius_nm):
        m = mag[i, j]
        if np.isfinite(m) and m >= 0.06 and (best is None or m > best[0]):
            best = (m, la, lo)
    if not best:
        return None
    b = fish.nearest_break(g, sm, mag, gx, gy, best[1], best[2], radius_km=3.0)
    if b:
        b["gps"] = ddm(b["lat"], b["lng"])
    return b


def zone_break_strength(g, grads, center, radius_nm) -> float | None:
    np = g.np
    _, mag, _, _ = grads
    vals = [float(mag[i, j]) for i, j, _, _ in _cells(g, center, radius_nm) if np.isfinite(mag[i, j])]
    return round(max(vals), 3) if vals else None


def paddy_in_zone(paddies, center, radius_nm, offset):
    """Tightest paddy region touching the zone, and the core's GPS."""
    if not paddies:
        return None
    frame = next((f for f in paddies.get("frames", []) if f.get("offset_days") == offset), None)
    if not frame:
        return None
    best = None
    for region in frame.get("hdr", {}).get("regions", []):
        for feat in region["fc"]["features"]:
            geom = feat["geometry"]
            polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            for poly in polys:
                ring = poly[0]
                clat = sum(p[1] for p in ring) / len(ring)
                clng = sum(p[0] for p in ring) / len(ring)
                inside = nm_between((clat, clng), center) <= radius_nm or any(
                    nm_between((p[1], p[0]), center) <= radius_nm for p in ring[:: max(1, len(ring) // 24)])
                if inside and (best is None or region["level"] < best[0]):
                    best = (region["level"], clat, clng)
    if not best:
        return None
    word = "core" if best[0] <= 0.3 else "likely" if best[0] <= 0.5 else "possible"
    return {"level": best[0], "word": word, "gps": ddm(best[1], best[2]), "lat": round(best[1], 3), "lng": round(best[2], 3)}


def fleet_by_zone(fleet: dict | None, today: date) -> dict[int, dict]:
    """Per zone: daily stopped hours over 14 days, last 3 days vs the 7 before,
    and the same 4-week window last year."""
    out: dict[int, dict] = {}
    if not fleet:
        return out
    boats = fleet.get("boats", [])
    d14 = today - timedelta(days=13)
    ly0, ly1 = today.replace(year=today.year - 1) - timedelta(days=14), today.replace(year=today.year - 1) + timedelta(days=14)
    daily: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    names: dict[int, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    last_year: dict[int, dict] = defaultdict(lambda: {"hours": 0.0, "trips": set()})
    for st in fleet.get("stops", []):
        bidx, day, lat, lng, minutes = st[0], st[1], st[2], st[3], st[4]
        if st[-1]:  # nearshore band (bait grounds, harbor approaches)
            continue
        z = zone_of(lat, lng)
        if z is None:
            continue
        d = date.fromisoformat(day)
        if d >= d14:
            daily[z][day] += minutes / 60
            names[z][day].add(boats[bidx]["name"] if bidx < len(boats) else str(bidx))
        if ly0 <= d <= ly1:
            last_year[z]["hours"] += minutes / 60
            last_year[z]["trips"].add(st[7] if len(st) > 7 else (bidx, day))
    for z in range(len(ZONES)):
        series = [{"date": (d14 + timedelta(days=k)).isoformat(),
                   "hours": round(daily[z].get((d14 + timedelta(days=k)).isoformat(), 0.0), 1),
                   "boats": len(names[z].get((d14 + timedelta(days=k)).isoformat(), ()))} for k in range(14)]
        last3 = sum(s["hours"] for s in series[-3:])
        prior7 = sum(s["hours"] for s in series[-10:-3])
        recent_boats = sorted({b for s in series[-3:] for b in names[z].get(s["date"], ())})
        out[z] = {"series": series, "last3_hours": round(last3, 1), "prior7_hours": round(prior7, 1),
                  "boats_last3": recent_boats,
                  "last_year_hours": round(last_year[z]["hours"], 1), "last_year_trips": len(last_year[z]["trips"])}
    return out


def outlook(center, days: list[date], manifest: dict) -> list[dict]:
    """Daytime (6 AM-6 PM) wind and seas maxima from our hourly frames."""
    out = []
    for d in days:
        rows = evidence.hourly_point(center[0], center[1], d, range(6, 19, 2), manifest)
        winds = [r for r in rows if r.get("wind_kt") is not None]
        waves = [r for r in rows if r.get("hs_ft") is not None]
        if not winds and not waves:
            continue
        top = max(winds, key=lambda r: r["wind_kt"]) if winds else None
        out.append({"date": d.isoformat(), "label": f"{d:%a}",
                    "wind_max_kt": top["wind_kt"] if top else None,
                    "wind_dir": evidence.compass(top.get("wind_from_deg")) if top else None,
                    "hs_max_ft": max(r["hs_ft"] for r in waves) if waves else None,
                    "tp_s": max(waves, key=lambda r: r["hs_ft"])["tp_s"] if waves else None})
    return out


def build(today: date, sst_hist, chl, paddies, paddy_off, fleet, manifest, outlook_days: list[date]) -> list[dict]:
    grads_now = fish.sst_gradient(sst_hist[-1][1]) if sst_hist else None
    grads_hist = [(d, g, fish.sst_gradient(g)) for d, g in sst_hist]
    fleet_z = fleet_by_zone(fleet, today)
    cards = []
    for idx, (name, group, center, radius, spots) in enumerate(ZONES):
        card = {"id": idx, "name": name, "short": SHORT.get(name, name), "group": group, "center": list(center), "radius_nm": radius, "spots": spots,
                "nm_from_sd": round(nm_between(center, fish.SAN_DIEGO)),
                "notice": ZONE_NOTES.get(name) or (MEXICO_NOTE if group == "mexico" else None)}
        # Water temperature: today and the 7-day line
        series = []
        for d, g, _ in grads_hist:
            m, cov = zone_mean(g, center, radius, lambda c: c * 9 / 5 + 32)
            series.append({"date": d, "temp_f": round(m, 1) if m is not None else None, "coverage": round(cov, 2)})
        card["temp_series"] = series
        valid = [s for s in series if s["temp_f"] is not None]
        if valid:
            card["temp_f"] = valid[-1]["temp_f"]
            card["temp_change_f"] = round(valid[-1]["temp_f"] - valid[0]["temp_f"], 1) if len(valid) > 1 else None
            card["temp_days"] = (len(valid) - 1)
        # Breaks: today's strongest, and the zone's strongest gradient each day
        if sst_hist and grads_now:
            card["break"] = zone_break(sst_hist[-1][1], grads_now, center, radius)
            card["break_series"] = [{"date": d, "c_per_km": zone_break_strength(g, gr, center, radius)} for d, g, gr in grads_hist]
        # Color
        if chl:
            m, cov = zone_mean(chl, center, radius)
            card["chl"] = round(m, 2) if m is not None else None
            card["chl_seen"] = round(cov, 2)
        # Paddies: today, and the drift model's days around it
        card["paddy"] = paddy_in_zone(paddies, center, radius, paddy_off)
        card["paddy_series"] = [{"offset": o, "word": (paddy_in_zone(paddies, center, radius, paddy_off + o) or {}).get("word")}
                                for o in (-3, -2, -1, 0, 1, 2)]
        card["fleet"] = fleet_z.get(idx)
        card["outlook"] = outlook(center, outlook_days, manifest) if manifest else []
        # Which offshore species' water this is (water fits, not fish present)
        if card.get("temp_f") is not None and group != "inshore":
            card["water_fits"] = [p["label"].split(" (")[0] for p in fish.PELAGICS.values()
                                  if p["bite"][0] <= card["temp_f"] <= p["bite"][1] and p["season"].get(today.month, 0) >= 0.3]
        cards.append(card)
    return cards


def snapshot(cards: list[dict]) -> dict:
    """The per-zone numbers we archive daily to grow the trends."""
    return {"zones": {c["name"]: {"temp_f": c.get("temp_f"), "chl": c.get("chl"),
                                   "break_c_per_km": (c.get("break") or {}).get("grad_c_per_km"),
                                   "paddy": (c.get("paddy") or {}).get("word"),
                                   "fleet_hours_last3": (c.get("fleet") or {}).get("last3_hours")} for c in cards}}


def map_layers(paddies, offset: int, fleet: dict | None, today: date, manifest: dict) -> dict:
    """What the report map draws besides zones: today's paddy core and likely
    regions (simplified), the last three days of offshore fleet stops, the SST
    grid's bounds and range for the client-side overlay."""
    polys = []
    frame = next((f for f in (paddies or {}).get("frames", []) if f.get("offset_days") == offset), None)
    for region in (frame or {}).get("hdr", {}).get("regions", []):
        if region["level"] > 0.5:
            continue
        for feat in region["fc"]["features"]:
            geom = feat["geometry"]
            rings = [p[0] for p in (geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]])]
            for ring in rings:
                step = max(1, len(ring) // 60)
                polys.append({"level": region["level"],
                              "ring": [[round(p[1], 3), round(p[0], 3)] for p in ring[::step]]})
    stops = []
    if fleet:
        since = (today - timedelta(days=3)).isoformat()
        for st in fleet.get("stops", []):
            if st[1] >= since and not st[-1]:
                stops.append({"lat": round(st[2], 3), "lng": round(st[3], 3), "date": st[1], "min": st[4]})
    sst = (manifest.get("layers") or {}).get("sst") or {}
    return {"paddies": polys, "stops": stops, "bbox": manifest.get("bbox"),
            "sst_url": ((sst.get("windows") or {}).get("1d") or {}).get("url"), "sst_range_c": sst.get("range")}
