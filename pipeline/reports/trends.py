"""Trend sources: what changed over days and weeks, from data we can read now.

  buoy_history()      NDBC/CDIP realtime files carry ~45 days: daily water
                      temperature, wave height and period, wind.
  sst_history()       our 7 daily SST grids (public/data/sst/history/d-6..d0).
  glider_series()     one Spray glider profile per day near San Diego:
                      thermocline depth and temperature at 60 ft.
  vis_series()        dive-shop visibility posts, one per post date.
  enso_weekly()       CPC weekly Nino-3.4 anomaly (OISST).
  archive()           our own daily zone snapshots (public/data/reports/
                      trends.json), which lengthen every trend from here on.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import sources

# --- Buoys -------------------------------------------------------------------

TREND_BUOYS = [
    ("46254", "Scripps Nearshore (La Jolla)"), ("46232", "Point Loma South"),
    ("46086", "San Clemente Basin"), ("46047", "Tanner Bank"), ("46253", "San Pedro South"),
]


def parse_ndbc_history(text: str) -> list[dict]:
    """All rows of a realtime2 .txt file -> per-UTC-day means/maxima."""
    days: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ln in text.splitlines():
        if not ln or ln.startswith("#"):
            continue
        r = ln.split()
        d = f"{r[0]}-{r[1]}-{r[2]}"
        for key, idx in (("wspd", 6), ("wvht", 8), ("dpd", 9), ("wtmp", 14)):
            v = r[idx] if idx < len(r) else "MM"
            if v != "MM":
                days[d][key].append(float(v))
    out = []
    for d in sorted(days):
        v = days[d]
        row = {"date": d}
        if v["wtmp"]:
            row["temp_f"] = round(sum(v["wtmp"]) / len(v["wtmp"]) * 9 / 5 + 32, 1)
        if v["wvht"]:
            row["wave_ft"] = round(sum(v["wvht"]) / len(v["wvht"]) * 3.28084, 1)
            row["wave_max_ft"] = round(max(v["wvht"]) * 3.28084, 1)
        if v["dpd"]:
            row["period_s"] = round(sorted(v["dpd"])[len(v["dpd"]) // 2])
        if v["wspd"]:
            row["wind_max_kt"] = round(max(v["wspd"]) * 1.94384)
            row["wind_mean_kt"] = round(sum(v["wspd"]) / len(v["wspd"]) * 1.94384)
        out.append(row)
    return out


def buoy_history(station: str, name: str, now: datetime | None = None) -> dict:
    url = f"https://www.ndbc.noaa.gov/data/realtime2/{station}.txt"
    label = f"Buoy {station} {name}, last 45 days"

    def run():
        rows = parse_ndbc_history(sources._get(url).text)
        if not rows:
            raise RuntimeError("no rows")
        return sources.source(f"hist_{station}", label, f"https://www.ndbc.noaa.gov/station_page.php?station={station}",
                              ok=True, time=sources.parse_iso(rows[-1]["date"] + "T00:00:00Z"),
                              data={"name": name, "days": rows}, now=now)
    return sources._guard(f"hist_{station}", label, url, run)


# --- SST grids -----------------------------------------------------------------

def sst_history(data_dir: Path | None = None) -> list[tuple[str, object]]:
    """[(date, fish.Grid)] for the 7 daily history grids, oldest first."""
    from lib.decode import decode_linear_png

    from .fish import Grid
    data_dir = data_dir or sources.DATA
    try:
        summ = json.loads((data_dir / "sst" / "summary.json").read_text(encoding="utf-8"))
        man = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    lo, hi = summ["range"]
    out = []
    for d in sorted(summ.get("days", []), key=lambda x: x["offset"]):
        p = data_dir / "sst" / "history" / Path(d["url"]).name
        if p.exists():
            out.append((d["date"], Grid(decode_linear_png(p, lo, hi), man["bbox"])))
    return out


# --- Glider thermocline, day by day ----------------------------------------------

def glider_series(now: datetime | None = None, days: int = 10, max_km: float = 160) -> dict:
    label = f"Spray glider profiles, one a day for {days} days"
    G = sources.GLIDER_ERDDAP

    def run():
        now_ = now or sources.utcnow()
        since = (now_ - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        listing = sources._get(f"{G}/allDatasets.csv?datasetID,maxTime,minLatitude,maxLatitude,minLongitude,maxLongitude&maxTime%3E={since}").text
        per_day: dict[str, tuple] = {}
        for line in listing.splitlines()[2:]:
            parts = line.split(",")
            try:
                la0, la1, lo0, lo1 = map(float, parts[2:6])
            except ValueError:
                continue
            if not (la1 > 31.5 and la0 < 34.5 and lo0 < -116.8 and lo1 > -121.5):
                continue
            ds = parts[0]
            pos = sources._get(f"{G}/{ds}.csv?profile_id,time,latitude,longitude&time%3E={since}&distinct()").text
            for r in pos.splitlines()[2:]:
                f = r.split(",")
                if len(f) != 4:
                    continue
                km = sources._km((float(f[2]), float(f[3])), sources.SAN_DIEGO)
                day = f[1][:10]
                if km <= max_km and (day not in per_day or km < per_day[day][0]):
                    per_day[day] = (km, ds, f[0], f[1], float(f[2]), float(f[3]))
        series = []
        for day in sorted(per_day):
            km, ds, pid, t, la, lo = per_day[day]
            prof = sources._get(f"{G}/{ds}.csv?depth,temperature&profile_id={pid}").text
            samples = []
            for r in prof.splitlines()[2:]:
                try:
                    dep, tmp = r.split(",")
                    samples.append((float(dep), float(tmp)))
                except ValueError:
                    continue
            th = sources.thermocline_from_profile(samples)
            if th:
                series.append({"date": day, "glider": ds.split("-")[0], "km": round(km),
                               "thermocline_ft": round(th["thermocline_m"] * 3.28084),
                               "mixed_layer_ft": round(th["mixed_layer_m"] * 3.28084) if th.get("mixed_layer_m") is not None else None,
                               "surface_f": round(th["surface_c"] * 9 / 5 + 32, 1),
                               "t60_f": round(th["t_18m_c"] * 9 / 5 + 32, 1) if th["t_18m_c"] is not None else None,
                               "t100_f": round(th["t_30m_c"] * 9 / 5 + 32, 1) if th["t_30m_c"] is not None else None})
        if not series:
            raise RuntimeError("no glider profiles near San Diego")
        return sources.source("glider_series", label, "https://gliders.ioos.us/erddap/", ok=True,
                              time=sources.parse_iso(series[-1]["date"] + "T00:00:00Z"), data=series, now=now)
    return sources._guard("glider_series", label, G, run)


# --- Dive-shop visibility over time ---------------------------------------------

_RANGE = re.compile(r"Vis(?:ibility)?:?\s*(\d+)\s*(?:-|to|–)?\s*(\d+)?\+?\s*(?:ft|feet|')", re.I)


def _prose_range(sentence: str) -> tuple[float, float] | None:
    from validation.ingest.southcoastdivers import _BOUND_AFTER, _LEAD_NOT_NOW, _NOT_NOW, _NUM_THEN_VIZ, _VIZ_THEN_NUM
    found = sorted(list(_VIZ_THEN_NUM.finditer(sentence)) + list(_NUM_THEN_VIZ.finditer(sentence)), key=lambda m: m.start("lo"))
    for m in found:
        if (_NOT_NOW.search(m.group("gap")) or _BOUND_AFTER.search(sentence[m.end():m.end() + 14])
                or _LEAD_NOT_NOW.search(sentence[max(0, m.start() - 30):m.start()])):
            continue
        lo = float(m.group("lo"))
        hi = float(m.group("hi")) if m.group("hi") else lo
        return (min(lo, hi), max(lo, hi))
    return None


def vis_series(days: int = 45, today: date | None = None) -> dict:
    """One point per (shop, site, post date): low and high of the posted range."""
    today = today or sources.utcnow().date()
    cutoff = today - timedelta(days=days)
    seen: dict[tuple, dict] = {}
    try:
        lines = sources.OBS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    first_seen: dict[str, datetime] = {}
    rows = []
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not str(o.get("source", "")).startswith("dive-shop"):
            continue
        ts = sources.parse_iso(o.get("timestamp_utc"))
        url = o.get("source_url") or ""
        if ts and (url not in first_seen or ts < first_seen[url]):
            first_seen[url] = ts
        rows.append(o)
    for o in rows:
        url = o.get("source_url") or ""
        if url not in first_seen:
            continue
        m = _RANGE.search(o.get("raw_excerpt") or "")
        if m:
            lo = float(m.group(1))
            hi = float(m.group(2)) if m.group(2) else lo
        elif o.get("extraction_method") == "regex" and o.get("raw_excerpt") and o.get("observed_secchi_ft") is not None:
            # Prose posts (South Coast Divers): the range the scraper read from
            # the same sentence, else the stated midpoint.
            r = _prose_range(o["raw_excerpt"])
            lo, hi = r if r else (o["observed_secchi_ft"], o["observed_secchi_ft"])
        else:
            continue
        d = sources.post_date(url, first_seen[url])
        if d < cutoff:
            continue
        key = (o["source"], o.get("spot_name"), d.isoformat())
        seen[key] = {"source": o["source"], "site": o.get("spot_name"), "date": d.isoformat(), "lo_ft": lo, "hi_ft": hi}
    # One series per shop: its posts cover its home waters under several site
    # labels; the newest post on a date wins.
    per_day: dict[tuple, dict] = {}
    for v in seen.values():
        per_day[(v["source"], v["date"])] = v
    by_series: dict[str, list] = defaultdict(list)
    for v in per_day.values():
        by_series[v["source"]].append(v)
    # A series whose range never changes is a page being re-read, not a report.
    out = {}
    for k, pts in by_series.items():
        pts.sort(key=lambda p: p["date"])
        if len(pts) >= 3 and len({(p["lo_ft"], p["hi_ft"]) for p in pts}) == 1 and len(pts) == len({p["date"] for p in pts}):
            if (date.fromisoformat(pts[-1]["date"]) - date.fromisoformat(pts[0]["date"])).days > 14:
                continue
        out[k] = pts
    return out


# --- ENSO weekly -------------------------------------------------------------------

def enso_weekly(now: datetime | None = None, weeks: int = 26) -> dict:
    url = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"
    label = "NOAA CPC weekly Nino-3.4 anomaly (OISST, 1991-2020 base)"

    def run():
        rows = []
        for ln in sources._get(url).text.splitlines():
            m = re.match(r"\s*(\d{2}[A-Z]{3}\d{4})\s+([\d.]+)\s*(-?[\d.]+)\s+([\d.]+)\s*(-?[\d.]+)\s+([\d.]+)\s*(-?[\d.]+)\s+([\d.]+)\s*(-?[\d.]+)", ln)
            if m:
                d = datetime.strptime(m.group(1), "%d%b%Y").date()
                rows.append({"date": d.isoformat(), "nino12": float(m.group(3)), "nino34": float(m.group(7))})
        rows = rows[-weeks:]
        if not rows:
            raise RuntimeError("no rows")
        return sources.source("enso_weekly", label, url, ok=True,
                              time=sources.parse_iso(rows[-1]["date"] + "T00:00:00Z"), data=rows, now=now)
    return sources._guard("enso_weekly", label, url, run)


# --- Our own archive ---------------------------------------------------------------

def update_archive(path: Path, today: str, snapshot: dict, keep_days: int = 400) -> dict:
    """Append (or replace) today's zone snapshot in trends.json and return it."""
    try:
        arch = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        arch = {"days": []}
    days = [d for d in arch.get("days", []) if d.get("date") != today]
    days.append({"date": today, **snapshot})
    days.sort(key=lambda d: d["date"])
    arch["days"] = days[-keep_days:]
    arch["updated"] = sources.iso(datetime.now(timezone.utc))
    return arch
