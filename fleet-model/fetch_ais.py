"""Fetch MarineCadastre daily AIS files and reduce each to a SoCal parquet.

    python fetch_ais.py --start 2025-06-01 --end 2025-10-31 [--workers 3]

Source: NOAA OCM "csv2" daily files (zstd CSV, one position per vessel per
minute, US EEZ only), e.g.
  https://noaaocm.blob.core.windows.net/ais/csv2/csv2025/ais-2025-07-15.csv.zst
Each national day file is ~220-280 MB; the SoCal cut (BBOX below) is ~450k
rows / ~10 MB parquet. The raw file is deleted after the cut unless
--keep-raw is passed. Re-runs skip days whose parquet already exists, so the
script is safe to interrupt and relaunch.

Output dir: $FLEET_DATA_DIR/socal/<date>.parquet (default: sibling
ShoudiDive-fleet-data/ next to the repo checkout).
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import os
import sys
import time

import duckdb
import pyarrow.parquet as pq
import requests

from config import DATA_DIR, BBOX

BASE = "https://noaaocm.blob.core.windows.net/ais/csv2/csv{year}/ais-{date}.csv.zst"
COLS = ("mmsi", "base_date_time", "longitude", "latitude", "sog", "cog", "heading",
        "vessel_name", "imo", "call_sign", "vessel_type", "status", "length", "width",
        "draft", "transceiver")
# A constant statement; the path and bbox are bound parameters.
REDUCE_SQL = """
    select mmsi, base_date_time, longitude, latitude, sog, cog, heading,
           vessel_name, imo, call_sign, vessel_type, status, length, width,
           draft, transceiver
    from read_csv($raw, compression='zstd', header=true, sample_size=20000,
                  timestampformat='%Y-%m-%d %H:%M:%S',
                  types={'base_date_time': 'TIMESTAMP'})
    where latitude between $s and $n and longitude between $w and $e
"""


def day_range(start, end):
    d = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    while d <= e:
        yield d
        d += dt.timedelta(days=1)


def download(url, dest, retries=4):
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=120) as r:
                if r.status_code == 404:
                    return False
                r.raise_for_status()
                tmp = dest + ".part"
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                os.replace(tmp, dest)
                return True
        except Exception as exc:  # noqa: BLE001
            wait = 10 * (attempt + 1)
            print(f"  retry {attempt + 1} for {os.path.basename(dest)}: {exc} (sleep {wait}s)", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"download failed: {url}")


def reduce_day(raw, out):
    (s, w, n, e) = BBOX  # south, west, north, east
    con = duckdb.connect()
    # base_date_time is UTC in the source ("2025-07-15 07:00:00"); keep it as a
    # naive UTC timestamp. sample_size=20000 is plenty to type the columns and
    # the WHERE prunes the rest. Path and bbox are bound parameters.
    tbl = con.execute(REDUCE_SQL, {"raw": raw.replace(os.sep, "/"), "s": s, "n": n, "w": w, "e": e}).fetch_arrow_table()
    con.close()
    pq.write_table(tbl, out, compression="zstd")
    return tbl.num_rows


def process(day, keep_raw):
    date = day.isoformat()
    out = os.path.join(DATA_DIR, "socal", f"{date}.parquet")
    if os.path.exists(out):
        return date, "skip", None
    raw_dir = os.path.join(DATA_DIR, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    raw = os.path.join(raw_dir, f"ais-{date}.csv.zst")
    t0 = time.time()
    if not os.path.exists(raw):
        ok = download(BASE.format(year=day.year, date=date), raw)
        if not ok:
            return date, "missing", None
    tmp = out + ".part"
    n = reduce_day(raw, tmp)
    os.replace(tmp, out)
    if not keep_raw:
        os.remove(raw)
    return date, f"{n} rows in {time.time() - t0:.0f}s", n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--keep-raw", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.join(DATA_DIR, "socal"), exist_ok=True)
    days = list(day_range(a.start, a.end))
    print(f"{len(days)} days -> {os.path.join(DATA_DIR, 'socal')}", flush=True)
    missing = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for date, msg, n in ex.map(lambda d: process(d, a.keep_raw), days):
            print(f"{date}: {msg}", flush=True)
            if msg == "missing":
                missing.append(date)
    if missing:
        print(f"not published yet: {missing[0]} .. {missing[-1]} ({len(missing)} days)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
