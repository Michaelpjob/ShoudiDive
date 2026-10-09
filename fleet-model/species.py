"""Species hotspots by time of year, from the landings' dock counts joined to
the boats' AIS trips. Builds public/fleet-species/data.json for the dev-only
Species Hotspots page (kept out of the Fleet Tracks tool on purpose).

    python fleet-model/species.py

Method
------
1. Categories. The landings post counts per boat per day ("12 Dorado, 40
   Yellowtail, 3 Striped Marlin Released"). A trip is POSITIVE for a category
   when any of its names was kept or released (marlin are mostly released).
   Dorado and mahi-mahi are the same fish (dolphinfish) and are one category.

2. Calendar (fleet-wide, every boat that posts a count, on AIS or not): per
   week, how many posted trips caught the category, out of all posted trips.

3. Join count -> AIS trip. A posted count is matched to a tracked trip of the
   same boat (normalised name) from the same PORT (the three Point Loma
   landings share one basin, so the port is the safe key) whose local return
   date is the posting date (or one day either side, penalised), and whose
   length fits the posted trip type (a "2.5 Day" count is not matched to a
   4-hour move). Pairs are assigned greedily, best fit first, one-to-one.

4. Where. A matched positive trip is LOCATED when AIS saw it stop offshore
   (drift/anchor or troll, >= 6 km from a dock, as in Fleet Tracks). Its
   category count is spread over its offshore stops by stop minutes, binned
   into H3 res-6 cells (~3.7 km edge). The denominator for a catch rate is
   every counted trip that stopped in the cell in the same month.

Limits: AIS is shore receivers only, so multi-day trips that fish beyond
~50 nm or in Mexican waters are seen leaving and returning: their count
lands on the stops we can see, or the trip is counted as unlocated.
"""
import ast
import datetime as dt
import json
import math
import os
import re
import sys

import h3
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from config import DATA_DIR, LANDINGS, LOCAL_TZ  # noqa: E402
from roster import norm  # noqa: E402

ROOT = os.path.join(HERE, "..")
OUT_DIR = os.path.join(ROOT, "public", "fleet-species")
RES = 6

CATS = [
    # key, label, names in the counts, colour
    ("dorado", "Dorado (mahi-mahi)", ["Dorado", "Dolphinfish", "Mahi Mahi", "Mahi-Mahi"], "#facc15"),
    ("yellowtail", "Yellowtail", ["Yellowtail", "California Yellowtail"], "#fbbf24"),
    ("bluefin", "Bluefin tuna", ["Bluefin Tuna"], "#60a5fa"),
    ("yellowfin", "Yellowfin tuna", ["Yellowfin Tuna"], "#fde047"),
    ("marlin", "Marlin", ["Striped Marlin", "Blue Marlin", "Black Marlin", "Marlin"], "#a78bfa"),
    ("wahoo", "Wahoo", ["Wahoo"], "#34d399"),
    ("skipjack", "Skipjack tuna", ["Skipjack Tuna"], "#93c5fd"),
    ("seabass", "White seabass", ["White Seabass"], "#e2e8f0"),
    ("mako", "Mako shark", ["Mako Shark"], "#f87171"),
]
NAME2CAT = {n.lower(): k for k, _, names, _ in CATS for n in names}

TYPE_HOURS = [  # (pattern, expected hours); first match wins
    (r"1/2 day", 5), (r"3/4 day", 9), (r"\b6 hour", 6), (r"\b4 hour", 4), (r"\b12 hour", 12),
    (r"full day", 12), (r"overnight", 20), (r"1\.75 day", 40), (r"1\.5 day", 32), (r"2\.5 day", 58),
    (r"3\.5 day", 82), (r"\b2 day", 44), (r"\b3 day", 70), (r"\b4 day", 94), (r"\b5 day", 118),
    (r"\b(\d+) day", None),
]


def expected_hours(tt):
    s = (tt or "").lower()
    for pat, h in TYPE_HOURS:
        m = re.search(pat, s)
        if m:
            return h if h is not None else float(m.group(1)) * 24 - 2
    return None


def port_of(landing):
    v = LANDINGS.get(landing)
    return v[2] if v else None


def cats_of(kept, rel):
    out = {}
    for d in (kept, rel):
        for name, n in d.items():
            k = NAME2CAT.get(name.lower())
            if k and n:
                out[k] = out.get(k, 0) + int(n)
    return out


def nm_between(a, b):
    R = 3440.065
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def main():
    counts = pd.read_parquet(os.path.join(DATA_DIR, "counts.parquet"))
    counts["kept_d"] = counts["kept"].map(ast.literal_eval)
    counts["rel_d"] = counts["released"].map(ast.literal_eval)
    counts["cats"] = [cats_of(k, r) for k, r in zip(counts["kept_d"], counts["rel_d"])]
    counts["port"] = counts["landing"].map(port_of)
    counts["nboat"] = counts["boat"].map(norm)
    counts["exp_h"] = counts["trip_type"].map(expected_hours)
    counts = counts.reset_index(drop=True)
    print(f"{len(counts)} posted counts {counts.date.min()}..{counts.date.max()}, "
          f"{sum(1 for c in counts.cats if c)} with a target species")

    # ---- calendar: every posted trip, fleet-wide, by ISO week -------------
    d = pd.to_datetime(counts["date"])
    counts["week"] = (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    weeks = sorted(counts["week"].unique())
    wi = {w: i for i, w in enumerate(weeks)}
    tot_w = counts.groupby("week").size().reindex(weeks, fill_value=0).tolist()
    cal = {k: [[0, 0] for _ in weeks] for k, *_ in CATS}
    for w, cs in zip(counts["week"], counts["cats"]):
        for k, n in cs.items():
            cal[k][wi[w]][0] += 1
            cal[k][wi[w]][1] += n

    # ---- AIS trips + boat names/ports from the roster ----------------------
    base = os.path.join(HERE, "data", "base")
    trips = pd.read_parquet(os.path.join(base, "trips.parquet"))
    stops = pd.read_parquet(os.path.join(base, "stops.parquet"))
    roster = json.load(open(os.path.join(HERE, "data", "roster.json"), encoding="utf-8"))["boats"]
    names = {b["mmsi"]: b for b in roster}
    trips = trips[trips["mmsi"].isin(names)].copy()
    trips["nboat"] = trips["mmsi"].map(lambda m: norm(names[m]["name"]))
    trips["nboat2"] = trips["mmsi"].map(lambda m: norm(names[m].get("ais_name") or ""))
    trips["port"] = trips["mmsi"].map(lambda m: names[m]["port"])
    trips["ret_local"] = pd.to_datetime(trips["ret"], utc=True).dt.tz_convert(LOCAL_TZ).dt.strftime("%Y-%m-%d")
    trips["dep_local"] = pd.to_datetime(trips["depart"], utc=True).dt.tz_convert(LOCAL_TZ)
    print(f"{len(trips)} tracked trips {trips.dep_local.min():%Y-%m-%d}..{trips.dep_local.max():%Y-%m-%d}")

    # ---- greedy one-to-one match, best fit first ---------------------------
    by_key = {}
    for i, r in counts.iterrows():
        by_key.setdefault((r.port, r.nboat), []).append(i)
    pairs = []
    for t in trips.itertuples(index=False):
        rd = dt.date.fromisoformat(t.ret_local)
        for nb in {t.nboat, t.nboat2} - {""}:
            for ci in by_key.get((t.port, nb), []):
                c = counts.iloc[ci]
                dd = abs((dt.date.fromisoformat(c.date) - rd).days)
                if dd > 1:
                    continue
                eh = c.exp_h
                fit = abs(math.log(max(t.hours, 0.5) / eh)) if eh else 0.7
                if eh and fit > math.log(3):
                    continue
                pairs.append((fit + 0.6 * dd, t.mmsi, t.trip_id, ci))
    pairs.sort()
    used_t, used_c, match = set(), set(), {}
    for score, m, tid, ci in pairs:
        if (m, tid) in used_t or ci in used_c:
            continue
        used_t.add((m, tid))
        used_c.add(ci)
        match[(m, tid)] = ci
    print(f"matched {len(match)} counts to tracked trips "
          f"({len(match) / max(1, len(trips)):.0%} of trips, {len(match) / len(counts):.0%} of counts)")

    # ---- per-trip offshore stops -> res-6 cells ----------------------------
    off = stops[~stops["nearshore"]].copy()
    # Inside the LA / Long Beach federal breakwater is harbor (bait barges,
    # anchorage), 6+ km from the landings so the ring misses it: 117 summer
    # trips "stopped" there. Not a fishing ground for these species.
    lalb = off["lat"].between(33.705, 33.80) & off["lon"].between(-118.30, -118.08)
    print(f"  dropping {int(lalb.sum())} stops inside the LA/LB breakwater")
    off = off[~lalb]
    off["c6"] = [h3.latlng_to_cell(a, b, RES) for a, b in zip(off["lat"], off["lon"])]
    stops_by_trip = {k: g for k, g in off.groupby(["mmsi", "trip_id"])}
    tmeta = trips.set_index(["mmsi", "trip_id"])

    grid = {k: {} for k, *_ in CATS}   # cat -> cell -> month -> [pos_trips, fish, minutes]
    effort = {}                        # cell -> month -> [counted_trips, minutes]
    unloc = {k: {} for k, *_ in CATS}  # cat -> month -> n matched-but-unlocated
    trip_recs, exits = [], []
    for (m, tid), ci in match.items():
        c = counts.iloc[ci]
        t = tmeta.loc[(m, tid)]
        month = t.dep_local.strftime("%Y-%m")
        g = stops_by_trip.get((m, tid))
        if g is None or g["minutes"].sum() <= 0:
            for k in c.cats:
                unloc[k][month] = unloc[k].get(month, 0) + 1
            if c.cats and pd.notna(t.far_lat):
                exits.append({"m": int(m), "id": int(tid), "b": names[m]["name"], "l": c.landing,
                              "d": t.dep_local.strftime("%Y-%m-%dT%H:%M"), "h": round(float(t.hours), 1),
                              "nm": round(float(t.max_km) / 1.852, 1), "cov": round(float(t.coverage), 2),
                              "tt": c.trip_type, "a": int(c.anglers), "sp": c.cats,
                              "far": [round(float(t.far_lat), 4), round(float(t.far_lon), 4)]})
            continue
        mins = g.groupby("c6")["minutes"].sum()
        tot = float(mins.sum())
        for cell, mn in mins.items():
            e = effort.setdefault(cell, {}).setdefault(month, [0, 0])
            e[0] += 1
            e[1] += round(float(mn))
        for k, n in c.cats.items():
            for cell, mn in mins.items():
                v = grid[k].setdefault(cell, {}).setdefault(month, [0, 0, 0])
                v[0] += 1
                v[1] += round(n * float(mn) / tot, 2)
                v[2] += round(float(mn))
        if c.cats:
            trip_recs.append({
                "m": int(m), "id": int(tid), "b": names[m]["name"], "l": c.landing,
                "d": t.dep_local.strftime("%Y-%m-%dT%H:%M"), "h": round(float(t.hours), 1),
                "nm": round(float(t.max_km) / 1.852, 1), "cov": round(float(t.coverage), 2),
                "tt": c.trip_type, "a": int(c.anglers), "sp": c.cats,
                "cells": sorted(mins.index.tolist(), key=lambda x: -mins[x]),
            })

    # ---- cell geometry + nearest named structure ---------------------------
    feats = json.load(open(os.path.join(ROOT, "public", "data", "bathy-features.geojson"), encoding="utf-8"))["features"]
    feats = [(f["properties"].get("shortName") or f["properties"]["name"], f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
             for f in feats if 30.5 <= f["geometry"]["coordinates"][1] <= 35 and -122 <= f["geometry"]["coordinates"][0] <= -116.5]
    # plus the chart gazetteer the reports use (numbered spots, banks, island ends)
    import fish_reports
    feats += [(f"the {n}", ll[0], ll[1]) for n, ll in fish_reports.SPOTS.items()]
    feats += [(name, ll[0], ll[1]) for name, rx, ll, approx in fish_reports.NAMED if ll]
    cells_used = set(effort) | {c for k in grid for c in grid[k]}
    cells = {}
    for cell in cells_used:
        la, lo = h3.cell_to_latlng(cell)
        best = min(feats, key=lambda f: nm_between((la, lo), (f[1], f[2]))) if feats else None
        dist = nm_between((la, lo), (best[1], best[2])) if best else None
        cells[cell] = {"b": [[round(a, 4), round(b, 4)] for a, b in h3.cell_to_boundary(cell)],
                       "c": [round(la, 4), round(lo, 4)],
                       "near": [best[0], round(dist, 1)] if best and dist <= 12 else None}

    # ---- the landings' written reports: species x named spot x month --------
    rspots, rlist, rmentions, rwin = {}, [], {k: 0 for k, *_ in CATS}, None
    rep_path = os.path.join(DATA_DIR, "fish_reports.json")
    n_reports = 0
    if os.path.exists(rep_path):
        reps = json.load(open(rep_path, encoding="utf-8"))["reports"]
        n_reports = len(reps)
        for r in reps:
            for k in r["species"]:
                rmentions[k] += 1
            located = {x["name"]: x for x in r["spots"] if x.get("ll")}
            if not located:
                continue
            mo = r["date"][:7]
            pairs = [tuple(p) for p in r.get("pairs", [])]      # (species, spot) in one sentence
            for name, x in located.items():
                e = rspots.setdefault(name, {"ll": [round(x["ll"][0], 4), round(x["ll"][1], 4)], "approx": bool(x["approx"]), "tot": {}, "sp": {}})
                e["tot"][mo] = e["tot"].get(mo, 0) + 1
            for k, name in pairs:
                if name in located:
                    d = rspots[name]["sp"].setdefault(k, {})
                    d[mo] = d.get(mo, 0) + 1
            if pairs:
                sp_here = {}
                for k, name in pairs:
                    sp_here.setdefault(k, []).append(name)
                rlist.append({"d": r["date"], "a": r["author"], "l": r["landing"], "t": (r["title"] or "")[:100],
                              "u": r["url"], "sp": {k: r["species"].get(k, 0) for k in sp_here},
                              "at": sp_here, "s": sorted(located)})
        rwin = [min(r["date"] for r in reps), max(r["date"] for r in reps)] if reps else None
        print(f"reports: {n_reports} SoCal reports {rwin}, {len(rlist)} tie a target species to a charted spot in one sentence, {len(rspots)} spots")

    months = sorted({mo for k in grid for c in grid[k] for mo in grid[k][c]} | {mo for c in effort for mo in effort[c]}
                    | {mo for e in rspots.values() for mo in e["tot"]})
    summary = {}
    for k, label, _, _ in CATS:
        pos_rows = sum(1 for cs in counts["cats"] if k in cs)
        fish = sum(cs.get(k, 0) for cs in counts["cats"])
        located = sum(1 for r in trip_recs if k in r["sp"])
        summary[k] = {"posted_trips": pos_rows, "fish": fish, "located_trips": located,
                      "unlocated_trips": sum(unloc[k].values()), "report_mentions": rmentions[k],
                      "report_located": sum(1 for r in rlist if k in r["sp"])}
        print(f"  {label:<20} posted trips {pos_rows:>5}  fish {fish:>6}  located {located:>4}  unlocated {summary[k]['unlocated_trips']:>4}")

    os.makedirs(OUT_DIR, exist_ok=True)
    data = {
        "meta": {"built_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "h3_res": RES,
                 "counts_window": [counts.date.min(), counts.date.max()],
                 "ais_window": [trips.dep_local.min().strftime("%Y-%m-%d"), trips.dep_local.max().strftime("%Y-%m-%d")],
                 "n_counts": int(len(counts)), "n_trips": int(len(trips)), "n_matched": len(match), "n_reports": n_reports, "reports_window": rwin,
                 "landings": sorted(counts["landing"].unique().tolist()),
                 "landing_ll": {k: [v[0], v[1]] for k, v in LANDINGS.items()}},
        "cats": [{"key": k, "label": l, "names": n, "color": col} for k, l, n, col in CATS],
        "summary": summary,
        "weeks": weeks, "week_total": tot_w, "calendar": cal,
        "months": months, "cells": cells, "grid": grid, "effort": effort, "unlocated": unloc,
        "trips": trip_recs, "exits": exits, "rspots": rspots, "reports": rlist,
    }
    out = os.path.join(OUT_DIR, "data.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), allow_nan=False)
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.2f} MB): {len(cells)} cells, {len(months)} months, {len(trip_recs)} positive located trips")


if __name__ == "__main__":
    main()
