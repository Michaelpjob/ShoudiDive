"""Load the SoCal AIS cut for a set of MMSIs (or everything) via DuckDB."""
import glob
import os

import duckdb
import pandas as pd

from config import DATA_DIR


def socal_files(start=None, end=None):
    files = sorted(glob.glob(os.path.join(DATA_DIR, "socal", "*.parquet")))
    out = []
    for f in files:
        d = os.path.basename(f)[:10]
        if start and d < start:
            continue
        if end and d > end:
            continue
        out.append(f)
    return out


def available_days(start=None, end=None):
    return [os.path.basename(f)[:10] for f in socal_files(start, end)]


def _paths(files):
    return [f.replace(os.sep, "/") for f in files]


def load_positions(mmsis, start=None, end=None):
    """All positions for `mmsis`, sorted by (mmsi, time). `t` is tz-aware UTC."""
    files = socal_files(start, end)
    cols = ["mmsi", "t", "lat", "lon", "sog", "cog", "heading"]
    if not files or not mmsis:
        return pd.DataFrame(columns=cols)
    con = duckdb.connect()
    # File list and MMSI list travel as bound parameters, not SQL text.
    df = con.execute("""
        select mmsi, base_date_time as t, latitude as lat, longitude as lon,
               sog, cog, heading
        from read_parquet($files)
        where list_contains($mmsis, mmsi)
        order by mmsi, base_date_time
    """, {"files": _paths(files), "mmsis": [int(m) for m in mmsis]}).df()
    con.close()
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df


def vessel_directory(start=None, end=None):
    """One row per MMSI: most common name, type, length, position count, days seen."""
    files = socal_files(start, end)
    con = duckdb.connect()
    df = con.execute("""
        select mmsi,
               mode(vessel_name) as vessel_name,
               mode(vessel_type) as vessel_type,
               mode(length) as length,
               mode(call_sign) as call_sign,
               count(*) as n_pos,
               count(distinct date_trunc('day', base_date_time)) as n_days
        from read_parquet($files)
        group by mmsi
    """, {"files": _paths(files)}).df()
    con.close()
    return df
