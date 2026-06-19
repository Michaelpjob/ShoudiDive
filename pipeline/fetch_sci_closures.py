"""Scrape San Clemente Island military closure schedules from scisland.org.

scisland.org is the U.S. Navy's public-outreach site for SCI; boaters/divers
check it before a trip because SCI waters close for live-fire + air/surface
training. This builds a self-contained GeoJSON the web app renders directly
(like public/data/mpa-boundaries.geojson) — zone polygons + per-day status +
closure windows for a rolling 7-day forecast.

Two kinds of closed areas (both rendered):
  * Safety Zones (SZALFA..SZGOLF, SZWILCOVE) — the nearshore 3 nm ring. Mostly
    standing status (most "available", Golf/Wilson Cove "always restricted"),
    but can go time-restricted during exercises. Status = the row color.
  * SOAR Operations Areas (SOAR S1..S4) — offshore boxes that carry the actual
    timed daily closures (start/end + altitude). Polygons only appear on the
    day pages that have ops, so geometry is unioned across the fetched days.

Source quirks (see also the parser tests):
  * Pages are on S3/CloudFront: 403 a bare request, 200 with a browser UA +
    Referer; a 403 can also just mean "that day index doesn't exist".
  * The day page is days/<N>.html where N = the target date's day-of-month
    (wraps across month boundaries) — so we VERIFY the rendered "Schedule for
    DD-Mon-YYYY" matches the date we asked for.
  * Schedule times are local Pacific (no TZ suffix) — converted to UTC here.
  * The site is a periodically-regenerated static snapshot; its published
    window + "Updated" stamp are recorded so the UI can show an honest as-of.

Run:
    python pipeline/fetch_sci_closures.py
Output:
    public/data/navy-closures.geojson   (CA region only)
"""
from __future__ import annotations

import json
import re
import sys
import time
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

SCI_BASE = "https://www.scisland.org/schedules/safetyZoneUse/days"
SCI_HEADERS = {
    # The site 403s the pipeline's default UA; a browser UA + referer get 200.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://www.scisland.org/",
}
PACIFIC = ZoneInfo("America/Los_Angeles")
FORECAST_DAYS = 7
INTRA_PAUSE_S = 1.0  # polite gap between page fetches

# Map the site's row background-color to a status. The legend also defines
# striped/black-outline states for sub-24h/scheduled ops; map any unknown
# color to "scheduled" so a future format change degrades safely rather than
# silently reporting "open".
_COLOR_STATUS = {"lightgreen": "open", "green": "open", "red": "restricted"}

_GEO_RE = re.compile(r"areaGEO\.push\((\{.*?\})\);", re.S)
_MAPLABELS_RE = re.compile(r"var\s+mapLabels\s*=\s*(\{.*?\})\s*;", re.S)
_SZ_STATUS_RE = re.compile(r"background-color:\s*([a-zA-Z]+);?'?\s*><td id='(SZ[A-Z]+|WCNAA)'")
_PAGE_DATE_RE = re.compile(r"Schedule for (\d{2}-[A-Za-z]{3}-\d{4})")
_BEGIN_RE = re.compile(r"begin_date\s*=\s*new Date\('([^']+)'\)")
_END_RE = re.compile(r"end_date\s*=\s*new Date\('([^']+)'\)")
_UPDATED_RE = re.compile(r"Updated ([A-Za-z]+ \d{1,2}, \d{4}, \d{1,2}:\d{2})")
_NO_OPS_RE = re.compile(r"No operations scheduled", re.I)
# A SOAR operations row: the area cell (with rowspan) then start/end/altitude.
_OPS_AREA_RE = re.compile(r"id='(SOAR S\d+)'[^>]*rowspan='(\d+)'")
_OPS_WINDOW_RE = re.compile(
    r"<td class='bordered'>(\d{2}-[A-Z]{3}-\d{2} \d{3,4})</td>\s*"
    r"<td class='bordered'>(\d{2}-[A-Z]{3}-\d{2} \d{3,4})</td>\s*"
    r"<td class='bordered'>([^<]+)</td>"
)


# ---- pure parsers (network-free; unit-tested) --------------------------

def parse_geometry(html: str) -> dict[str, dict]:
    """Extract every named polygon from the page's areaGEO pushes.
    Returns {name: {"geometry": <GeoJSON geom>, "centroid": [lon,lat]}}.
    Skips Point markers and empty geometries (e.g. WCNAA)."""
    out: dict[str, dict] = {}
    for blob in _GEO_RE.findall(html):
        try:
            fc = json.loads(blob)
        except json.JSONDecodeError:
            continue
        for feat in fc.get("features", []):
            geom = feat.get("geometry") or {}
            name = (feat.get("properties") or {}).get("NAME")
            if not name or geom.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            if not geom.get("coordinates"):
                continue
            out[name] = {"geometry": geom, "centroid": feat.get("centerpt")}
    return out


def parse_labels(html: str) -> dict[str, str]:
    m = _MAPLABELS_RE.search(html)
    if not m:
        return {}
    try:
        raw = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    return {k: (v.get("label") or k) for k, v in raw.items() if isinstance(v, dict)}


def parse_safety_zone_status(html: str) -> dict[str, str]:
    """{zone_name: status} from the safety-zone table row colors."""
    out: dict[str, str] = {}
    for color, name in _SZ_STATUS_RE.findall(html):
        out[name] = _COLOR_STATUS.get(color.lower(), "scheduled")
    return out


def _parse_pt(stamp: str) -> datetime:
    """'18-JUN-26 1200' (local Pacific) -> aware datetime in PT."""
    dt = datetime.strptime(stamp.strip().title(), "%d-%b-%y %H%M")
    return dt.replace(tzinfo=PACIFIC)


def parse_operations(html: str) -> dict[str, list[dict]]:
    """{area_name: [window,...]} from the Hazardous Operations table.
    Each window: start/end as both local PT display + UTC ISO, plus altitude.
    Empty dict on an all-open day."""
    if _NO_OPS_RE.search(html) and not _OPS_AREA_RE.search(html):
        return {}
    # Position each window under the most recent area marker so a rowspan>1
    # area (multiple windows) is captured, not just the first row.
    areas = [(m.start(), m.group(1)) for m in _OPS_AREA_RE.finditer(html)]
    if not areas:
        return {}
    out: dict[str, list[dict]] = {}
    for m in _OPS_WINDOW_RE.finditer(html):
        owner = None
        for pos, name in areas:
            if pos < m.start():
                owner = name
            else:
                break
        if owner is None:
            continue
        try:
            start_pt, end_pt = _parse_pt(m.group(1)), _parse_pt(m.group(2))
        except ValueError:
            continue
        out.setdefault(owner, []).append({
            "start_local": f"{start_pt:%Y-%m-%d %H:%M} PT",
            "end_local": f"{end_pt:%Y-%m-%d %H:%M} PT",
            "start_utc": start_pt.astimezone(timezone.utc)
                .isoformat(timespec="minutes").replace("+00:00", "Z"),
            "end_utc": end_pt.astimezone(timezone.utc)
                .isoformat(timespec="minutes").replace("+00:00", "Z"),
            "altitude": m.group(3).strip(),
        })
    return out


def parse_page_date(html: str) -> date | None:
    m = _PAGE_DATE_RE.search(html)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).title(), "%d-%b-%Y").date()
    except ValueError:
        return None


def parse_published_window(html: str) -> dict:
    def _iso(rx):
        m = rx.search(html)
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1), "%d %b %Y").date().isoformat()
        except ValueError:
            return None
    upd = _UPDATED_RE.search(html)
    return {
        "start": _iso(_BEGIN_RE),
        "end": _iso(_END_RE),
        "source_updated": upd.group(1) if upd else None,
    }


def parse_closure_page(html: str) -> dict | None:
    """Parse one day page into {date, safety_zones, operations, geometry,
    labels, window}. None if the page has no recognizable schedule date."""
    page_date = parse_page_date(html)
    if page_date is None:
        return None
    return {
        "date": page_date.isoformat(),
        "safety_zones": parse_safety_zone_status(html),
        "operations": parse_operations(html),
        "geometry": parse_geometry(html),
        "labels": parse_labels(html),
        "window": parse_published_window(html),
    }


# ---- network + assembly -------------------------------------------------

def _fetch_day(target: date) -> dict | None:
    """Fetch + parse one forecast day; verify the rendered date matches."""
    url = f"{SCI_BASE}/{target.day}.html"
    r = http_get(url, headers=SCI_HEADERS, timeout=30, retries=3)
    if r is None or r.status_code != 200:
        print(f"  [closures] {target} -> {getattr(r, 'status_code', 'no-response')}, skipping", flush=True)
        return None
    parsed = parse_closure_page(r.text)
    if parsed is None:
        print(f"  [closures] {target} -> unparseable page, skipping", flush=True)
        return None
    if parsed["date"] != target.isoformat():
        # Day-of-month index resolved to the wrong month (rollover) — drop it
        # rather than mislabel a closure.
        print(f"  [closures] {target} -> page shows {parsed['date']}, skipping", flush=True)
        return None
    return parsed


def build_closures(today: date | None = None) -> dict:
    """Fetch the rolling FORECAST_DAYS window and assemble the GeoJSON."""
    today = today or datetime.now(PACIFIC).date()
    dates = [today + timedelta(days=i) for i in range(FORECAST_DAYS)]

    geometry: dict[str, dict] = {}   # name -> {geometry, centroid} (unioned)
    labels: dict[str, str] = {}
    status_by_date: dict[str, dict] = {}  # name -> {date_iso -> {status, windows}}
    window_meta: dict = {}

    for i, target in enumerate(dates):
        if i:
            time.sleep(INTRA_PAUSE_S)
        page = _fetch_day(target)
        if page is None:
            continue
        geometry.update(page["geometry"])     # SZ on every day, SOAR on ops days
        labels.update(page["labels"])
        window_meta = window_meta or page["window"]
        d = page["date"]
        # Safety zones: status from row color.
        for name, status in page["safety_zones"].items():
            status_by_date.setdefault(name, {})[d] = {"status": status, "windows": []}
        # Operations areas: closed (with windows) on ops days, else open.
        for name in [n for n in geometry if n.startswith("SOAR")]:
            wins = page["operations"].get(name, [])
            status_by_date.setdefault(name, {})[d] = {
                "status": "restricted" if wins else "open",
                "windows": wins,
            }

    date_isos = [d.isoformat() for d in dates]
    features = []
    for name, geo in geometry.items():
        kind = "operations_area" if name.startswith("SOAR") else "safety_zone"
        per_date = status_by_date.get(name, {})
        # Fill any missing day with "unknown" so the UI never silently implies open.
        full = {di: per_date.get(di, {"status": "unknown", "windows": []}) for di in date_isos}
        features.append({
            "type": "Feature",
            "geometry": geo["geometry"],
            "properties": {
                "id": name,
                "label": labels.get(name, name),
                "kind": kind,
                "centroid": geo.get("centroid"),
                "statusByDate": full,
            },
        })
    features.sort(key=lambda f: (f["properties"]["kind"], f["properties"]["id"]))

    return {
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "scisland.org",
        "source_updated": window_meta.get("source_updated"),
        "published_window": {"start": window_meta.get("start"), "end": window_meta.get("end")},
        "tz": "America/Los_Angeles",
        "dates": date_isos,
        "features": features,
    }


def main() -> int:
    region = active_region()
    out_dir = region.data_output_dir(ROOT)
    fc = build_closures()
    n_feat = len(fc["features"])
    n_geo = sum(1 for f in fc["features"] if f["geometry"].get("coordinates"))
    closed_today = [
        f["properties"]["id"]
        for f in fc["features"]
        if fc["dates"] and f["properties"]["statusByDate"]
        .get(fc["dates"][0], {}).get("status") == "restricted"
    ]
    print(f"[closures] {n_feat} zones ({n_geo} with geometry); "
          f"closed today ({fc['dates'][0] if fc['dates'] else '?'}): "
          f"{closed_today or 'none'}", flush=True)
    if n_feat == 0:
        print("[closures] no zones parsed — leaving previous file untouched", flush=True)
        return 1
    out_path = out_dir / "navy-closures.geojson"
    out_path.write_text(json.dumps(fc, indent=2) + "\n", encoding="utf-8")
    print(f"[closures] wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
