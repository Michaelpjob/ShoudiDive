"""Build the fleet roster: which MMSIs are the sportfishing boats, by landing.

Two signals, intersected and then reviewed:
  1. Berth discovery from AIS: vessels that spend nights tied up within
     BERTH_KM of a landing dock (01:00-04:00 local, SOG < 0.5 kt), and that
     also make trips (seen > MIN_TRIP_KM from the dock).
  2. The landing fleet lists scraped from the landing websites
     ($FLEET_DATA_DIR/roster_web.json: name + landing + length), matched by
     normalised vessel name, with the berth's port required to agree with the
     fleet list's port (two boats are called ISLANDER: the H&M sportfisher
     and an Island Packers ferry at Ventura).

A boat is ACCEPTED when its AIS name matches a fleet-list row at the same
port, or the fleet list carries its MMSI, or data/roster_overrides.json says
so. Vessels that merely look the part (vessel_type 60/30, length >= 15 m,
sleeping at a landing, going to sea) are listed as CANDIDATES for review, not
accepted: that bucket is full of ferries, whale-watch and dive boats.

    python roster.py [--start YYYY-MM-DD --end YYYY-MM-DD]  -> data/roster.json
"""
import argparse
import json
import os
import re

import duckdb
import pandas as pd

from config import DATA_DIR, LANDINGS, BERTH_KM, MIN_TRIP_KM, LOCAL_TZ
from geo import min_dist_to_points_km
import tracks

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "roster.json")
WEB = os.path.join(DATA_DIR, "roster_web.json")
OVERRIDES = os.path.join(HERE, "data", "roster_overrides.json")

PASSENGER_TYPES = {30, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69}
ROMAN = {"II": "2", "III": "3", "IV": "4"}
PORT_ALIAS = {"NEWPORT": "Newport Beach", "SAN DIEGO / OXNARD": "San Diego"}


def norm(name):
    s = (name if isinstance(name, str) else "").upper()
    s = re.sub(r"^(M/V|MV|F/V|FV)\s+", "", s)
    s = s.replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\b(III|II|IV)\b", lambda m: ROMAN[m.group(0)], s)
    return re.sub(r"\s+", " ", s).strip()


_LANDING_KEYS = {norm(k): k for k in LANDINGS}


def canon_landing(raw):
    """'Channel Islands Sportfishing (CISCO's)' -> 'Channel Islands Sportfishing'."""
    n = norm(raw)
    if n in _LANDING_KEYS:
        return _LANDING_KEYS[n]
    for nk, k in _LANDING_KEYS.items():
        if n.startswith(nk):
            return k
    return raw


def canon_port(web_row):
    L = canon_landing(web_row.get("landing") or "")
    if L in LANDINGS:
        return LANDINGS[L][2]
    p = (web_row.get("port") or "").strip()
    return PORT_ALIAS.get(p.upper(), p) or None


def berth_table(start, end):
    """Per MMSI: nights at a landing berth + which landing, from the full SoCal cut."""
    files = tracks.socal_files(start, end)
    paths = [f.replace(os.sep, "/") for f in files]
    pts = [(v[0], v[1]) for v in LANDINGS.values()]
    lat_min, lat_max = min(p[0] for p in pts) - 0.02, max(p[0] for p in pts) + 0.02
    lon_min, lon_max = min(p[1] for p in pts) - 0.02, max(p[1] for p in pts) + 0.02
    con = duckdb.connect()
    df = con.execute("""
        select mmsi, base_date_time as t, latitude as lat, longitude as lon
        from read_parquet($files)
        where sog < 0.5 and latitude between $lat_min and $lat_max
          and longitude between $lon_min and $lon_max
    """, {"files": paths, "lat_min": lat_min, "lat_max": lat_max,
          "lon_min": lon_min, "lon_max": lon_max}).df()
    con.close()
    cols = ["mmsi", "nights", "landing", "berth_lat", "berth_lon"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    t = pd.to_datetime(df["t"], utc=True).dt.tz_convert(LOCAL_TZ)
    night = ((t.dt.hour >= 1) & (t.dt.hour < 4)).values
    df = df[night].copy()
    df["night"] = t[night].dt.strftime("%Y-%m-%d").values
    d, idx = min_dist_to_points_km(df["lat"].values, df["lon"].values, pts)
    names = list(LANDINGS)
    df["berth_km"] = d
    df["landing"] = [names[i] for i in idx]
    df = df[df["berth_km"] <= BERTH_KM]
    g = df.groupby("mmsi").agg(
        nights=("night", "nunique"),
        landing=("landing", lambda s: s.value_counts().index[0]),
        berth_lat=("lat", "median"),
        berth_lon=("lon", "median"),
    ).reset_index()
    return g


VISIT_KM = 1.5


def port_visits(mmsis, landings, start, end):
    """{(mmsi, landing): min distance (km) the vessel ever got to that landing's dock}."""
    pos = tracks.load_positions(mmsis, start, end)
    out = {}
    if pos.empty:
        return out
    want = {}
    for m, L in zip(mmsis, landings):
        if isinstance(L, str) and L in LANDINGS:
            want.setdefault(int(m), set()).add(L)
    for m, g in pos.groupby("mmsi"):
        for L in want.get(int(m), ()):
            la, lo = LANDINGS[L][0], LANDINGS[L][1]
            from geo import haversine_km
            out[(int(m), L)] = float(haversine_km(g["lat"].values, g["lon"].values, la, lo).min())
    return out


def trip_evidence(mmsis, start, end):
    """Max distance from any landing per MMSI (does it actually go to sea?)."""
    pos = tracks.load_positions(mmsis, start, end)
    if pos.empty:
        return pd.Series(dtype=float)
    pts = [(v[0], v[1]) for v in LANDINGS.values()]
    d, _ = min_dist_to_points_km(pos["lat"].values, pos["lon"].values, pts)
    pos["dist_km"] = d
    return pos.groupby("mmsi")["dist_km"].max()


def load_web():
    if not os.path.exists(WEB):
        return []
    with open(WEB, encoding="utf-8") as f:
        rows = json.load(f).get("boats", [])
    for b in rows:
        b["_landing"] = canon_landing(b.get("landing") or "")
        b["_port"] = canon_port(b)
    return rows


def _blank(mmsi, directory, landing=None):
    return {"mmsi": mmsi, "nights": 0, "landing": landing, "berth_lat": None, "berth_lon": None,
            "vessel_name": directory.loc[mmsi, "vessel_name"], "vessel_type": directory.loc[mmsi, "vessel_type"],
            "length": directory.loc[mmsi, "length"]}


def build(start, end):
    print("berth discovery ...", flush=True)
    berths = berth_table(start, end)
    print(f"  {len(berths)} vessels sleep at a landing berth", flush=True)
    directory = tracks.vessel_directory(start, end).set_index("mmsi")
    web = load_web()
    web_by_name = {}
    for b in web:
        web_by_name.setdefault(norm(b.get("name")), []).append(b)
    web_mmsi = {}
    for b in web:
        try:
            if b.get("mmsi"):
                web_mmsi[int(b["mmsi"])] = b
        except (TypeError, ValueError):
            pass
    overrides = {}
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES, encoding="utf-8") as f:
            overrides = {int(k): v for k, v in json.load(f).items()}

    cands = berths.copy()
    cands["vessel_name"] = cands["mmsi"].map(directory["vessel_name"])
    cands["vessel_type"] = cands["mmsi"].map(directory["vessel_type"])
    cands["length"] = cands["mmsi"].map(directory["length"])
    have = set(int(m) for m in cands["mmsi"])
    extra = []
    for m, b in web_mmsi.items():           # fleet-list MMSIs never seen at a berth (long-range boats out for weeks)
        if m not in have and m in directory.index:
            extra.append(_blank(m, directory, b["_landing"]))
    for m, o in overrides.items():
        if o.get("accept") and m not in have and m in directory.index:
            extra.append(_blank(m, directory, o.get("landing")))
    # Fleet-list names found anywhere in the SoCal directory, even if they never
    # registered a night at a berth (dock coordinates are approximate, and some
    # boats only switch AIS on under way). Accepted below if they visit the port.
    have |= {e["mmsi"] for e in extra}
    for m, nm in directory["vessel_name"].items():
        if m in have:
            continue
        hits = web_by_name.get(norm(nm))
        if hits:
            extra.append(_blank(int(m), directory, hits[0]["_landing"]))
            have.add(int(m))
    if extra:
        cands = pd.concat([cands, pd.DataFrame(extra)], ignore_index=True)
    cands["mmsi"] = cands["mmsi"].astype(int)

    # Does each candidate ever come within VISIT_KM of the dock of the landing it
    # claims (berth or fleet list)? Loaded once for everyone.
    print("port visits ...", flush=True)
    visit_km = port_visits(cands["mmsi"].tolist(), cands["landing"].tolist(), start, end)

    rows = []
    for r in cands.itertuples(index=False):
        m = int(r.mmsi)
        # berth port only counts when the hull actually slept there; directory-only
        # rows carry the fleet list's landing in r.landing, which proves nothing.
        berth_port = LANDINGS.get(r.landing, (0, 0, None))[2] if (isinstance(r.landing, str) and int(r.nights) >= 1) else None
        nname = norm(r.vessel_name)
        o = overrides.get(m, {})
        webrow, how = None, None
        if m in web_mmsi:
            webrow, how = web_mmsi[m], "fleet-list MMSI"
        elif nname in web_by_name:
            same_port = [b for b in web_by_name[nname] if b["_port"] == berth_port]
            visits = [b for b in web_by_name[nname] if visit_km.get((m, b["_landing"]), 1e9) <= VISIT_KM]
            if same_port:
                webrow, how = same_port[0], "name + berth port"
            elif visits:
                webrow, how = visits[0], "name + visits port"
            else:
                webrow, how = web_by_name[nname][0], "name only (never at that port)"
        length = float(r.length) if pd.notna(r.length) else 0.0
        typ_ok = bool(pd.notna(r.vessel_type) and int(r.vessel_type) in PASSENGER_TYPES and length >= 15)
        # A name match with no berth evidence must at least look like a party
        # boat: passenger/fishing type, or a 14-45 m pleasure-coded hull (several
        # sportfishers are keyed as type 37). Tugs, cargo and 60 m yachts named
        # VISION, INDEPENDENCE or GAME CHANGER are not the fleet.
        vt = int(r.vessel_type) if pd.notna(r.vessel_type) else None
        looks = (vt in PASSENGER_TYPES and length <= 45) or (vt in (36, 37) and 14 <= length <= 45)
        if how == "name + visits port" and not looks:
            how = f"name only (type {vt}, {int(length)} m)"
        if o.get("drop"):
            status = "dropped"
        elif o.get("accept"):
            status, how = "accepted", "override"
        elif how in ("fleet-list MMSI", "name + berth port", "name + visits port"):
            status = "accepted"
        elif how or typ_ok:
            status = "candidate"
        else:
            status = "rejected"
        rows.append({"mmsi": m, "status": status, "how": how, "webrow": webrow, "r": r, "o": o, "typ_ok": typ_ok})

    print("trip evidence ...", flush=True)
    maxd = trip_evidence([x["mmsi"] for x in rows if x["status"] in ("accepted", "candidate")], start, end)
    for x in rows:
        x["max_km"] = float(maxd.get(x["mmsi"], float("nan")))
        if x["status"] == "accepted" and not (x["max_km"] >= MIN_TRIP_KM) and not x["o"].get("accept"):
            x["status"] = "candidate"
            x["how"] = (x["how"] or "") + " / never left harbor in window"

    def rec(x):
        r, w, o = x["r"], x["webrow"] or {}, x["o"]
        berth_landing = r.landing if (isinstance(r.landing, str) and r.landing in LANDINGS and int(r.nights) >= 2) else None
        web_landing = w.get("_landing") if w.get("_landing") in LANDINGS else None
        # The three Point Loma landings share one basin (docks ~200 m apart), so
        # the nearest-dock berth cannot tell them apart: when the berth and the
        # fleet list agree on the PORT, the fleet list names the landing.
        if berth_landing and web_landing and LANDINGS[berth_landing][2] == LANDINGS[web_landing][2]:
            berth_landing = web_landing
        landing = o.get("landing") or berth_landing or w.get("_landing") or (r.landing if isinstance(r.landing, str) else None)
        port = LANDINGS.get(landing, (0, 0, None))[2] or w.get("_port")
        typ = r.vessel_type
        tt = w.get("trip_type")
        return {
            "mmsi": x["mmsi"],
            "name": o.get("name") or (w.get("name") or r.vessel_name or "").strip().title(),
            "ais_name": r.vessel_name, "landing": landing, "port": port,
            "berth": [round(float(r.berth_lat), 5), round(float(r.berth_lon), 5)] if pd.notna(r.berth_lat) else None,
            "nights_at_berth": int(r.nights),
            "vessel_type": int(typ) if pd.notna(typ) else None,
            "length_m": int(r.length) if pd.notna(r.length) else None,
            "length_ft": w.get("length_ft"), "capacity": w.get("capacity"),
            "trip_type": ", ".join(tt) if isinstance(tt, list) else tt,
            "max_km_from_dock": round(x["max_km"], 1) if x["max_km"] == x["max_km"] else None,
            "matched_by": x["how"],
        }

    # One hull per fleet-list row: when several MMSIs share a name at a port,
    # keep the one with berth nights, then passenger/fishing type, then days seen.
    def rank(x):
        r = x["r"]
        vt = int(r.vessel_type) if pd.notna(r.vessel_type) else 0
        return (x["how"] in ("fleet-list MMSI", "override"), int(r.nights), vt in PASSENGER_TYPES,
                int(directory.loc[x["mmsi"], "n_days"]))
    best = {}
    for x in rows:
        if x["status"] != "accepted" or not x["webrow"]:
            continue
        key = id(x["webrow"])
        if key not in best or rank(x) > rank(best[key]):
            best[key] = x
    for x in rows:
        if x["status"] == "accepted" and x["webrow"] and best.get(id(x["webrow"])) is not x:
            x["status"] = "candidate"
            x["how"] = (x["how"] or "") + " / another hull with this name ranks higher"
    boats = sorted((rec(x) for x in rows if x["status"] == "accepted"),
                   key=lambda b: (b["port"] or "", b["landing"] or "", b["name"]))
    candidates = sorted((rec(x) for x in rows if x["status"] == "candidate"),
                        key=lambda b: (-(b["max_km_from_dock"] or 0)))
    seen = {norm(b["ais_name"]) for b in boats} | {norm(b["name"]) for b in boats}
    unmatched = [{"name": b.get("name"), "landing": b["_landing"], "length_ft": b.get("length_ft"), "trip_type": b.get("trip_type")}
                 for b in web if norm(b.get("name")) not in seen]

    out = {"generated_from": {"start": start, "end": end, "days": len(tracks.available_days(start, end))},
           "boats": boats, "candidates": candidates, "unmatched_fleet_list": unmatched}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"roster: {len(boats)} accepted, {len(candidates)} candidates for review, "
          f"{len(unmatched)} fleet-list names not matched on AIS -> {OUT}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    a = ap.parse_args()
    build(a.start, a.end)
