"""The fishing picture: conditions against each species' profile, and where the
fleet is working. It says where a profile is met, never how many fish there
are; no dock counts are used (sportfishingreport.com's terms forbid it).

Inputs, all ours: the MUR SST and blended chlorophyll grids, today's kelp-paddy
frame, Fleet Tracks' AIS stops, the live fleet endpoint. Species bands and
seasons come from Projects/ShouldIDive/fish-reports-research.md (section 3),
the same evidence base as Pelagic Lab's profiles.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

from . import sources

# Named spots: BD Outdoors chart coordinates (via Pelagic Lab's gazetteer) for
# the numbered spots and banks San Diego boats fish, plus the inshore grounds.
SPOTS = {
    "La Jolla kelp": (32.854, -117.275, "kelp"), "Point Loma kelp": (32.685, -117.27, "kelp"),
    "Coronado Islands": (32.401, -117.26, "island"), "South 9 Mile Bank": (32.5333, -117.35, "bank"),
    "North 9 Mile Bank": (32.6333, -117.4333, "bank"), "178": (32.7, -117.4886, "bank"),
    "439": (32.4972, -117.4, "bank"), "425": (32.25, -117.3867, "bank"), "302": (32.4472, -117.5833, "bank"),
    "371": (32.27, -117.5467, "bank"), "226": (32.5055, -117.6333, "bank"), "230": (32.3569, -117.707, "bank"),
    "182": (32.6972, -117.7139, "bank"), "The Corner": (32.6267, -117.8233, "bank"), "43": (32.6555, -117.9736, "bank"),
    "San Salvador Knoll": (32.3167, -117.9, "bank"), "181": (32.9139, -117.8833, "bank"), "312": (33.0236, -117.8083, "bank"),
    "209": (33.1056, -117.8722, "bank"), "267": (33.2967, -117.8192, "bank"), "289": (32.9333, -118.0972, "bank"),
    "14 Mile Bank": (33.4, -117.9972, "bank"), "60 Mile Bank": (32.0667, -118.2472, "bank"),
    "Mackerel Bank": (33.0367, -118.3933, "bank"), "San Clemente Island": (32.92, -118.483, "island"),
    "Catalina": (33.389, -118.45, "island"), "Avalon Bank": (33.4117, -118.225, "bank"),
    "Tanner Bank": (32.7, -119.1333, "bank"), "Cortes Bank": (32.4403, -119.1305, "bank"),
    "Hidden Bank": (31.9, -117.5, "bank"), "Upper 500": (31.7667, -117.5, "bank"),
}
SAN_DIEGO = (32.72, -117.23)

# Pelagic profiles: (bite band F lo, hi), (tolerated F lo, hi), season month -> weight,
# locating cues, chlorophyll ceiling for "blue water". Sources in the research doc.
PELAGICS = {
    "bluefin": {"label": "Bluefin tuna", "bite": (63, 72), "tol": (59, 77),
                "season": {m: (1.0 if 6 <= m <= 11 else 0.4) for m in range(1, 13)},
                "cues": ["break", "bank", "blue"], "blue_chl": 0.4,
                "note": "Sport-fleet catch peaks at 63-70°F (Runcie 2019); in warmer surface water bluefin go deep, out of reach of surface gear."},
    "yellowfin": {"label": "Yellowfin tuna", "bite": (68, 80), "tol": (64, 84),
                  "season": {7: 0.6, 8: 1.0, 9: 1.0, 10: 1.0, 11: 0.6},
                  "cues": ["break", "paddy", "blue", "bank"], "blue_chl": 0.3,
                  "note": "Holds on the warm, clear side of fronts (Block 1997); a shallow mixed layer pushes it up."},
    "dorado": {"label": "Dorado (mahi)", "bite": (70, 82), "tol": (66, 86),
               "season": {7: 0.8, 8: 1.0, 9: 1.0, 10: 0.8, 11: 0.3},
               "cues": ["paddy", "blue", "break"], "blue_chl": 0.25,
               "note": "The paddy fish: 75% of Mexican Pacific catch at chlorophyll 0.05-0.25 mg/m³ (Marín-Enríquez 2018)."},
    "yellowtail": {"label": "Yellowtail (offshore, paddy fish)", "bite": (64, 70), "tol": (60, 75),
                   "season": {6: 1.0, 7: 1.0, 8: 1.0, 9: 1.0, 10: 0.8, 11: 0.4},
                   "cues": ["paddy", "bank", "break"], "blue_chl": 0.5,
                   "note": "San Diego catch rates rise at 64-68°F and fall above 69°F (Squire 1982; inshore fleet data)."},
    "marlin": {"label": "Striped marlin", "bite": (68, 78), "tol": (61, 80),
               "season": {7: 0.4, 8: 0.9, 9: 1.0, 10: 0.9, 11: 0.4},
               "cues": ["break", "blue", "bank"], "blue_chl": 0.3,
               "note": "San Diego catch averaged 67.8°F; works breaks and blue water against green over the banks."},
    "wahoo": {"label": "Wahoo", "bite": (72, 84), "tol": (69, 86),
              "season": {8: 0.5, 9: 1.0, 10: 1.0, 11: 0.5},
              "cues": ["bank", "paddy", "blue"], "blue_chl": 0.3,
              "note": "A warm-year visitor (CPFV: 42 in 2014, 256 in 2015); holds on banks and high spots."},
}


def _f(c: float) -> float:
    return c * 9 / 5 + 32


class Grid:
    """A layer PNG decoded onto the region bbox (row 0 = north, cell centres)."""

    def __init__(self, arr, bbox):
        import numpy as np
        self.a = arr
        self.h, self.w = arr.shape
        self.lng0, self.lat0, self.lng1, self.lat1 = bbox
        self.dlat = (self.lat1 - self.lat0) / self.h
        self.dlng = (self.lng1 - self.lng0) / self.w
        self.np = np

    def ij(self, lat, lng):
        return int((self.lat1 - lat) / self.dlat), int((lng - self.lng0) / self.dlng)

    def latlng(self, i, j):
        return self.lat1 - (i + 0.5) * self.dlat, self.lng0 + (j + 0.5) * self.dlng

    def value(self, lat, lng, ring=2):
        i0, j0 = self.ij(lat, lng)
        best = None
        for r in range(ring + 1):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    i, j = i0 + di, j0 + dj
                    if 0 <= i < self.h and 0 <= j < self.w and self.np.isfinite(self.a[i, j]):
                        d = di * di + dj * dj
                        if best is None or d < best[0]:
                            best = (d, float(self.a[i, j]))
            if best:
                return best[1]
        return None


def load_grid(layer: str, manifest: dict, data_dir: Path | None = None) -> Grid | None:
    from lib.decode import decode_linear_png, decode_log10_png
    data_dir = data_dir or sources.DATA
    L = (manifest.get("layers") or {}).get(layer) or {}
    try:
        url = L["windows"]["1d"]["url"]
    except KeyError:
        return None
    path = data_dir / Path(url).name
    if not path.exists():
        return None
    lo, hi = L["range"]
    arr = decode_log10_png(path, lo, hi) if L.get("scale") == "log10" else decode_linear_png(path, lo, hi)
    return Grid(arr, manifest["bbox"])


def sst_gradient(g: Grid):
    """Smoothed SST and its gradient (degC per km), NaN-aware."""
    np = g.np
    a = g.a.astype("float64")
    valid = np.isfinite(a)
    filled = np.where(valid, a, 0.0)
    k = np.array([1, 2, 1], dtype="float64")
    def blur(x):
        x = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, x)
        return np.apply_along_axis(lambda c: np.convolve(c, k, mode="same"), 0, x)
    num, den = blur(filled), blur(valid.astype("float64"))
    sm = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    sm[~valid] = np.nan
    mid = (g.lat0 + g.lat1) / 2
    gy, gx = np.gradient(sm, g.dlat * 111.0, g.dlng * 111.0 * math.cos(math.radians(mid)))
    return sm, np.hypot(gx, gy), gx, -gy  # -gy: row 0 is north, so northward is -row


BEARINGS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _bearing(lat0, lng0, lat1, lng1) -> tuple[float, str]:
    dy = (lat1 - lat0) * 111.0
    dx = (lng1 - lng0) * 111.0 * math.cos(math.radians(lat0))
    km = math.hypot(dx, dy)
    deg = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return km, BEARINGS[int(deg / 45 + 0.5) % 8]


def nearest_break(g: Grid, sm, mag, gx, gy, lat, lng, radius_km=18.0, min_grad=0.06) -> dict | None:
    """Strongest temperature gradient within radius of a spot, as an angler's
    break: the step across 8 km and which side is warm."""
    np = g.np
    i0, j0 = g.ij(lat, lng)
    ri = int(radius_km / (g.dlat * 111.0)) + 1
    rj = int(radius_km / (g.dlng * 111.0 * math.cos(math.radians(lat)))) + 1
    best = None
    for i in range(max(0, i0 - ri), min(g.h, i0 + ri + 1)):
        for j in range(max(0, j0 - rj), min(g.w, j0 + rj + 1)):
            m = mag[i, j]
            if not np.isfinite(m) or m < min_grad:
                continue
            la, lo = g.latlng(i, j)
            km, _ = _bearing(lat, lng, la, lo)
            if km <= radius_km and (best is None or m > best[0]):
                best = (m, i, j, la, lo, km)
    if best is None:
        return None
    m, i, j, la, lo, km = best
    ux, uy = gx[i, j] / m, gy[i, j] / m  # unit vector toward warmer water
    def t_at(dkm):
        dla = uy * dkm / 111.0
        dlo = ux * dkm / (111.0 * math.cos(math.radians(la)))
        return g.value(la + dla, lo + dlo, ring=1)
    warm, cold = t_at(4.0), t_at(-4.0)
    if warm is None or cold is None:
        return None
    step_f = (warm - cold) * 9 / 5
    warm_deg = (math.degrees(math.atan2(ux, uy)) + 360) % 360
    _, where = _bearing(lat, lng, la, lo)
    return {"km": round(km, 1), "nm": round(km / 1.852, 1), "direction": where if km > 1.5 else "",
            "step_f": round(step_f, 1), "over_nm": 4.3, "warm_side": BEARINGS[int(warm_deg / 45 + 0.5) % 8],
            "grad_c_per_km": round(float(m), 3), "lat": round(la, 4), "lng": round(lo, 4)}


def _point_in_poly(lat, lng, ring) -> bool:
    inside = False
    n = len(ring)
    for k in range(n):
        x1, y1 = ring[k][0], ring[k][1]
        x2, y2 = ring[(k + 1) % n][0], ring[(k + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lng < x:
                inside = not inside
    return inside


def paddy_level(paddies: dict | None, lat: float, lng: float, offset: int = 0) -> float | None:
    """Tightest paddy-likelihood region (0.3 core, 0.5, 0.8) containing the point
    in the frame for the report's day (offset from the paddy build date)."""
    if not paddies:
        return None
    frame = next((f for f in paddies.get("frames", []) if f.get("offset_days") == offset), None)
    if not frame:
        return None
    hit = None
    for region in frame.get("hdr", {}).get("regions", []):
        for feat in region["fc"]["features"]:
            geom = feat["geometry"]
            polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            if any(_point_in_poly(lat, lng, poly[0]) for poly in polys):
                hit = region["level"] if hit is None else min(hit, region["level"])
    return hit


def paddy_offset(paddies: dict | None, today: date) -> int:
    try:
        built = sources.parse_iso(paddies["model_meta"]["build_utc"]).date()
    except (TypeError, KeyError, AttributeError):
        return 0
    return (today - built).days


def spot_conditions(sst: Grid | None, chl: Grid | None, paddies: dict | None, offset: int = 0) -> list[dict]:
    out = []
    grads = sst_gradient(sst) if sst else None
    for name, (lat, lng, kind) in SPOTS.items():
        c = sst.value(lat, lng) if sst else None
        km, _ = _bearing(SAN_DIEGO[0], SAN_DIEGO[1], lat, lng)
        row = {"name": name, "kind": kind, "lat": lat, "lng": lng, "nm_from_sd": round(km / 1.852),
               "sst_f": round(_f(c), 1) if c is not None else None,
               "chl": round(chl.value(lat, lng), 2) if chl and chl.value(lat, lng) is not None else None,
               "paddy": paddy_level(paddies, lat, lng, offset)}
        if grads:
            row["break"] = nearest_break(sst, *grads, lat, lng)
        out.append(row)
    return out


def species_picture(month: int, spots: list[dict]) -> list[dict]:
    """Each pelagic against its profile: season, which spots sit in its band,
    and which locating cues those spots meet. A cue met at 60%+ of in-band
    spots is 'ambient' that day (it locates nothing) and is not counted."""
    out = []
    for key, p in PELAGICS.items():
        w = p["season"].get(month, 0.0)
        season = "in season" if w >= 0.8 else "edge of season" if w >= 0.3 else "out of season"
        offshore = [s for s in spots if s["kind"] != "kelp"]  # pelagics need offshore water (Pelagic Lab's depth gate)
        in_band = [s for s in offshore if s["sst_f"] is not None and p["bite"][0] <= s["sst_f"] <= p["bite"][1]]
        tolerated = [s for s in offshore if s["sst_f"] is not None and p["tol"][0] <= s["sst_f"] <= p["tol"][1]
                     and s not in in_band]
        def cues(s):
            met = []
            if "break" in p["cues"] and s.get("break") and s["break"]["step_f"] >= 0.5:
                met.append("break")
            if "bank" in p["cues"] and s["kind"] in ("bank", "island"):
                met.append("bank")
            if "blue" in p["cues"] and s.get("chl") is not None and s["chl"] <= p["blue_chl"]:
                met.append("blue")
            if "paddy" in p["cues"] and s.get("paddy") is not None:
                met.append("paddy")
            return met
        cue_map = {s["name"]: cues(s) for s in in_band}
        ambient = [c for c in p["cues"] if in_band and sum(c in v for v in cue_map.values()) >= 0.6 * len(in_band)
                   and len(in_band) >= 5]
        ranked = sorted(in_band, key=lambda s: (-len([c for c in cue_map[s["name"]] if c not in ambient]), s["nm_from_sd"]))
        temps = [s["sst_f"] for s in offshore if s["sst_f"] is not None]
        out.append({
            "key": key, "label": p["label"], "season": season, "season_weight": w, "note": p["note"],
            "bite_band_f": list(p["bite"]), "tolerated_f": list(p["tol"]),
            "spots_in_band": len(in_band), "spots_tolerated": len(tolerated), "spots_checked": len(temps),
            "ambient": ambient,
            "best": [{"name": s["name"], "sst_f": s["sst_f"], "nm_from_sd": s["nm_from_sd"],
                      "cues": [c for c in cue_map[s["name"]] if c not in ambient]} for s in ranked[:5]],
            "status": ("profile met" if in_band and w >= 0.3 else "water right, season wrong" if in_band
                       else "water marginal" if tolerated else "water outside its range"),
        })
    return out


# --- Fleet -------------------------------------------------------------------

def nearest_spot(lat: float, lng: float, max_nm: float = 10.0) -> str | None:
    best = None
    for name, (la, lo, _) in SPOTS.items():
        km, _ = _bearing(lat, lng, la, lo)
        if km / 1.852 <= max_nm and (best is None or km < best[0]):
            best = (km, name)
    return best[1] if best else None


def fleet_recent(fleet: dict | None, today: date, days: int = 3) -> dict | None:
    """Offshore stops (beyond the nearshore band) over the last few days,
    grouped by nearest named spot. A stop says the fleet worked there, not
    that fish were caught there."""
    if not fleet:
        return None
    boats = fleet.get("boats", [])
    since = (today - timedelta(days=days)).isoformat()
    groups: dict[str, dict] = {}
    n = 0
    for st in fleet.get("stops", []):
        bidx, day, lat, lng, minutes = st[0], st[1], st[2], st[3], st[4]
        nearshore = st[-1]
        if day < since or nearshore:
            continue
        n += 1
        spot = nearest_spot(lat, lng) or f"{lat:.2f}N {abs(lng):.2f}W"
        g = groups.setdefault(spot, {"spot": spot, "boats": set(), "minutes": 0, "days": set()})
        g["boats"].add(boats[bidx]["name"] if bidx < len(boats) else str(bidx))
        g["minutes"] += minutes
        g["days"].add(day)
    rows = sorted(({"spot": g["spot"], "boats": sorted(g["boats"]), "hours": round(g["minutes"] / 60, 1),
                    "days": sorted(g["days"])} for g in groups.values()), key=lambda r: -r["hours"])
    meta = fleet.get("meta", {})
    return {"since": since, "stops": n, "spots": rows[:8], "live_through": meta.get("live_through"),
            "coverage_note": "AIS is heard only within roughly 30-50 nm of shore receivers, and most boats under 65 ft never broadcast it."}


def fleet_now(now_json: dict | None, landings: dict | None, now: datetime | None = None, max_age_h: float = 3.0) -> dict | None:
    """Boats at sea right now (not at a landing), from positions under three
    hours old; stopped boats first."""
    import re
    if not now_json:
        return None
    docks = [(v[0], v[1]) for v in (landings or {}).values()]
    now = now or sources.utcnow()
    at_sea = []
    for b in now_json.get("boats", []):
        lat, lng, sog = b.get("lat"), b.get("lon"), b.get("sog")
        t = sources.parse_iso(b.get("t"))
        if lat is None or lng is None or t is None or (now - t).total_seconds() > max_age_h * 3600:
            continue
        b = {**b, "name": re.sub(r"[^A-Za-z0-9 '&.-]", "", b.get("name") or "").strip().title() or None}
        near_dock = any(_bearing(lat, lng, d[0], d[1])[0] < 6 for d in docks)
        if near_dock:
            continue
        at_sea.append({"name": b.get("name") or "unnamed", "sog": sog, "stopped": sog is not None and sog < 2,
                       "spot": nearest_spot(lat, lng, 15), "nm_from_sd": round(_bearing(*SAN_DIEGO, lat, lng)[0] / 1.852),
                       "t": b.get("t")})
    return {"at_sea": sorted(at_sea, key=lambda r: (not r["stopped"], r["nm_from_sd"]))[:12], "fresh": now_json.get("freshest"),
            "max_age_h": max_age_h}


# --- Inshore and island species (the calendar side) --------------------------

def inshore_calendar(today: date, surface_f: float | None, thermocline_ft: float | None) -> list[dict]:
    m = today.month
    def in_months(*ms):
        return m in ms
    rows = [
        {"key": "yellowtail", "label": "Yellowtail (islands and kelp)", "spear": True,
         "status": "in season" if in_months(5, 6, 7, 8, 9, 10) else "big resident fish only",
         "drivers": "Warm-year regime (landings swing from under 10,000 to 450,000 fish), bait: squid, sardine, mackerel.",
         "look": "Outer kelp edge next to deep water, up-current points at the islands.", "evidence": "Strong for the regime; the 64-66°F 'switch' is angler lore."},
        {"key": "wsb", "label": "White seabass", "spear": True,
         "status": "in season" if in_months(3, 4, 5, 6, 7, 8, 9) else "off peak",
         "drivers": "Spawning Mar-Jul (peak May); tagged fish spent half their time at 55-61°F, tracking the thermocline.",
         "look": "Kelp near squid grounds, at dusk; near the surface Apr-Oct, deeper Dec-Feb.", "evidence": "Thermal niche from one tagging study (Aalbers & Sepulveda 2015)."},
        {"key": "calico", "label": "Calico (kelp) bass", "spear": True, "status": "year-round",
         "drivers": "Small home ranges; spawns Jun-Aug at 25-60 ft under kelp.", "look": "Kelp and reef.", "evidence": "Strong."},
        {"key": "halibut", "label": "California halibut", "spear": True,
         "status": "inshore spawning run" if in_months(4, 5) else "deeper water, banks and islands",
         "drivers": "Moves inshore to spawn Apr-May.", "look": "Sand next to structure.", "evidence": "Moderate."},
        {"key": "sheephead", "label": "Sheephead", "spear": True, "status": "year-round",
         "drivers": "Daytime only, site-faithful.", "look": "Reef.", "evidence": "Strong."},
        {"key": "squid", "label": "Market squid (the bait that drives the inshore bite)", "spear": False,
         "status": "spawning season" if m in (10, 11, 12, 1, 2, 3, 4, 5) else "off season",
         "drivers": "Landings crash after big El Niños; eggs at 50-58°F.", "look": "CDFW weekly landings by block.",
         "evidence": "Strong for the El Niño effect: a weak squid year is likely this winter."},
    ]
    if surface_f is not None:
        for r in rows:
            if r["key"] == "wsb":
                r["today"] = (f"Surface water is {surface_f:.0f}°F, above the 55-61°F the tagged fish used"
                              + (f"; the nearest observed thermocline is at about {thermocline_ft:.0f} ft" if thermocline_ft else "")
                              + ", so expect them deep.") if surface_f > 63 else "Surface water is inside their tagged range."
            if r["key"] == "yellowtail":
                r["today"] = (f"Surface water is {surface_f:.0f}°F; San Diego catch rates have fallen above 69°F in fleet data."
                              if surface_f > 69 else f"Surface water is {surface_f:.0f}°F.")
    return rows


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def fleet_now_fetch(now: datetime | None = None) -> dict:
    url = "https://shouldidive.com/api/fleet/now"
    def run():
        d = sources._get(url).json()
        return sources.source("fleet_now", "Fleet Tracks live AIS positions", "https://shouldidive.com/fleet/#view=live",
                              ok=True, time=sources.parse_iso(d.get("freshest")), data=d, now=now)
    return sources._guard("fleet_now", "Fleet Tracks live AIS positions", url, run)
