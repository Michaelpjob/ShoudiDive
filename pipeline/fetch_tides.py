"""Fetch today's tide range for the active region's NOAA CO-OPS stations.

The visibility model's `tide_index` feature uses the daily tide range (max
minus min over a 24h window) as a proxy for tidal mixing energy. Range is
much more useful than a single height because it captures spring-vs-neap
variation, which actually matters for water clarity in shallow areas.

We pull the next-24-hour hi/lo predictions for each station, take max-min,
and emit a per-station JSON for the orchestrator to spread spatially via
nearest-station sampling.

2026-05-13: the per-region station list moved out of this file into
`pipeline/regions/{ca,pnw,tropical}.py` (the `tide_stations` field on
the Region dataclass). Add a new region's stations there; this file
stays generic.

Output: public/data/[<region>/]tides.json

Run: python pipeline/fetch_tides.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Region-aware output dir. CA stays at public/data/; PNW + tropical
# land under public/data/<region>/. See pipeline/regions/ (PR-X-1).
try:
    from pipeline.regions import active_region
    from pipeline.lib.http import http_get
except ModuleNotFoundError:
    from regions import active_region
    from lib.http import http_get

REGION = active_region()
OUT_DIR = REGION.data_output_dir(ROOT)
STATIONS = REGION.tide_stations

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"


def fetch_tide_range_m(station_id: str) -> dict | None:
    """Next-24h tide predictions for a station. None on error.

    Returns ``{"range_m": float, "events": [{"t", "type", "v_m"}, ...]}``
    — the predicted max-minus-min height plus the hi/lo events
    themselves (ISO UTC time, "H"/"L", height in meters).
    """
    now = datetime.now(timezone.utc)
    begin = now.strftime("%Y%m%d %H:%M")
    end = (now + timedelta(hours=24)).strftime("%Y%m%d %H:%M")
    params = {
        "product": "predictions",
        "datum": "MLLW",
        "interval": "hilo",
        "station": station_id,
        "begin_date": begin,
        "end_date": end,
        "time_zone": "gmt",
        "units": "metric",
        "format": "json",
        "application": "shouldidive",
    }
    try:
        # Stage 6a: was SESSION.get + raise_for_status with per-file UA.
        # http_get adds the shared lib/http Session + retries with
        # exponential backoff (NOAA CO-OPS occasionally throttles).
        r = http_get(API, params=params, timeout=30, raise_on_failure=True)
        data = r.json()
    except Exception as e:
        print(f"  {station_id}: fetch failed — {e!s}")
        return None

    preds = data.get("predictions", [])
    if not preds:
        return None
    vals = []
    events = []
    for p in preds:
        try:
            v = float(p["v"])
        except (KeyError, ValueError):
            continue
        vals.append(v)
        # Keep the hi/lo EVENTS too (time + type), not just the range.
        # The water-column model (fetch_viz_column.py) phase-locks the
        # internal-tide cliff swing to high-water times. Additive key —
        # existing range_m consumers are unaffected.
        t, typ = p.get("t"), p.get("type")
        if t and typ in ("H", "L"):
            events.append({
                "t": t.replace(" ", "T") + "Z",  # CO-OPS gmt -> ISO UTC
                "type": typ,
                "v_m": round(v, 3),
            })
    if len(vals) < 2:
        return None
    return {"range_m": max(vals) - min(vals), "events": events}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    if not STATIONS:
        # Region declares no tide stations (e.g., a brand-new region
        # skeleton). Skip silently — the visibility model will fall
        # back to its default tide_index when tides.json is missing.
        print(f"[tides] region={REGION.name} has no tide_stations — skip")
        return
    out_stations = []
    for st in STATIONS:
        fetched = fetch_tide_range_m(st["id"])
        # Fallback: 1.5 m is a reasonable mean range on the CA coast — keeps
        # `tide_index` ≠ 0 but well below spring-tide values.
        rng_safe = fetched["range_m"] if fetched is not None else 1.5
        out_stations.append({
            "name":          st["name"],
            "id":            st["id"],
            "lat":           st["lat"],
            "lng":           st["lng"],
            "range_m":       round(rng_safe, 3),
            "events":        fetched["events"] if fetched is not None else [],
            "has_real_data": fetched is not None,
        })
        tag = "OK" if fetched is not None else "fallback"
        print(f"  {st['name']:>14} ({st['id']}): {rng_safe:.2f} m  [{tag}]")

    out = {
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "stations": out_stations,
    }
    out_path = OUT_DIR / "tides.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"wrote {out_path.name}")


if __name__ == "__main__":
    main()
