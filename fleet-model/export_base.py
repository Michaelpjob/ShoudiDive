"""Freeze the ARCHIVE part of the model so a nightly CI job only has to process
the live days.

    python export_base.py --start 2025-06-01 --end 2026-06-30

Runs the model over the archive window and writes fleet-model/data/base/
  trips.parquet    one row per trip (as model.run produces, plus mmsi)
  stops.parquet    one row per stop
  cov.parquet      per-MMSI position counts (the "AIS coverage" input)
  counts.parquet   the landings' dock counts for the window (from $FLEET_DATA_DIR)
  meta.json        window + counts
These are small (a few MB) and committed; build_site.py --base merges them
with whatever live days are on disk (parquets rolled from the fleet-live
branch) and rebuilds data.json without touching the 40 GB archive.
"""
import argparse
import json
import os
import shutil

import model
import paths
from config import DATA_DIR

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "data", "base")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args()
    r = model.run(a.start, a.end)
    os.makedirs(BASE, exist_ok=True)
    r["trips"].to_parquet(os.path.join(BASE, "trips.parquet"), index=False)
    r["stops"].to_parquet(os.path.join(BASE, "stops.parquet"), index=False)
    r["coverage"].to_parquet(os.path.join(BASE, "cov.parquet"), index=False)
    names = {b["mmsi"]: b["name"] for b in r["roster"]["boats"]}
    n = paths.write_trip_paths(r["pos"], r["trips"], os.path.join(HERE, "..", "public", "fleet", "trips"), names)
    print(f"trip paths: {n} boat files -> public/fleet/trips/")
    counts = os.path.join(DATA_DIR, "counts.parquet")
    if os.path.exists(counts):
        shutil.copyfile(counts, os.path.join(BASE, "counts.parquet"))
    meta = {"start": a.start, "end": a.end, "days": len(r["days"]), "trips": int(len(r["trips"])),
            "stops": int(len(r["stops"])), "boats": len(r["roster"]["boats"]),
            "next_trip_id": int(r["trips"]["trip_id"].max()) + 1 if len(r["trips"]) else 0}
    with open(os.path.join(BASE, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    print("base:", meta)


if __name__ == "__main__":
    main()
