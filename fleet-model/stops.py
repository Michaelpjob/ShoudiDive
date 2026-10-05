"""Turn trip positions into fishing stops.

Two stop kinds, because San Diego party boats fish two ways:
  * drift/anchor: a run of positions at SOG < DRIFT_MAX_KT lasting >= DRIFT_MIN_MIN
    (anchored on structure, drifting a paddy, drifting a bluefin school).
  * troll: a run at TROLL_KT speeds lasting >= TROLL_MIN_MIN whose
    straightness (net displacement / path length) is < TROLL_MAX_STRAIGHTNESS.
    A transit is a straight line at those speeds; trolling loops and zig-zags.
A run breaks at an AIS gap longer than GAP_BREAK_MIN. Positions inside the
harbor ring (HARBOR_KM of a dock) are never stops: that is the bait receiver
and the channel. Each stop carries its centroid, H3 cells, minutes, and the
local date, which is the join key for the landings' per-boat fish counts.
"""
import numpy as np
import pandas as pd
import h3

from config import (DRIFT_MAX_KT, DRIFT_MIN_MIN, TROLL_KT, TROLL_MIN_MIN,
                    TROLL_MAX_STRAIGHTNESS, GAP_BREAK_MIN, H3_RES, H3_RES_FINE, LOCAL_TZ, NEARSHORE_KM)
from geo import haversine_km
from trips import path_length_km


def _runs(mask, t, gap_min):
    """Yield (i, j) index ranges where mask is true and gaps are < gap_min."""
    n = len(mask)
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i + 1
        while j < n and mask[j] and (t[j] - t[j - 1]) / np.timedelta64(1, "m") <= gap_min:
            j += 1
        yield i, j
        i = j


def detect(pos):
    """pos: annotated positions with trip_id. Returns stops DataFrame."""
    out = []
    sid = 0
    sea = pos[pos["trip_id"] >= 0]
    for (mmsi, tid), g in sea.groupby(["mmsi", "trip_id"], sort=False):
        g = g[~g["in_harbor"]]
        if len(g) < 3:
            continue
        t = g["t"].values
        lat = g["lat"].values
        lon = g["lon"].values
        sog = g["sog"].fillna(0).values
        kinds = [
            ("drift", sog < DRIFT_MAX_KT, DRIFT_MIN_MIN),
            ("troll", (sog >= TROLL_KT[0]) & (sog <= TROLL_KT[1]), TROLL_MIN_MIN),
        ]
        for kind, mask, min_len in kinds:
            for i, j in _runs(mask, t, GAP_BREAK_MIN):
                mins = (t[j - 1] - t[i]) / np.timedelta64(1, "m")
                if mins < min_len:
                    continue
                la, lo = lat[i:j], lon[i:j]
                if kind == "troll":
                    plen = path_length_km(la, lo)
                    net = float(haversine_km(la[0], lo[0], la[-1], lo[-1]))
                    straight = net / plen if plen > 0 else 1.0
                    if straight > TROLL_MAX_STRAIGHTNESS:
                        continue
                clat, clon = float(np.mean(la)), float(np.mean(lo))
                ts = pd.Timestamp(t[i]).tz_localize("UTC")
                out.append({
                    "stop_id": sid, "mmsi": int(mmsi), "trip_id": int(tid), "kind": kind,
                    "start": ts, "end": pd.Timestamp(t[j - 1]).tz_localize("UTC"),
                    "local_date": ts.tz_convert(LOCAL_TZ).strftime("%Y-%m-%d"),
                    "minutes": round(float(mins), 1),
                    "lat": round(clat, 5), "lon": round(clon, 5),
                    "spread_km": round(float(haversine_km(la, lo, clat, clon).max()), 2),
                    "dist_km": round(float(g["dist_km"].values[i:j].mean()), 1),
                    "nearshore": bool(g["dist_km"].values[i:j].mean() < NEARSHORE_KM),
                    "h3": h3.latlng_to_cell(clat, clon, H3_RES),
                    "h3f": h3.latlng_to_cell(clat, clon, H3_RES_FINE),
                })
                sid += 1
    cols = ["stop_id", "mmsi", "trip_id", "kind", "start", "end", "local_date", "minutes",
            "lat", "lon", "spread_km", "dist_km", "nearshore", "h3", "h3f"]
    return pd.DataFrame(out, columns=cols)
