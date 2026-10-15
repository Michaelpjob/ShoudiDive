"""This season against last year, week by week.

  buoy_season()   weekly mean water temperature at each trend buoy: this year,
                  last year, and the low-high range of the ten years before
                  last (NDBC historical stdmet files). Past years never change,
                  so they are cached in season.json and fetched once; this
                  year's monthly files are refetched when the cache is stale,
                  and the last 45 days come from the realtime file.
  fleet_season()  weekly hours the AIS-tracked sport fleet spent stopped
                  offshore, per zone group, this year against last year
                  (public/fleet/data.json; weeks without AIS coverage are
                  gaps, not zeros).
  zone_vs_normal  zone water from today's satellite map against the NOAA OISST
                  1991-2020 average for the month (public/data/sst_climo.png).
"""
from __future__ import annotations

import gzip
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import sources
from .trends import TREND_BUOYS
from .zones import GROUPS, ZONES, zone_mean, zone_of

NDBC = "https://www.ndbc.noaa.gov/data"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
RANGE_YEARS = 10           # the range band covers the ten years before last year
MIN_DAYS_PER_WEEK = 4      # a weekly mean needs at least this many daily means
REFRESH_DAYS = 3           # refetch this year's monthly files this often
COVERED_DAYS_PER_WEEK = 3  # fleet: a week counts when stops were heard on this many days


def parse_stdmet(text: str) -> dict[str, float]:
    """NDBC stdmet text (historical, monthly or realtime) -> daily mean water
    temperature in °C keyed by ISO date. Missing values are MM or 99/999."""
    acc: dict[str, list[float]] = defaultdict(list)
    for ln in text.splitlines():
        if not ln or ln.startswith("#"):
            continue
        r = ln.split()
        if len(r) < 15 or not r[0].isdigit():
            continue
        y = int(r[0])
        y = y + 1900 if y < 100 else y
        try:
            t = float(r[14])
        except ValueError:
            continue
        if t >= 99 or t < -5:
            continue
        acc[f"{y:04d}-{int(r[1]):02d}-{int(r[2]):02d}"].append(t)
    return {d: sum(v) / len(v) for d, v in acc.items()}


def weekly(daily: dict[str, float], year: int) -> dict[int, float]:
    """{ISO week number: mean °F} for one ISO year, weeks with enough days."""
    acc: dict[int, list[float]] = defaultdict(list)
    for d, c in daily.items():
        iy, wk, _ = date.fromisoformat(d).isocalendar()
        if iy == year and wk <= 52:
            acc[wk].append(c * 9 / 5 + 32)
    return {wk: round(sum(v) / len(v), 1) for wk, v in acc.items() if len(v) >= MIN_DAYS_PER_WEEK}


def week_monday(year: int, wk: int) -> str:
    return date.fromisocalendar(year, wk, 1).isoformat()


def _gz_text(url: str) -> str | None:
    try:
        r = sources._get(url)
    except Exception:  # noqa: BLE001 - a missing month or year is a gap, not a failure
        return None
    try:
        return gzip.decompress(r.content).decode("ascii", "replace")
    except OSError:
        return r.text


def _year_daily(station: str, year: int) -> dict[str, float]:
    text = _gz_text(f"{NDBC}/historical/stdmet/{station}h{year}.txt.gz")
    return parse_stdmet(text) if text else {}


def _this_year_daily(station: str, year: int, today: date) -> dict[str, float]:
    out: dict[str, float] = {}
    for m in range(1, today.month + 1):
        text = _gz_text(f"{NDBC}/stdmet/{MONTHS[m - 1]}/{station}{m}{year}.txt.gz")
        if text:
            out.update(parse_stdmet(text))
    return out


def buoy_season(today: date, cache: dict | None, recent: dict[str, list[dict]] | None = None,
                now: datetime | None = None) -> dict:
    """Weekly water temperature per buoy: this year, last year, the range.

    `cache` is the previous season.json (or None); `recent` maps station ->
    the realtime daily rows from trends.buoy_history (date, temp_f)."""
    year, last = today.year, today.year - 1
    span = (last - RANGE_YEARS, last - 1)
    cached = {b["id"]: b for b in (cache or {}).get("buoys", [])}
    fresh_cut = (now or sources.utcnow()) - timedelta(days=REFRESH_DAYS)
    out = []
    for station, name in TREND_BUOYS:
        c = cached.get(station) or {}
        if c.get("year") == year and c.get("range_years") == list(span) and c.get("past"):
            past = c["past"]
        else:
            per_year = {y: weekly(_year_daily(station, y), y) for y in range(span[0], last + 1)}
            past = {"last": {str(k): v for k, v in per_year[last].items()}, "range": {}}
            for wk in range(1, 53):
                vals = [per_year[y][wk] for y in range(span[0], span[1] + 1) if wk in per_year[y]]
                if len(vals) >= RANGE_YEARS // 2:
                    past["range"][str(wk)] = [min(vals), max(vals), len(vals)]
        stamp = c.get("this_fetched")
        if c.get("year") == year and c.get("this_daily") and stamp and sources.parse_iso(stamp) > fresh_cut:
            this_daily = dict(c["this_daily"])
        else:
            this_daily = _this_year_daily(station, year, today)
            stamp = (now or sources.utcnow()).strftime("%Y-%m-%dT%H:%MZ")
        archived = {d: v for d, v in this_daily.items()}
        for row in (recent or {}).get(station, []):
            if row.get("temp_f") is not None:
                this_daily[row["date"]] = (row["temp_f"] - 32) * 5 / 9
        this_w = weekly(this_daily, year)
        weeks = []
        for wk in range(1, 53):
            lo_hi = past["range"].get(str(wk))
            weeks.append({"w": week_monday(year, wk), "this": this_w.get(wk), "last": past["last"].get(str(wk)),
                          "lo": lo_hi[0] if lo_hi else None, "hi": lo_hi[1] if lo_hi else None})
        out.append({"id": station, "name": name, "year": year, "range_years": list(span), "past": past,
                    "this_fetched": stamp, "this_daily": {d: round(v, 2) for d, v in sorted(archived.items())},
                    "weeks": weeks})
    return {"year": year, "last_year": last, "range_label": f"{span[0]}-{span[1]}", "buoys": out}


def fleet_season(fleet: dict | None, today: date) -> dict:
    """Weekly hours stopped offshore (nearshore band excluded) per zone group
    and per zone, this ISO year against last."""
    year, last = today.year, today.year - 1
    if not fleet:
        return {}
    hours: dict[tuple, float] = defaultdict(float)
    heard: dict[tuple[int, int], set] = defaultdict(set)
    for st in fleet.get("stops", []):
        d = date.fromisoformat(st[1])
        iy, wk, _ = d.isocalendar()
        if iy not in (year, last) or wk > 52:
            continue
        heard[(iy, wk)].add(st[1])
        if st[-1]:
            continue
        z = zone_of(st[2], st[3])
        if z is None:
            continue
        h = st[4] / 60
        hours[(iy, wk, "zone", z)] += h
        hours[(iy, wk, "group", ZONES[z][1])] += h
    covered = {k for k, days in heard.items() if len(days) >= COVERED_DAYS_PER_WEEK}

    def series(kind, key):
        rows = []
        for wk in range(1, 53):
            this = round(hours.get((year, wk, kind, key), 0.0), 1) if (year, wk) in covered else None
            prev = round(hours.get((last, wk, kind, key), 0.0), 1) if (last, wk) in covered else None
            rows.append({"w": week_monday(year, wk), "this": this, "last": prev})
        return rows

    gaps = []
    for y in (year, last):
        miss = [wk for wk in range(1, 53) if (y, wk) not in covered and date.fromisocalendar(y, wk, 1) <= today]
        if miss:
            gaps.append({"year": y, "weeks": len(miss)})
    zones = []
    for i, z in enumerate(ZONES):
        rows = series("zone", i)
        zones.append({"id": i, "name": z[0], "this": [r["this"] for r in rows], "last": [r["last"] for r in rows]})
    return {"year": year, "last_year": last, "weeks": [week_monday(year, wk) for wk in range(1, 53)],
            "groups": [{"key": k, "label": lbl, "weeks": series("group", k)} for k, lbl in GROUPS],
            "zones": zones, "gaps": gaps, "boats": len(fleet.get("boats", []))}


def recent_vs_last_year(fs: dict, zone_id: int, today: date, n: int = 4) -> dict | None:
    """Hours in the last n covered weeks this year against the same weeks last
    year (both covered), for one zone."""
    z = next((r for r in fs.get("zones", []) if r["id"] == zone_id), None)
    if not z:
        return None
    cur_wk = today.isocalendar()[1]
    idx = [k for k in range(min(cur_wk, 52) - 1, -1, -1) if z["this"][k] is not None and z["last"][k] is not None][:n]
    if not idx:
        return None
    return {"weeks": len(idx), "this_h": round(sum(z["this"][k] for k in idx), 1), "last_h": round(sum(z["last"][k] for k in idx), 1)}


def load_climo(manifest: dict, data_dir: Path | None = None):
    """The OISST 1991-2020 monthly average grid on our SST grid, or None.
    Encoded with the same linear range as the SST layer."""
    from lib.decode import decode_linear_png

    from .fish import Grid
    data_dir = data_dir or sources.DATA
    path = data_dir / "sst_climo.png"
    L = (manifest.get("layers") or {}).get("sst") or {}
    try:
        meta = json.loads((data_dir / "climo_meta.json").read_text(encoding="utf-8"))
        lo, hi = L["range"]
    except (OSError, ValueError, KeyError):
        return None, None
    if not path.exists():
        return None, None
    return Grid(decode_linear_png(path, lo, hi), meta.get("bbox") or manifest["bbox"]), meta


def zone_vs_normal(cards: list[dict], climo, meta: dict | None) -> None:
    """Add vs_normal_f to each zone card: today's zone water minus the month's
    1991-2020 average over the same cells."""
    if climo is None or not meta or meta.get("sst_climo_method") != "oisst_1991-2020_monthly":
        return
    month = MONTHS[int(meta.get("month", 1)) - 1]
    for c in cards:
        if c.get("temp_f") is None:
            continue
        m, cov = zone_mean(climo, tuple(c["center"]), c["radius_nm"], lambda v: v * 9 / 5 + 32)
        if m is not None and cov >= 0.3:
            c["vs_normal_f"] = round(c["temp_f"] - m, 1)
            c["normal_f"] = round(m, 1)
            c["normal_label"] = f"{month} average, 1991-2020"
