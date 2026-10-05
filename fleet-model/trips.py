"""Split each boat's AIS track into trips (dock -> sea -> dock).

A position is "in harbor" when it is within HARBOR_KM of any landing dock:
that covers the bay channels and the bait receivers, which are not fishing.
A trip is a maximal run of out-of-harbor positions bracketed by in-harbor
positions, lasting >= MIN_TRIP_HOURS and reaching >= MIN_TRIP_KM from the
nearest dock. AIS coverage gaps (the boat sails beyond terrestrial receiver
range and goes silent) do NOT end a trip; they are recorded as the trip's
coverage fraction so downstream stats can be weighted or caveated.

Input:  positions DataFrame from tracks.load_positions (mmsi, t, lat, lon, sog, cog)
Output: (positions with trip_id/in_harbor/dist_km columns, trips DataFrame)
"""
import numpy as np
import pandas as pd

from config import LANDINGS, HARBOR_KM, MIN_TRIP_HOURS, MIN_TRIP_KM, LOCAL_TZ
from geo import min_dist_to_points_km, haversine_km

DOCKS = [(v[0], v[1]) for v in LANDINGS.values()]
DOCK_NAMES = list(LANDINGS)


def annotate(pos):
    pos = pos.sort_values(["mmsi", "t"]).reset_index(drop=True)
    d, idx = min_dist_to_points_km(pos["lat"].values, pos["lon"].values, DOCKS)
    pos["dist_km"] = d
    pos["near_dock"] = idx
    pos["in_harbor"] = d <= HARBOR_KM
    return pos


def segment(pos):
    """Assign trip ids. Returns (pos, trips)."""
    pos = annotate(pos)
    pos["trip_id"] = -1
    trips = []
    tid = 0
    for mmsi, g in pos.groupby("mmsi", sort=False):
        inh = g["in_harbor"].values
        t = g["t"].values
        idx = g.index.values
        n = len(g)
        i = 0
        while i < n:
            if inh[i]:
                i += 1
                continue
            # start of an out-of-harbor run
            j = i
            while j < n and not inh[j]:
                j += 1
            # run is [i, j); bracket with the last in-harbor point before and first after
            a = i - 1 if i > 0 else i
            b = j if j < n else j - 1
            seg = g.iloc[a:b + 1]
            hours = (t[b] - t[a]) / np.timedelta64(1, "h")
            max_km = float(seg["dist_km"].max())
            if hours >= MIN_TRIP_HOURS and max_km >= MIN_TRIP_KM:
                pos.loc[idx[a:b + 1], "trip_id"] = tid
                dep_dock = int(g["near_dock"].values[a])
                ret_dock = int(g["near_dock"].values[b])
                # coverage: minutes with a position / minutes elapsed (1 pos/min nominal)
                npos = b + 1 - a
                mins = max(hours * 60, 1)
                far_i = int(np.argmax(seg["dist_km"].values))
                gaps = np.diff(t[a:b + 1]) / np.timedelta64(1, "h")
                max_gap_h = float(gaps.max()) if len(gaps) else 0.0
                tl = pd.Timestamp(t[a]).tz_localize("UTC").tz_convert(LOCAL_TZ)
                trips.append({
                    "trip_id": tid, "mmsi": int(mmsi),
                    "depart": pd.Timestamp(t[a]), "ret": pd.Timestamp(t[b]),
                    "depart_local_date": tl.strftime("%Y-%m-%d"),
                    "hours": round(hours, 2), "max_km": round(max_km, 1),
                    "far_lat": round(float(seg["lat"].values[far_i]), 4),
                    "far_lon": round(float(seg["lon"].values[far_i]), 4),
                    "n_pos": int(npos), "coverage": round(min(1.0, npos / mins), 3),
                    "max_gap_h": round(max_gap_h, 1),
                    "depart_dock": DOCK_NAMES[dep_dock], "return_dock": DOCK_NAMES[ret_dock],
                    "open_ended": bool(not inh[a]) or bool(not inh[b]),
                })
                tid += 1
            i = j
    trips = pd.DataFrame(trips)
    return pos, trips


def path_length_km(lat, lon):
    if len(lat) < 2:
        return 0.0
    return float(haversine_km(lat[:-1], lon[:-1], lat[1:], lon[1:]).sum())
