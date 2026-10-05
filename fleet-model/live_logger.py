"""Log the fleet's live AIS positions from aisstream.io into daily parquet files.

MarineCadastre publishes about two months in arrears, so this fills the gap:
a free aisstream.io API key (https://aisstream.io, sign in with GitHub) gives
one WebSocket that pushes every AIS position in a bounding box. We subscribe
to the SoCal box, keep only roster MMSIs (plus anything in FILTER_EXTRA) and
append rows in the same schema as fetch_ais.py's SoCal cut, so model.py can
read live days alongside the archive.

    set AISSTREAM_API_KEY=...        (never commit it)
    python live_logger.py [--all-vessels]

Writes $FLEET_DATA_DIR/live/<date>.jsonl (one line per position, UTC) and
rolls each finished day into $FLEET_DATA_DIR/socal/<date>.parquet when the
next UTC day starts, downsampled to one position per vessel per minute so
the live days match the archive's cadence. Reconnects on drop (the service
allows one socket per key and needs ~25 s between connects).

Hosting: this is a long-running process. Run it under Task Scheduler on a
PC that stays on, or on any small always-on box. GitHub Actions is the wrong
tool (6 h job cap), Cloudflare Workers cannot hold an outbound socket open.
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


def roll_day(day):
    """jsonl -> parquet (1 position / vessel / minute), same columns as the archive."""
    import pandas as pd
    src = os.path.join(LIVE_DIR, f"{day}.jsonl")
    out = os.path.join(DATA_DIR, "socal", f"{day}.parquet")
    if not os.path.exists(src) or os.path.exists(out):
        return
    rows = [json.loads(line) for line in open(src, encoding="utf-8")]
    if not rows:
        return
    df = pd.DataFrame(rows)
    df["base_date_time"] = pd.to_datetime(df["base_date_time"]).dt.floor("min")
    df = df.sort_values(["mmsi", "base_date_time"]).drop_duplicates(["mmsi", "base_date_time"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_parquet(out, index=False, compression="zstd")
    print(f"rolled {day}: {len(df)} positions -> {out}", flush=True)


async def run(api_key, mmsis, all_vessels):
    s, w, n, e = BBOX
    sub = {"APIKey": api_key, "BoundingBoxes": [[[s, w], [n, e]]],
           "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport"]}
    if not all_vessels and mmsis:
        sub["FiltersShipMMSI"] = [str(m) for m in mmsis[:50]]  # the service caps the MMSI filter at 50
    os.makedirs(LIVE_DIR, exist_ok=True)
    keep = set(mmsis)
    current_day = None
    fh = None
    n_rows = 0
    while True:
        try:
            async with websockets.connect(URL, ping_interval=20) as ws:
                await ws.send(json.dumps(sub))
                print(f"connected; {len(mmsis)} roster MMSIs, all_vessels={all_vessels}", flush=True)
                async for raw in ws:
                    msg = json.loads(raw)
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
                    if n_rows % 500 == 0:
                        fh.flush()
                        print(f"{dt.datetime.utcnow():%H:%M} {n_rows} positions", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"socket dropped: {exc}; reconnecting in 30 s", flush=True)
            if fh:
                fh.flush()
            time.sleep(30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-vessels", action="store_true", help="log every vessel in the box (not just the roster)")
    a = ap.parse_args()
    key = os.environ.get("AISSTREAM_API_KEY")
    if not key:
        sys.exit("set AISSTREAM_API_KEY (free key from https://aisstream.io)")
    asyncio.run(run(key, roster_mmsis(), a.all_vessels))


if __name__ == "__main__":
    main()
