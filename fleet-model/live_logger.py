"""Log the fleet's live AIS positions from aisstream.io into daily parquet files.

MarineCadastre publishes about two months in arrears, so this fills the gap:
a free aisstream.io API key (https://aisstream.io, sign in with GitHub) gives
one WebSocket that pushes every AIS position in a bounding box. We subscribe
to the SoCal box, keep only roster MMSIs (plus anything in FILTER_EXTRA) and
append rows in the same schema as fetch_ais.py's SoCal cut, so model.py can
read live days alongside the archive.

    set AISSTREAM_API_KEY=...        (never commit it)
    python live_logger.py [--all-vessels] [--minutes N]   # N: stop after N minutes (CI chunks)
    python live_logger.py --roll-all                       # jsonl day files -> socal parquet, no socket

Writes $FLEET_DATA_DIR/live/<date>.jsonl (one line per position, UTC) and
rolls each finished day into $FLEET_DATA_DIR/socal/<date>.parquet when the
next UTC day starts, downsampled to one position per vessel per minute so
the live days match the archive's cadence. Reconnects on drop (the service
allows one socket per key and needs ~25 s between connects).

Hosting: a long-running process. .github/workflows/fleet-live-logger.yml runs
it in overlapping ~5 h 40 min chunks every 6 h (GitHub-hosted runners, free on
a public repo) and commits the day files to the `fleet-live` branch; a PC
under Task Scheduler or any small always-on box works the same way.
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import time

from config import DATA_DIR, BBOX

try:
    import websockets
except ImportError:  # pragma: no cover
    print("pip install websockets", file=sys.stderr)
    raise

HERE = os.path.dirname(os.path.abspath(__file__))
ROSTER = os.path.join(HERE, "data", "roster.json")
URL = "wss://stream.aisstream.io/v0/stream"
LIVE_DIR = os.path.join(DATA_DIR, "live")


def roster_mmsis():
    with open(ROSTER, encoding="utf-8") as f:
        r = json.load(f)
    return sorted({b["mmsi"] for b in r["boats"]} | {b["mmsi"] for b in r.get("candidates", [])})


def roll_day(day, force=False):
    """jsonl -> parquet (1 position / vessel / minute), same columns as the archive.
    Re-rolls an existing live parquet (never an archive one: archive days come
    from fetch_ais.py and are authoritative; a live file is only written when
    no parquet exists yet or when it was itself produced from live data)."""
    import pandas as pd
    src = os.path.join(LIVE_DIR, f"{day}.jsonl")
    out = os.path.join(DATA_DIR, "socal", f"{day}.parquet")
    marker = out + ".live"
    if not os.path.exists(src):
        return
    if os.path.exists(out) and not os.path.exists(marker) and not force:
        return  # archive day already there; the archive wins
    rows = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    if not rows:
        return
    df = pd.DataFrame(rows)
    df["base_date_time"] = pd.to_datetime(df["base_date_time"]).dt.floor("min")
    df = df.sort_values(["mmsi", "base_date_time"]).drop_duplicates(["mmsi", "base_date_time"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_parquet(out, index=False, compression="zstd")
    with open(marker, "w") as f:
        f.write("rolled from live jsonl\n")
    print(f"rolled {day}: {len(df)} positions -> {out}", flush=True)


def roll_all():
    if not os.path.isdir(LIVE_DIR):
        return
    for name in sorted(os.listdir(LIVE_DIR)):
        if name.endswith(".jsonl"):
            roll_day(name[:-6], force=True)


async def run(api_key, mmsis, all_vessels, minutes=None):
    s, w, n, e = BBOX
    sub = {"APIKey": api_key, "BoundingBoxes": [[[s, w], [n, e]]],
           "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport"]}
    # aisstream caps FiltersShipMMSI at 50 hulls and the roster is ~200, so
    # subscribe to the whole SoCal box and keep the roster locally (a few
    # hundred messages a minute; trivial).
    os.makedirs(LIVE_DIR, exist_ok=True)
    keep = set(mmsis)
    current_day = None
    fh = None
    n_rows = 0
    deadline = time.time() + minutes * 60 if minutes else None
    while True:
        if deadline and time.time() >= deadline:
            break
        try:
            async with websockets.connect(URL, ping_interval=20) as ws:
                await ws.send(json.dumps(sub))
                print(f"connected; {len(mmsis)} roster MMSIs, all_vessels={all_vessels}", flush=True)
                first = True
                async for raw in ws:
                    if deadline and time.time() >= deadline:
                        break
                    msg = json.loads(raw)
                    if "error" in msg and "Message" not in msg:
                        # aisstream answers a bad key / bad subscription with
                        # {"error": "..."} and closes: do not loop on it.
                        raise SystemExit(f"aisstream error: {msg['error']}")
                    meta = msg.get("MetaData", {})
                    mmsi = meta.get("MMSI")
                    if not mmsi or (not all_vessels and mmsi not in keep):
                        continue
                    body = msg["Message"].get("PositionReport") or msg["Message"].get("StandardClassBPositionReport") or {}
                    ts = meta.get("time_utc", "")[:19].replace(" ", "T")
                    day = ts[:10]
                    if day != current_day:
                        if fh:
                            fh.close()
                            roll_day(current_day)
                        current_day = day
                        fh = open(os.path.join(LIVE_DIR, f"{day}.jsonl"), "a", encoding="utf-8")
                    fh.write(json.dumps({
                        "mmsi": mmsi, "base_date_time": ts.replace("T", " "),
                        "longitude": meta.get("longitude"), "latitude": meta.get("latitude"),
                        "sog": body.get("Sog"), "cog": body.get("Cog"), "heading": body.get("TrueHeading"),
                        "vessel_name": (meta.get("ShipName") or "").strip() or None,
                        "imo": None, "call_sign": None, "vessel_type": None, "status": body.get("NavigationalStatus"),
                        "length": None, "width": None, "draft": None, "transceiver": "B" if "StandardClassBPositionReport" in msg["Message"] else "A",
                    }) + "\n")
                    n_rows += 1
                    if first:
                        first = False
                        print(f"first position: {meta.get('ShipName', '').strip()} ({mmsi}) at {ts}", flush=True)
                    if n_rows % 500 == 0:
                        fh.flush()
                        print(f"{dt.datetime.utcnow():%H:%M} {n_rows} positions", flush=True)
            if deadline and time.time() >= deadline:
                break
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"socket dropped: {exc}; reconnecting in 30 s", flush=True)
            if fh:
                fh.flush()
            if deadline and time.time() + 30 >= deadline:
                break
            await asyncio.sleep(30)
    if fh:
        fh.close()
    print(f"done: {n_rows} positions this run", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-vessels", action="store_true", help="log every vessel in the box (not just the roster)")
    ap.add_argument("--minutes", type=float, default=None, help="stop after this many minutes (CI chunks)")
    ap.add_argument("--roll-all", action="store_true", help="only roll existing jsonl day files into parquet")
    a = ap.parse_args()
    if a.roll_all:
        roll_all()
        return
    key = os.environ.get("AISSTREAM_API_KEY")
    if not key:
        sys.exit("set AISSTREAM_API_KEY (free key from https://aisstream.io)")
    asyncio.run(run(key, roster_mmsis(), a.all_vessels, a.minutes))


if __name__ == "__main__":
    main()
