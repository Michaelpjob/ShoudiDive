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


def run(start=None, end=None, roster=None, base=None):
    """base: path to fleet-model/data/base (export_base.py). When given, the
    archive window comes from the frozen trips/stops there and only the LIVE
    days on disk (after the base's end) are segmented fresh."""
    t0 = time.time()
    roster = roster or load_roster()
    boats = roster["boats"]
    mmsis = [b["mmsi"] for b in boats]
    if base:
        return run_with_base(base, roster, boats, mmsis, end, t0)
    days = tracks.available_days(start, end)
    print(f"{len(boats)} boats, {len(days)} days ({days[0]}..{days[-1]})", flush=True)
    pos = tracks.load_positions(mmsis, start, end)
    print(f"  {len(pos):,} positions loaded in {time.time() - t0:.0f}s", flush=True)
    pos, trips = trips_mod.segment(pos)
    print(f"  {len(trips)} trips", flush=True)
    st = stops_mod.detect(pos)
    print(f"  {len(st)} stops ({(st['kind'] == 'troll').sum()} troll) in {time.time() - t0:.0f}s", flush=True)
    return _finish(roster, days, pos, trips, st, t0)


def run_with_base(base, roster, boats, mmsis, end, t0):
    with open(os.path.join(base, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    b_trips = pd.read_parquet(os.path.join(base, "trips.parquet"))
    b_stops = pd.read_parquet(os.path.join(base, "stops.parquet"))
    b_cov = pd.read_parquet(os.path.join(base, "cov.parquet"))
    live_start = (pd.Timestamp(meta["end"]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    live_days = [os.path.basename(f)[:10] for f in tracks.live_files(live_start, end)]
    print(f"base {meta['start']}..{meta['end']} ({meta['trips']} trips, {meta['stops']} stops) + {len(live_days)} live days", flush=True)
    if live_days:
        pos = tracks.load_positions(mmsis, live_days[0], live_days[-1])
        pos = pos[pos["t"].dt.strftime("%Y-%m-%d").isin(live_days)] if len(pos) else pos
        pos, l_trips = trips_mod.segment(pos)
        if len(l_trips):
            off = int(meta["next_trip_id"])
            l_trips["trip_id"] = l_trips["trip_id"] + off
            pos.loc[pos["trip_id"] >= 0, "trip_id"] += off
        l_stops = stops_mod.detect(pos)
        print(f"  live: {len(pos):,} positions, {len(l_trips)} trips, {len(l_stops)} stops", flush=True)
        trips = pd.concat([b_trips, l_trips], ignore_index=True) if len(l_trips) else b_trips
        st = pd.concat([b_stops, l_stops], ignore_index=True) if len(l_stops) else b_stops
        l_cov = pos.groupby("mmsi").agg(n_pos=("t", "size")).reset_index() if len(pos) else pd.DataFrame(columns=["mmsi", "n_pos"])
        cov = pd.concat([b_cov[["mmsi", "n_pos"]], l_cov], ignore_index=True).groupby("mmsi", as_index=False)["n_pos"].sum()
    else:
        trips, st, cov, pos = b_trips, b_stops, b_cov[["mmsi", "n_pos"]], pd.DataFrame()
    days = list(pd.date_range(meta["start"], meta["end"]).strftime("%Y-%m-%d")) + live_days
    for c in ("start", "end"):
        if c in st.columns:
            st[c] = pd.to_datetime(st[c], utc=True)
    r = _finish(roster, days, pos, trips, st, t0, cov=cov)
    r["live_days"] = live_days
    return r


def _finish(roster, days, pos, trips, st, t0, cov=None):
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
    trips = trips.drop(columns=[c for c in ("fish_min", "n_stops") if c in trips.columns])
    if len(st):
        per_trip = off.groupby(["mmsi", "trip_id"]).agg(fish_min=("minutes", "sum"), n_stops=("stop_id", "size")).reset_index()
        trips = trips.merge(per_trip, on=["mmsi", "trip_id"], how="left")
    else:
        trips["fish_min"] = 0.0
        trips["n_stops"] = 0
    trips["fish_min"] = trips["fish_min"].fillna(0.0)
    trips["n_stops"] = trips["n_stops"].fillna(0).astype(int)
    # coverage of the positions inside the bbox (how much of each boat's time we can see)
    if cov is None:
        cov = pos.groupby("mmsi").agg(n_pos=("t", "size")).reset_index()
    print(f"  done in {time.time() - t0:.0f}s", flush=True)
    return {"roster": roster, "days": days, "pos": pos, "trips": trips, "stops": st,
            "cell_week": cw, "cell_total": ct, "cell_top": top, "conv": conv,
            "return_rates": rr, "home_zones": hz, "conv_share": cs, "summary": summ, "coverage": cov}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--base")
    a = ap.parse_args()
    r = run(a.start, a.end, base=a.base)
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
