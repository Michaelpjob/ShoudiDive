"""Fetch CA river discharge from USGS NWIS.

For each river mouth tracked by the visibility model, we hit two USGS web
services:
  * `iv` (instantaneous values) for the past 3 days — averaged to give a
    representative current discharge in cfs.
  * `stat` (long-term statistics) for the month-of-year mean — used as the
    climatological baseline so the model can compute log-anomalies.

Some CA gauges run dry seasonally; missing data is handled gracefully by
falling back to a small positive baseline (1 cfs) and a sane climo so the
anomaly term contributes ~0 instead of crashing.

Output: public/data/rivers.json — a small lookup the visibility orchestrator
loads per cell via nearest-river lookup.

Run: python pipeline/fetch_rivers.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public" / "data"

# USGS site IDs picked for "closest reliable gauge to the river mouth".
# (lat/lng here mirrors RIVER_MOUTHS in fetch_visibility.py for clarity.)
RIVERS = [
    {"name": "salinas",      "site": "11152500", "lat": 36.747, "lng": -121.808},  # Salinas R nr Spreckels
    {"name": "santa-clara",  "site": "11114000", "lat": 34.236, "lng": -119.265},  # Santa Clara R at Montalvo
    {"name": "ventura",      "site": "11118500", "lat": 34.275, "lng": -119.302},  # Ventura R nr Ventura
    {"name": "la-river",     "site": "11103000", "lat": 33.748, "lng": -118.205},  # LA R at LA
    {"name": "santa-ana",    "site": "11078000", "lat": 33.633, "lng": -117.953},  # Santa Ana R bl Prado Dam
    {"name": "san-luis-rey", "site": "11042000", "lat": 33.215, "lng": -117.395},  # SLR at Oceanside
    {"name": "san-diego",    "site": "11023340", "lat": 32.756, "lng": -117.221},  # SDR at Fashion Valley
    {"name": "tijuana",      "site": "11013500", "lat": 32.555, "lng": -117.130},  # TJR nr Nestor
    {"name": "carmel",       "site": "11143250", "lat": 36.539, "lng": -121.929},  # Carmel R nr Carmel
    {"name": "santa-ynez",   "site": "11128500", "lat": 34.701, "lng": -120.597},  # SYR at Lompoc
]

NWIS_IV = "https://waterservices.usgs.gov/nwis/iv/"
NWIS_STAT = "https://waterservices.usgs.gov/nwis/stat/"

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json", "User-Agent": "shouldidive/0.1"})


def fetch_recent_discharge(site: str, period_days: int = 3) -> float | None:
    """Mean discharge in cfs over the past N days. Returns None if no data."""
    params = {
        "format": "json",
        "sites": site,
        "parameterCd": "00060",  # discharge, cubic feet per second
        "period": f"P{period_days}D",
    }
    try:
        r = SESSION.get(NWIS_IV, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  {site}: iv fetch failed — {e!s}")
        return None

    series = data.get("value", {}).get("timeSeries", [])
    if not series:
        return None
    points = series[0].get("values", [{}])[0].get("value", [])
    vals = []
    for p in points:
        v = p.get("value")
        if v in (None, "", "-999999"):
            continue
        try:
            f = float(v)
        except ValueError:
            continue
        # USGS uses -999999 / large-negative sentinel for missing.
        if f < 0:
            continue
        vals.append(f)
    if not vals:
        return None
    return sum(vals) / len(vals)


def fetch_monthly_climo(site: str, month: int) -> float | None:
    """Long-term mean daily discharge for the given calendar month. Returns
    None if the gauge has no published statistics."""
    params = {
        "format": "rdb",
        "sites": site,
        "parameterCd": "00060",
        "statReportType": "monthly",
        "statTypeCd": "mean",
    }
    try:
        r = SESSION.get(NWIS_STAT, params=params, timeout=30)
        r.raise_for_status()
        text = r.text
    except Exception as e:
        print(f"  {site}: stat fetch failed — {e!s}")
        return None

    # RDB is tab-separated with `#`-prefixed comments and one header line.
    header = None
    rows: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        if header is None:
            header = cols
            continue
        # second non-comment line is the type spec (e.g. "5s"); skip it.
        if cols and cols[0] in ("5s", "USGS"):
            if cols[0] == "5s":
                continue
        rows.append(cols)

    if not header or not rows:
        return None

    try:
        i_month = header.index("month_nu")
        i_mean = header.index("mean_va")
    except ValueError:
        return None

    means = []
    for row in rows:
        if len(row) <= max(i_month, i_mean):
            continue
        try:
            if int(row[i_month]) == month:
                means.append(float(row[i_mean]))
        except ValueError:
            continue
    if not means:
        return None
    return sum(means) / len(means)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    rivers_out = []
    for r in RIVERS:
        site = r["site"]
        flow = fetch_recent_discharge(site)
        climo = fetch_monthly_climo(site, now.month)
        # Fallbacks chosen so the model's log-anomaly term is ~0 when data is
        # missing — preserves the "no fake signal" property.
        flow_safe = flow if flow is not None else 1.0
        climo_safe = climo if climo is not None else max(flow_safe, 1.0)
        rivers_out.append({
            "name":          r["name"],
            "site":          site,
            "lat":           r["lat"],
            "lng":           r["lng"],
            "discharge_cfs": round(flow_safe, 2),
            "climo_cfs":     round(climo_safe, 2),
            "has_recent":    flow is not None,
            "has_climo":     climo is not None,
        })
        tag = "OK" if flow is not None else "no-data"
        print(f"  {r['name']:>14} ({site}): {flow_safe:7.1f} cfs  climo {climo_safe:7.1f}  [{tag}]")

    out = {
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "month": now.month,
        "rivers": rivers_out,
    }
    out_path = OUT_DIR / "rivers.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"wrote {out_path.name}")


if __name__ == "__main__":
    main()
