"""Fetch San Clemente Island military closure schedules from scisland.org.

scisland.org is the U.S. Navy's public-outreach site for SCI; boaters/divers
check it before a trip because SCI waters close for live-fire + air/surface
training. This builds a self-contained GeoJSON the web app renders directly
(like public/data/mpa-boundaries.geojson) — zone polygons + per-day status +
closure windows for a rolling 7-day forecast.

Source format (since mid-2026): the schedule page (schedule.html) is rendered
client-side from ONE data file, ``assets/js/data.js``:

    //2026-Oct-19 20:49:18                      <- regeneration stamp
    const data = {
      "dates":  {"19-Oct-26": {"sz": {"A": [0], "B": [13, 393], ...},
                               "sched": [0, 13, ...]}, ...},
      "events": ["LEEWARD-1, 19-Oct-26 1300, 19-Oct-26 1700, 0:0", ...],
      "points": {"A": [["33.03417", "-118.59778"], ...], "SOAR S1": [...]},
      "areas":  []
    }

  * ``dates[d].sz[zone]`` lists the event indexes that affect nearshore safety
    zone A..F (plus G / W, which are always restricted). ``dates[d].sched``
    lists every event that day, across all offshore operations areas.
  * ``events[i]`` = "AREA, start, end, altitudes" with local-Pacific times.
  * ``points[name]`` = the polygon for every zone/area as [lat, lng] strings.

The pre-2026 per-day pages (schedules/safetyZoneUse/days/<N>.html) still
resolve but are a frozen June 2026 snapshot — reading them is what froze this
layer at 2026-06-29. Status rules mirror the site's own schedule.js:

  * safety zone with no events that day          -> open
  * any event covering >= 24 h                   -> restricted
  * shorter timed events                         -> scheduled
  * G (Golf) + W (Wilson Cove)                   -> always restricted
  * a date the source does not list              -> unknown (the site makes
    no availability claim for it, so neither do we)

GitHub runner egress IPs get HTTP 403 from scisland.org (a local fetch with
the same headers gets 200). When the direct fetch is refused, this falls back
to the shouldidive-relay Cloudflare Worker (workers/relay/), which fetches
this one file and nothing else.

Run:
    python pipeline/fetch_sci_closures.py
Output:
    public/data/navy-closures.geojson   (CA region only)
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
try:
    from pipeline.lib.http import http_get
    from pipeline.regions import active_region
except ModuleNotFoundError:  # invoked as `python pipeline/fetch_sci_closures.py`
    sys.path.insert(0, str(ROOT / "pipeline"))
    from lib.http import http_get
    from regions import active_region

SCI_DATA_URL = "https://www.scisland.org/assets/js/data.js"
RELAY_URL = os.environ.get(
    "SCI_RELAY_URL",
    "https://shouldidive-relay.michael-job-gb.workers.dev/scisland/data.js",
)
SCI_HEADERS = {
    # The site 403s the pipeline's default UA; a browser UA + referer get 200.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://www.scisland.org/schedule.html",
}
PACIFIC = ZoneInfo("America/Los_Angeles")
FORECAST_DAYS = 7

# Nearshore safety zones: source key -> (stable feature id, map label). The ids
# predate the data.js format and are kept so saved UI state + tests stay valid.
SAFETY_ZONES = {
    "A": ("SZALFA", "A"),
    "B": ("SZBRAVO", "B"),
    "C": ("SZCHARLIE", "C"),
    "D": ("SZDELTA", "D"),
    "E": ("SZECHO", "E"),
    "F": ("SZFOXTROT", "F"),
    "G": ("SZGOLF", "G"),
    "W": ("SZWILCOVE", "W/C"),
}
ALWAYS_RESTRICTED = {"G", "W"}  # schedule.js drawAlwaysRed()

_DATA_RE = re.compile(r"(?:const|var|let)\s+data\s*=\s*(\{.*\})\s*;?\s*$", re.S)
_STAMP_RE = re.compile(r"^\s*//\s*(\d{4}-[A-Za-z]{3}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)")


# ---- pure parsers (network-free; unit-tested) --------------------------

def parse_data_js(text: str) -> tuple[dict, str | None]:
    """Split data.js into (data dict, regeneration stamp or None).
    Raises ValueError when the `data` object is missing or not JSON."""
    m = _DATA_RE.search(text)
    if not m:
        raise ValueError("no `data = {...}` object in data.js")
    data = json.loads(m.group(1))
    if not isinstance(data, dict) or "dates" not in data or "events" not in data:
        raise ValueError("data.js object is missing dates/events")
    s = _STAMP_RE.match(text)
    stamp = None
    if s:
        for fmt in ("%Y-%b-%d %H:%M:%S", "%Y-%b-%d %H:%M"):
            try:
                dt = datetime.strptime(s.group(1).title(), fmt)
            except ValueError:
                continue
            # Same shape as the old pages' "Updated Jun 3, 2026, 15:22".
            stamp = f"{dt:%b} {dt.day}, {dt.year}, {dt:%H:%M}"
            break
    return data, stamp


def parse_sci_date(key: str) -> date | None:
    """'19-Oct-26' -> date(2026, 10, 19); None if unparseable."""
    try:
        return datetime.strptime(key.strip().title(), "%d-%b-%y").date()
    except ValueError:
        return None


def _parse_pt(stamp: str) -> datetime:
    """'19-Oct-26 1300' (local Pacific) -> aware datetime in PT. '2400' rolls
    to 00:00 the next day."""
    day, hhmm = stamp.strip().split()
    hhmm = hhmm.zfill(4)
    d = datetime.strptime(day.title(), "%d-%b-%y")
    hours, minutes = int(hhmm[:2]), int(hhmm[2:])
    return (d + timedelta(hours=hours, minutes=minutes)).replace(tzinfo=PACIFIC)


def friendly_altitude(raw: str) -> str:
    """'0:50000' -> 'SFC:50K', '-90000:8000' -> 'FLOOR:8000' (schedule.js
    makeFriendlyAltitudes), so the popup matches what scisland.org shows."""
    if ":" not in raw:
        return raw.strip()
    out = []
    for part in raw.split(":"):
        try:
            v = int(part.strip())
        except ValueError:
            out.append(part.strip())
            continue
        if v == 0:
            out.append("SFC")
        elif v == -90000:
            out.append("FLOOR")
        elif v > 10000:
            out.append(f"{v // 1000}K" if v % 1000 == 0 else f"{v / 1000:g}K")
        else:
            out.append(str(v))
    return ":".join(out)


def parse_event(raw: str) -> dict | None:
    """'AREA, 19-Oct-26 1300, 19-Oct-26 1700, 0:0' -> window dict, or None."""
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) < 4:
        return None
    try:
        start, end = _parse_pt(parts[1]), _parse_pt(parts[2])
    except (ValueError, IndexError):
        return None
    return {
        "area": parts[0],
        "start_local": f"{start:%Y-%m-%d %H:%M} PT",
        "end_local": f"{end:%Y-%m-%d %H:%M} PT",
        "start_utc": start.astimezone(timezone.utc)
            .isoformat(timespec="minutes").replace("+00:00", "Z"),
        "end_utc": end.astimezone(timezone.utc)
            .isoformat(timespec="minutes").replace("+00:00", "Z"),
        "hours": round((end - start).total_seconds() / 3600, 2),
        "altitude": friendly_altitude(parts[3]),
    }


def status_for(windows: list[dict], always_restricted: bool = False) -> str:
    if always_restricted:
        return "restricted"
    if not windows:
        return "open"
    if any(w["hours"] >= 24 for w in windows):
        return "restricted"
    return "scheduled"


def polygon_from_points(rows) -> tuple[dict, list[float]] | None:
    """[["lat","lng"], ...] -> (GeoJSON Polygon, [lon, lat] centroid)."""
    ring = []
    for row in rows or []:
        try:
            lat, lng = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        ring.append([lng, lat])
    if len(ring) < 3:
        return None
    verts = ring[:-1] if ring[0] == ring[-1] else ring
    centroid = [
        round(sum(p[0] for p in verts) / len(verts), 5),
        round(sum(p[1] for p in verts) / len(verts), 5),
    ]
    if ring[0] != ring[-1]:
        ring.append(list(ring[0]))
    return {"type": "Polygon", "coordinates": [ring]}, centroid


def build_closures(data: dict, stamp: str | None, today: date) -> dict:
    """Assemble the rolling FORECAST_DAYS GeoJSON from a parsed data.js."""
    dates = [today + timedelta(days=i) for i in range(FORECAST_DAYS)]
    date_isos = [d.isoformat() for d in dates]
    events = data.get("events") or []
    points = data.get("points") or {}

    # Index the source's day keys by real date. The file carries the odd typo
    # (e.g. '18-Nov-20' beside '18-Nov-26'), so keep only dates within a year
    # of today when computing the published window.
    by_date: dict[str, dict] = {}
    for key, day in (data.get("dates") or {}).items():
        d = parse_sci_date(key)
        if d is not None and isinstance(day, dict):
            by_date[d.isoformat()] = day
    sane = sorted(k for k in by_date if abs((date.fromisoformat(k) - today).days) <= 366)

    def windows_for(indexes) -> list[dict]:
        out = []
        for i in indexes or []:
            if isinstance(i, int) and 0 <= i < len(events):
                w = parse_event(events[i])
                if w:
                    out.append(w)
        out.sort(key=lambda w: w["start_utc"])
        return out

    features = []

    # Nearshore safety zones: every zone, every day.
    for key, (fid, label) in SAFETY_ZONES.items():
        poly = polygon_from_points(points.get(key))
        if poly is None:
            continue
        geom, centroid = poly
        per_date = {}
        for di in date_isos:
            day = by_date.get(di)
            if day is None:
                status = "restricted" if key in ALWAYS_RESTRICTED else "unknown"
                per_date[di] = {"status": status, "windows": []}
                continue
            wins = windows_for((day.get("sz") or {}).get(key))
            per_date[di] = {"status": status_for(wins, key in ALWAYS_RESTRICTED), "windows": wins}
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "id": fid, "label": label, "kind": "safety_zone",
                "centroid": centroid, "statusByDate": per_date,
            },
        })

    # Offshore operations areas: only the ones with ops in the forecast window
    # (the old day pages drew an area only on days it was scheduled).
    ops_by_area: dict[str, dict[str, list]] = {}
    for di in date_isos:
        day = by_date.get(di)
        if day is None:
            continue
        for w in windows_for(day.get("sched")):
            if w["area"] in SAFETY_ZONES:
                continue
            ops_by_area.setdefault(w["area"], {}).setdefault(di, []).append(w)
    for name, days in ops_by_area.items():
        poly = polygon_from_points(points.get(name))
        if poly is None:
            continue
        geom, centroid = poly
        per_date = {}
        for di in date_isos:
            if di not in by_date:
                per_date[di] = {"status": "unknown", "windows": []}
            else:
                wins = days.get(di, [])
                per_date[di] = {"status": status_for(wins), "windows": wins}
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "id": name, "label": name, "kind": "operations_area",
                "centroid": centroid, "statusByDate": per_date,
            },
        })

    features.sort(key=lambda f: (f["properties"]["kind"], f["properties"]["id"]))
    return {
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "scisland.org",
        "source_url": SCI_DATA_URL,
        "source_updated": stamp,
        "published_window": {
            "start": sane[0] if sane else None,
            "end": sane[-1] if sane else None,
        },
        "tz": "America/Los_Angeles",
        "dates": date_isos,
        "features": features,
    }


# ---- network ------------------------------------------------------------

def fetch_data_js() -> str | None:
    """data.js text, direct first and then via the relay Worker."""
    for label, url in (("direct", SCI_DATA_URL), ("relay", RELAY_URL)):
        if not url:
            continue
        r = http_get(url, headers=SCI_HEADERS, timeout=30, retries=3, use_breaker=False)
        code = getattr(r, "status_code", "no-response")
        if r is not None and r.status_code == 200 and "data" in r.text:
            print(f"  [closures] {label}: 200 ({len(r.text)} B)", flush=True)
            return r.text
        print(f"  [closures] {label}: {code}", flush=True)
    return None


def main() -> int:
    region = active_region()
    out_dir = region.data_output_dir(ROOT)
    text = fetch_data_js()
    if text is None:
        print("[closures] could not fetch data.js — leaving previous file untouched", flush=True)
        return 1
    try:
        data, stamp = parse_data_js(text)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[closures] data.js unparseable ({e}) — leaving previous file untouched", flush=True)
        return 1
    fc = build_closures(data, stamp, datetime.now(PACIFIC).date())
    n_sz = sum(1 for f in fc["features"] if f["properties"]["kind"] == "safety_zone")
    n_ops = len(fc["features"]) - n_sz
    today = fc["dates"][0]
    closed_today = [
        f["properties"]["id"] for f in fc["features"]
        if f["properties"]["statusByDate"][today]["status"] in ("restricted", "scheduled")
    ]
    print(f"[closures] source updated {stamp}; window "
          f"{fc['published_window']['start']} -> {fc['published_window']['end']}; "
          f"{n_sz} safety zones + {n_ops} ops areas; closed/scheduled today "
          f"({today}): {closed_today or 'none'}", flush=True)
    if n_sz == 0:
        print("[closures] no safety-zone geometry parsed — leaving previous file untouched", flush=True)
        return 1
    out_path = out_dir / "navy-closures.geojson"
    out_path.write_text(json.dumps(fc, indent=2) + "\n", encoding="utf-8")
    print(f"[closures] wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
