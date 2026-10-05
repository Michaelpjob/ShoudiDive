"""The three questions, answered per boat and for the fleet.

1. Return rate: for consecutive trips of one boat, what share of trip N+1's
   fishing minutes falls in H3_RES_FINE cells (~0.5 km) the boat fished on
   trip N, and within a 1-ring (~1.5 km) of them. "Same spot" at the fine
   scale; "same area" at the ring scale.
2. Home zone: concentration of a boat's season fishing minutes across
   H3_RES cells: share in its top cell, share in its top 5 cells, and the
   number of cells needed to reach 50% of its minutes (the fewer, the
   stronger the home zone).
3. Convergence: how often a boat's fishing day had >= 3 fleet boats in the
   same cell (fleet-on-a-bite days), from zones.score's convergence table.
"""
import numpy as np
import pandas as pd
import h3


def return_rates(stops):
    rows = []
    for mmsi, g in stops.groupby("mmsi"):
        trips = (g.groupby("trip_id")
                   .agg(start=("start", "min"))
                   .sort_values("start").index.tolist())
        prev_fine = None
        prev_ring = None
        for tid in trips:
            tg = g[g["trip_id"] == tid]
            fine = set(tg["h3f"])
            if prev_fine is not None and tg["minutes"].sum() > 0:
                same = tg.loc[tg["h3f"].isin(prev_fine), "minutes"].sum() / tg["minutes"].sum()
                near = tg.loc[tg["h3f"].isin(prev_ring), "minutes"].sum() / tg["minutes"].sum()
                rows.append({"mmsi": int(mmsi), "trip_id": int(tid), "same_spot": float(same), "same_area": float(near)})
            prev_fine = fine
            prev_ring = set()
            for c in fine:
                prev_ring.update(h3.grid_disk(c, 1))
    return pd.DataFrame(rows, columns=["mmsi", "trip_id", "same_spot", "same_area"])


def home_zones(stops):
    rows = []
    for mmsi, g in stops.groupby("mmsi"):
        m = g.groupby("h3")["minutes"].sum().sort_values(ascending=False)
        tot = float(m.sum())
        if tot <= 0:
            continue
        shares = (m / tot).values
        cum = np.cumsum(shares)
        rows.append({
            "mmsi": int(mmsi), "fish_min": round(tot), "cells": int(len(m)),
            "top_cell": m.index[0], "top_share": round(float(shares[0]), 3),
            "top5_share": round(float(shares[:5].sum()), 3),
            "cells_for_half": int(np.searchsorted(cum, 0.5) + 1),
        })
    return pd.DataFrame(rows)


def convergence_share(stops, conv):
    """Per boat: share of its fishing days that were convergence days (>=3 boats in its cell)."""
    key = set(zip(conv["h3"], conv["local_date"]))
    rows = []
    for mmsi, g in stops.groupby("mmsi"):
        days = g.groupby("local_date")["h3"].apply(set)
        n = len(days)
        if n == 0:
            continue
        hit = sum(1 for d, cells in days.items() if any((c, d) in key for c in cells))
        rows.append({"mmsi": int(mmsi), "fishing_days": int(n), "convergence_days": int(hit),
                     "convergence_share": round(hit / n, 3)})
    return pd.DataFrame(rows)


def fleet_summary(rr, hz, cs):
    out = {}
    if len(rr):
        out["return_same_spot_median"] = round(float(rr["same_spot"].median()), 3)
        out["return_same_area_median"] = round(float(rr["same_area"].median()), 3)
        out["return_same_area_mean"] = round(float(rr["same_area"].mean()), 3)
        out["trip_pairs"] = int(len(rr))
    if len(hz):
        out["home_top_share_median"] = round(float(hz["top_share"].median()), 3)
        out["home_cells_for_half_median"] = float(hz["cells_for_half"].median())
    if len(cs):
        out["convergence_share_median"] = round(float(cs["convergence_share"].median()), 3)
        out["convergence_days_total"] = int(cs["convergence_days"].sum())
    return out
