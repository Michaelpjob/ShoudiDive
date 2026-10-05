"""Score H3 cells from fishing stops: per cell x week and per cell overall.

Per cell and week (Monday-keyed ISO weeks, local dates):
  fish_min   total stop minutes (drift + troll)
  trips      distinct (mmsi, trip_id)
  boats      distinct MMSIs
  stops      number of stops
  troll_min  minutes in trolling runs (subset of fish_min)
Convergence events: (local_date, cell) with >= CONV_BOATS distinct boats.
"""
import pandas as pd
import h3

CONV_BOATS = 3


def week_key(local_date):
    d = pd.to_datetime(local_date)
    return (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")


def score(stops):
    s = stops.copy()
    s["week"] = week_key(s["local_date"])
    s["troll_min"] = s["minutes"].where(s["kind"] == "troll", 0.0)
    s["trip_key"] = s["mmsi"].astype(str) + ":" + s["trip_id"].astype(str)
    agg = dict(fish_min=("minutes", "sum"), trips=("trip_key", "nunique"), boats=("mmsi", "nunique"),
               stops=("stop_id", "size"), troll_min=("troll_min", "sum"))
    cw = s.groupby(["h3", "week"]).agg(**agg).reset_index()
    ct = s.groupby("h3").agg(**agg).reset_index()
    # per-cell top boats by minutes (for the hover panel)
    cb = s.groupby(["h3", "mmsi"])["minutes"].sum().reset_index()
    top = (cb.sort_values(["h3", "minutes"], ascending=[True, False])
             .groupby("h3").head(5).groupby("h3")
             .apply(lambda g: [[int(m), round(float(v))] for m, v in zip(g["mmsi"], g["minutes"])])
             .to_dict())
    # convergence: same cell, same local day, several boats
    cd = s.groupby(["h3", "local_date"]).agg(boats=("mmsi", "nunique"), fish_min=("minutes", "sum"),
                                              mmsis=("mmsi", lambda x: sorted(set(int(v) for v in x)))).reset_index()
    conv = cd[cd["boats"] >= CONV_BOATS].sort_values(["local_date", "boats"], ascending=[True, False])
    return cw, ct, top, conv


def cell_boundary(cell):
    return [[round(la, 4), round(lo, 4)] for la, lo in h3.cell_to_boundary(cell)]


def cell_center(cell):
    la, lo = h3.cell_to_latlng(cell)
    return [round(la, 4), round(lo, 4)]
