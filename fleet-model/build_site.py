"""Build the ShouldIDive "Fleet Tracks" tool bundle: public/fleet/data.json.

    python build_site.py [TARGET_DIR] [--start YYYY-MM-DD --end YYYY-MM-DD]

TARGET_DIR defaults to the repo's public/fleet/. The static files (index.html,
app.js, fleet.css, leaflet.*) live in the repo and are not touched here; this
only writes data.json, the same way paddies-model/build_site.py feeds
public/paddies/. CSP-clean: no external hosts, basemap is /data/land.geojson.
"""
import argparse
import datetime as dt
import json
import os
import sys

import pandas as pd

import model
import zones
from config import LANDINGS, H3_RES, HARBOR_KM, NEARSHORE_KM, DRIFT_MAX_KT, DRIFT_MIN_MIN, TROLL_KT, TROLL_MIN_MIN

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TARGET = os.path.join(HERE, "..", "public", "fleet")
COUNTS = os.path.join(model.tracks.DATA_DIR, "counts.parquet")


def _r(x, n=1):
    return None if x is None or (isinstance(x, float) and pd.isna(x)) else round(float(x), n)


def _i(x):
    """int or None; pandas turns a column of ints-with-gaps into floats with NaN."""
    return None if x is None or (isinstance(x, float) and pd.isna(x)) else int(x)


def _s(x):
    return None if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)


def dive_spots(repo_root):
    """Name + bbox-centre of each main-app dive spot in the SoCal field (same
    rule as paddies-model/reference.py), baked in so the CSP-clean bundle
    needs no extra fetch. Structure (banks, seamounts, reefs) is NOT baked:
    the page reads /data/bathy-features.geojson at runtime so it tracks the
    main app's file."""
    import glob
    out = []
    for bj in sorted(glob.glob(os.path.join(repo_root, "public", "data", "spots", "*", "bundle.json"))):
        try:
            with open(bj, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        b = d.get("bbox") or {}
        if not all(k in b for k in ("lat_min", "lat_max", "lng_min", "lng_max")):
            continue
        lat = round((b["lat_min"] + b["lat_max"]) / 2, 4)
        lng = round((b["lng_min"] + b["lng_max"]) / 2, 4)
        if 31.0 <= lat <= 34.8 and -121.5 <= lng <= -116.8:
            out.append({"name": d.get("name") or os.path.basename(os.path.dirname(bj)), "lat": lat, "lng": lng})
    return out


def load_counts():
    if not os.path.exists(COUNTS):
        return None
    c = pd.read_parquet(COUNTS)
    return c


def join_counts(trips, boats, counts):
    """Attach the dock's posted count to each trip by (landing, boat name, return date)."""
    if counts is None or counts.empty:
        trips["anglers"] = None
        trips["fish_kept"] = None
        trips["fish_released"] = None
        trips["count_trip_type"] = None
        return trips, 0
    from roster import norm
    counts = counts.copy()
    counts["nboat"] = counts["boat"].map(norm)
    by_key = {}
    for r in counts.itertuples(index=False):
        by_key.setdefault((r.landing, r.nboat, r.date), []).append(r)
    name_of = {b["mmsi"]: (b["landing"], norm(b["name"]), norm(b.get("ais_name") or "")) for b in boats}
    ang, kept, rel, tt = [], [], [], []
    hits = 0
    for t in trips.itertuples(index=False):
        landing, n1, n2 = name_of.get(t.mmsi, (None, None, None))
        ret_date = pd.Timestamp(t.ret).tz_localize("UTC").tz_convert("America/Los_Angeles").strftime("%Y-%m-%d")
        rows = by_key.get((landing, n1, ret_date)) or by_key.get((landing, n2, ret_date)) or []
        if not rows:
            # the same boat may be listed under a sister landing (H&M / Fisherman's / Point Loma share a basin)
            for L in LANDINGS:
                if LANDINGS[L][2] == LANDINGS.get(landing, (0, 0, None))[2]:
                    rows = by_key.get((L, n1, ret_date)) or by_key.get((L, n2, ret_date)) or []
                    if rows:
                        break
        if rows:
            # pick the posted trip whose duration class best matches (multi-day vs day); else the biggest
            best = max(rows, key=lambda r: r.anglers)
            hits += 1
            ang.append(int(best.anglers)); kept.append(int(best.fish_kept)); rel.append(int(best.fish_released)); tt.append(best.trip_type)
        else:
            ang.append(None); kept.append(None); rel.append(None); tt.append(None)
    trips["anglers"] = ang
    trips["fish_kept"] = kept
    trips["fish_released"] = rel
    trips["count_trip_type"] = tt
    return trips, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", default=DEFAULT_TARGET)
    ap.add_argument("--start")
    ap.add_argument("--end")
    a = ap.parse_args()
    target = os.path.abspath(a.target)
    os.makedirs(target, exist_ok=True)

    r = model.run(a.start, a.end)
    boats = r["roster"]["boats"]
    trips, stops, cw, ct = r["trips"], r["stops"], r["cell_week"], r["cell_total"]
    counts = load_counts()
    trips, count_hits = join_counts(trips, boats, counts)

    weeks = sorted(set(cw["week"])) if len(cw) else []
    widx = {w: i for i, w in enumerate(weeks)}
    bidx = {b["mmsi"]: i for i, b in enumerate(boats)}

    # cells: geometry once, stats per week as compact arrays
    cells = {}
    for c in ct["h3"]:
        cells[c] = {"b": zones.cell_boundary(c), "c": zones.cell_center(c)}
    cell_week = {}
    for row in cw.itertuples(index=False):
        cell_week.setdefault(row.h3, {})[widx[row.week]] = [round(float(row.fish_min)), int(row.trips), int(row.boats),
                                                             int(row.stops), round(float(row.troll_min))]
    cell_total = {row.h3: [round(float(row.fish_min)), int(row.trips), int(row.boats), int(row.stops), round(float(row.troll_min))]
                  for row in ct.itertuples(index=False)}
    cell_top = {c: [[bidx.get(m, -1), v] for m, v in lst] for c, lst in r["cell_top"].items()}

    # per-boat stats
    hz = r["home_zones"].set_index("mmsi") if len(r["home_zones"]) else pd.DataFrame()
    rr = r["return_rates"].groupby("mmsi")[["same_spot", "same_area"]].mean() if len(r["return_rates"]) else pd.DataFrame()
    cs = r["conv_share"].set_index("mmsi") if len(r["conv_share"]) else pd.DataFrame()
    cov = r["coverage"].set_index("mmsi")
    out_boats = []
    for b in boats:
        m = b["mmsi"]
        tb = trips[trips["mmsi"] == m]
        out_boats.append({
            "mmsi": m, "name": b["name"], "landing": b["landing"], "port": b["port"],
            "length_ft": b.get("length_ft") or (round(b["length_m"] * 3.281) if b.get("length_m") else None),
            "trip_type": b.get("trip_type"), "berth": b.get("berth"),
            "trips": int(len(tb)), "fish_min": round(float(tb["fish_min"].sum())),
            "coverage": _r(tb["coverage"].mean(), 2) if len(tb) else None,
            "max_km": _r(tb["max_km"].max()) if len(tb) else None,
            "top_cell": hz.loc[m, "top_cell"] if m in hz.index else None,
            "top_share": _r(hz.loc[m, "top_share"], 2) if m in hz.index else None,
            "cells_for_half": int(hz.loc[m, "cells_for_half"]) if m in hz.index else None,
            "return_same_spot": _r(rr.loc[m, "same_spot"], 2) if m in rr.index else None,
            "return_same_area": _r(rr.loc[m, "same_area"], 2) if m in rr.index else None,
            "convergence_share": _r(cs.loc[m, "convergence_share"], 2) if m in cs.index else None,
            "n_pos": int(cov.loc[m, "n_pos"]) if m in cov.index else 0,
        })

    # trips: compact per-boat lists (local depart date, hours, max_km, coverage, stops, fish_min, counts)
    out_trips = []
    for t in trips.itertuples(index=False):
        out_trips.append([bidx[t.mmsi], t.depart_local_date, _r(t.hours), _r(t.max_km), _r(t.coverage, 2),
                          int(t.n_stops), round(float(t.fish_min)), t.far_lat, t.far_lon,
                          _i(t.anglers), _i(t.fish_kept), _i(t.fish_released), _s(t.count_trip_type), int(t.trip_id)])
    # stops: [boat_idx, local_date, lat, lon, minutes, kind(0 drift/1 troll), h3, trip_id, nearshore(0/1)]
    out_stops = [[bidx[s.mmsi], s.local_date, s.lat, s.lon, round(float(s.minutes)), 1 if s.kind == "troll" else 0, s.h3, int(s.trip_id), 1 if s.nearshore else 0]
                 for s in stops.itertuples(index=False)]
    conv = [{"date": c.local_date, "h3": c.h3, "boats": [bidx.get(m, -1) for m in c.mmsis], "fish_min": round(float(c.fish_min))}
            for c in r["conv"].itertuples(index=False)]

    # catch-per-angler by cell: trips with a posted count, minutes-weighted over the trip's cells
    cell_catch = {}
    if count_hits:
        tc = trips.dropna(subset=["fish_kept"])
        tc = tc[tc["anglers"].fillna(0) > 0]
        st2 = stops[~stops["nearshore"]].merge(tc[["mmsi", "trip_id", "fish_kept", "anglers", "fish_min"]], on=["mmsi", "trip_id"])
        st2 = st2[st2["fish_min"] > 0]
        st2["cpa"] = st2["fish_kept"] / st2["anglers"]
        st2["w"] = st2["minutes"] / st2["fish_min"]
        g = st2.groupby("h3").apply(lambda x: pd.Series({"cpa": (x["cpa"] * x["w"]).sum() / x["w"].sum(), "n": x["trip_id"].nunique()}))
        cell_catch = {c: [_r(v["cpa"], 2), int(v["n"])] for c, v in g.iterrows()}

    data = {
        "meta": {
            "build_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "start": r["days"][0], "end": r["days"][-1], "n_days": len(r["days"]),
            "n_boats": len(boats), "n_trips": int(len(trips)), "n_stops": int(len(stops)),
            "count_joined_trips": int(count_hits),
            "h3_res": H3_RES, "harbor_km": HARBOR_KM, "nearshore_km": NEARSHORE_KM,
            "stop_rule": {"drift_max_kt": DRIFT_MAX_KT, "drift_min_min": DRIFT_MIN_MIN,
                          "troll_kt": list(TROLL_KT), "troll_min_min": TROLL_MIN_MIN},
            "source": "NOAA OCM / USCG NAIS daily AIS (1 position per minute, terrestrial receivers only)",
            "counts_source": "sportfishingreport.com landing dock counts" if count_hits else None,
        },
        "summary": r["summary"],
        "landings": {k: [v[0], v[1], v[2]] for k, v in LANDINGS.items()},
        "reference": {"spots": dive_spots(os.path.join(HERE, ".."))},
        "boats": out_boats,
        "weeks": weeks,
        "cells": cells,
        "cell_week": cell_week,
        "cell_total": cell_total,
        "cell_top": cell_top,
        "cell_catch": cell_catch,
        "trips": out_trips,
        "stops": out_stops,
        "convergence": conv,
    }
    out = os.path.join(target, "data.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), allow_nan=False)  # a NaN would break JSON.parse in the browser
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.2f} MB): {len(boats)} boats, {len(trips)} trips, "
          f"{len(stops)} stops, {len(cells)} cells, {len(weeks)} weeks, {count_hits} trips with counts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
