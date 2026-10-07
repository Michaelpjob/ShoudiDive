"""Per-boat trip PATHS: the full track of every dock-to-dock trip, one JSON
file per boat under public/fleet/trips/<mmsi>.json, loaded lazily by the tool
when a boat is selected ("where has this boat been, and when").

  {"mmsi": 367146720, "name": "Malihini", "next_archive_id": 5557,
   "trips": [{"id": 12, "dep": "2025-06-02T13:05Z", "ret": "2025-06-02T22:41Z",
              "hours": 9.6, "max_nm": 11.2, "open": false,
              "pts": [[epochSec, lat, lon, sogKt], ...]}, ...]}

Points are thinned to one per 3 minutes (5 for multi-day trips) which keeps a
day trip at ~200 points. Archive trips (id < keep_below) already in the file
are preserved untouched when live trips are merged in by the nightly build.
"""
import json
import os

import numpy as np
import pandas as pd


def _thin(g, step_s):
    t = (g["t"].values.astype("datetime64[s]").astype("int64"))
    keep = np.zeros(len(g), dtype=bool)
    last = -10 ** 12
    for i in range(len(g)):
        if t[i] - last >= step_s or i == len(g) - 1:
            keep[i] = True
            last = t[i]
    keep[0] = True
    sub = g[keep]
    sog = sub["sog"].fillna(0).values
    return [[int(a), round(float(b), 5), round(float(c), 5), round(float(d), 1)]
            for a, b, c, d in zip(t[keep], sub["lat"].values, sub["lon"].values, sog)]


def write_trip_paths(pos, trips, out_dir, names, keep_below=None):
    """pos: positions with trip_id (from trips.segment); trips: the trips table.
    names: {mmsi: boat name}. keep_below: archive trip ids to preserve from
    the existing file (None = rewrite everything)."""
    os.makedirs(out_dir, exist_ok=True)
    meta = trips.set_index(["mmsi", "trip_id"]) if len(trips) else None
    sea = pos[pos["trip_id"] >= 0] if len(pos) else pos
    new_by_boat = {}
    if len(sea):
        for (mmsi, tid), g in sea.groupby(["mmsi", "trip_id"], sort=False):
            m = meta.loc[(mmsi, tid)] if meta is not None and (mmsi, tid) in meta.index else None
            hours = float(m["hours"]) if m is not None else 0.0
            step = 300 if hours > 24 else 180
            rec = {"id": int(tid),
                   "dep": pd.Timestamp(g["t"].iloc[0]).strftime("%Y-%m-%dT%H:%MZ"),
                   "ret": pd.Timestamp(g["t"].iloc[-1]).strftime("%Y-%m-%dT%H:%MZ"),
                   "hours": round(hours, 1),
                   "max_nm": round(float(m["max_km"]) / 1.852, 1) if m is not None else None,
                   "open": bool(m["open_ended"]) if m is not None and "open_ended" in m else False,
                   "cov": round(float(m["coverage"]), 2) if m is not None and "coverage" in m else None,
                   "pts": _thin(g, step)}
            new_by_boat.setdefault(int(mmsi), []).append(rec)
    written = 0
    for mmsi, name in names.items():
        path = os.path.join(out_dir, f"{mmsi}.json")
        old = []
        if keep_below is not None and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    old = [t for t in json.load(f).get("trips", []) if t["id"] < keep_below]
            except (OSError, ValueError):
                old = []
        new = new_by_boat.get(mmsi, [])
        if keep_below is not None:
            new = [t for t in new if t["id"] >= keep_below]
        allt = sorted(old + new, key=lambda t: t["dep"])
        if not allt and not os.path.exists(path):
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"mmsi": mmsi, "name": name, "next_archive_id": keep_below, "trips": allt},
                      f, separators=(",", ":"), allow_nan=False)
        written += 1
    return written
