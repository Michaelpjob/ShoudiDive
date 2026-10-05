"""End-to-end: roster -> positions -> trips -> stops -> zones -> answers.

    python model.py [--start --end]   (prints a summary; build_site.py consumes run())
"""
import argparse
import json
import os
import time

import pandas as pd

import analysis
import stops as stops_mod
import tracks
import trips as trips_mod
import zones
from config import NEARSHORE_KM

HERE = os.path.dirname(os.path.abspath(__file__))
ROSTER = os.path.join(HERE, "data", "roster.json")


def load_roster():
    with open(ROSTER, encoding="utf-8") as f:
        return json.load(f)


def run(start=None, end=None, roster=None):
    t0 = time.time()
    roster = roster or load_roster()
    boats = roster["boats"]
    mmsis = [b["mmsi"] for b in boats]
    days = tracks.available_days(start, end)
    print(f"{len(boats)} boats, {len(days)} days ({days[0]}..{days[-1]})", flush=True)
    pos = tracks.load_positions(mmsis, start, end)
    print(f"  {len(pos):,} positions loaded in {time.time() - t0:.0f}s", flush=True)
    pos, trips = trips_mod.segment(pos)
    print(f"  {len(trips)} trips", flush=True)
    st = stops_mod.detect(pos)
    print(f"  {len(st)} stops ({(st['kind'] == 'troll').sum()} troll) in {time.time() - t0:.0f}s", flush=True)
    # The answers and the zone scores use offshore stops only: the bait grounds
    # and kelp edge just outside the harbor ring are where every boat starts its
    # day, and counting them makes every landing a "convergence" and a "home zone".
    off = st[~st["nearshore"]] if len(st) else st
    print(f"  {len(off)} offshore stops (>= {NEARSHORE_KM:.0f} km from a dock) used for the answers", flush=True)
    cw, ct, top, conv = zones.score(off)
    rr = analysis.return_rates(off)
    hz = analysis.home_zones(off)
    cs = analysis.convergence_share(off, conv)
    summ = analysis.fleet_summary(rr, hz, cs)
    # per-trip fishing minutes + stop count
    if len(st):
        per_trip = off.groupby(["mmsi", "trip_id"]).agg(fish_min=("minutes", "sum"), n_stops=("stop_id", "size")).reset_index()
        trips = trips.merge(per_trip, on=["mmsi", "trip_id"], how="left")
    else:
        trips["fish_min"] = 0.0
        trips["n_stops"] = 0
    trips["fish_min"] = trips["fish_min"].fillna(0.0)
    trips["n_stops"] = trips["n_stops"].fillna(0).astype(int)
    # coverage of the positions inside the bbox (how much of each boat's time we can see)
    cov = pos.groupby("mmsi").agg(n_pos=("t", "size"), first=("t", "min"), last=("t", "max")).reset_index()
    print(f"  done in {time.time() - t0:.0f}s", flush=True)
    return {"roster": roster, "days": days, "pos": pos, "trips": trips, "stops": st,
            "cell_week": cw, "cell_total": ct, "cell_top": top, "conv": conv,
            "return_rates": rr, "home_zones": hz, "conv_share": cs, "summary": summ, "coverage": cov}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    a = ap.parse_args()
    r = run(a.start, a.end)
    pd.set_option("display.width", 200)
    names = {b["mmsi"]: b["name"] for b in r["roster"]["boats"]}
    t = r["trips"].copy()
    t["name"] = t["mmsi"].map(names)
    print(t[["name", "depart_local_date", "hours", "max_km", "coverage", "n_stops", "fish_min", "depart_dock"]].head(40).to_string())
    print(r["stops"].groupby("kind")["minutes"].describe())
    print("summary:", r["summary"])
    hz = r["home_zones"].copy()
    hz["name"] = hz["mmsi"].map(names)
    print(hz.sort_values("fish_min", ascending=False).head(20).to_string())
    print("convergence events:", len(r["conv"]))
    print(r["conv"].head(15).to_string())
