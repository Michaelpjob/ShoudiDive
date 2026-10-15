"""Fetchers. Every input becomes a Source record carrying its own time, so the
report can say how old each number is and treat stale inputs as missing.

Network sources are official, free, and machine-readable: api.weather.gov
(alerts, CWF, SRF), NHC's offshore text, NDBC/CDIP buoy realtime files,
NOAA CO-OPS water temperature, the Iowa Environmental Mesonet daily climate
for the KSAN rain gauge, and CPC's ENSO discussion. Local sources are files
our own pipeline already publishes under public/data/.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from lib.http import http_get

from . import nws_text

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "public" / "data"
OBS = ROOT / "pipeline" / "validation" / "data" / "observations.jsonl"
TIMEOUT = 25


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if dt else None


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def source(sid: str, label: str, url: str | None, *, ok: bool, time: datetime | None = None,
           data: Any = None, error: str | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    age = round((now - time).total_seconds() / 3600, 1) if time else None
    return {"id": sid, "label": label, "url": url, "ok": ok, "time_utc": iso(time),
            "age_h": age, "error": error, "data": data}


def _guard(sid: str, label: str, url: str | None, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001  one bad feed must never stop the report
        return source(sid, label, url, ok=False, error=f"{type(e).__name__}: {e}")


def _get(url: str, **kw):
    r = http_get(url, timeout=TIMEOUT, **kw)
    if r is None or r.status_code != 200:
        raise RuntimeError(f"HTTP {getattr(r, 'status_code', 'no response')}")
    return r


# --- NWS ---------------------------------------------------------------------

_TZ_OFFSETS = {"PDT": -7, "PST": -8, "MDT": -6, "MST": -7, "EDT": -4, "EST": -5, "UTC": 0, "GMT": 0}


def parse_issued(line: str | None) -> datetime | None:
    """'201 AM PDT Thu Oct 15 2026' -> aware UTC datetime."""
    if not line:
        return None
    m = re.match(r"(\d{1,2})(\d{2}) ([AP])M ([A-Z]{3}) \w{3} (\w{3}) (\d{1,2}) (\d{4})", line)
    if not m:
        return None
    hh, mm, ap, tz, mon, dd, yyyy = m.groups()
    h = int(hh) % 12 + (12 if ap == "P" else 0)
    local = datetime.strptime(f"{yyyy} {mon} {dd} {h}:{mm}", "%Y %b %d %H:%M")
    return (local - timedelta(hours=_TZ_OFFSETS.get(tz, 0))).replace(tzinfo=timezone.utc)


def alerts(zones: list[str], now: datetime | None = None) -> dict:
    url = "https://api.weather.gov/alerts/active?zone=" + ",".join(zones)

    def run():
        r = _get(url, headers={"Accept": "application/geo+json"})
        return source("alerts", "NWS active alerts", url, ok=True, time=now or utcnow(),
                      data=normalize_alerts(r.json()), now=now)
    return _guard("alerts", "NWS active alerts", url, run)


def normalize_alerts(geojson: dict) -> list[dict]:
    out = []
    for f in geojson.get("features", []):
        p = f.get("properties", {})
        out.append({
            "event": p.get("event"),
            "headline": p.get("headline"),
            "onset": p.get("onset") or p.get("effective"),
            "ends": p.get("ends") or p.get("expires"),
            "zones": (p.get("geocode") or {}).get("UGC", []),
            "area": p.get("areaDesc"),
            "description": (p.get("description") or "")[:900] or None,
            "instruction": (p.get("instruction") or "")[:500] or None,
        })
    return out


def nws_product(ptype: str, office: str, now: datetime | None = None) -> dict:
    sid = f"{ptype.lower()}_{office.lower()}"
    list_url = f"https://api.weather.gov/products/types/{ptype}/locations/{office}"
    label = {"CWF": "NWS Coastal Waters Forecast", "SRF": "NWS Surf Zone Forecast"}.get(ptype, ptype) + f" ({office})"

    def run():
        listing = _get(list_url, headers={"Accept": "application/ld+json"}).json()
        latest = listing["@graph"][0]
        prod = _get(f"https://api.weather.gov/products/{latest['id']}").json()
        text = prod["productText"]
        return source(sid, label, f"https://api.weather.gov/products/{latest['id']}", ok=True,
                      time=parse_iso(prod.get("issuanceTime")),
                      data={"text": text, "issued_line": nws_text.issued_line(text)}, now=now)
    return _guard(sid, label, list_url, run)


def nhc_offshore(now: datetime | None = None) -> dict:
    url = "https://www.nhc.noaa.gov/text/MIAOFFPZ7.shtml"
    label = "NHC Offshore Waters Forecast (Mexico, OFFPZ7)"

    def run():
        t = _get(url).text
        m = re.search(r"<pre>(.*?)</pre>", t, re.S)
        text = html.unescape(m.group(1) if m else t)
        line = nws_text.issued_line(text)
        return source("off_nhc", label, url, ok=True, time=parse_issued(line),
                      data={"text": text, "issued_line": line}, now=now)
    return _guard("off_nhc", label, url, run)


# --- Buoys -------------------------------------------------------------------

def _mm(v: str) -> float | None:
    return None if v in ("MM", "N/A", "") else float(v)


FILL_WINDOW_S = 90 * 60  # NDBC stations log wind every 10 min but waves every 30


def _ndbc_rows(text: str) -> list[tuple[datetime, list[str]]]:
    out = []
    for ln in text.splitlines():
        if not ln or ln.startswith("#"):
            continue
        r = ln.split()
        out.append((datetime(int(r[0]), int(r[1]), int(r[2]), int(r[3]), int(r[4]), tzinfo=timezone.utc), r))
    return out


def _fill(rows, cols: dict[str, int], text_cols: frozenset = frozenset()) -> dict | None:
    """Newest row's time; each field from the newest row within 90 minutes
    that has it (MM = missing)."""
    if not rows:
        return None
    t0 = rows[0][0]
    rec: dict = {"time": t0}
    for name, idx in cols.items():
        rec[name] = None
        for t, r in rows:
            if (t0 - t).total_seconds() > FILL_WINDOW_S:
                break
            v = r[idx] if idx < len(r) else "MM"
            if v not in ("MM", "N/A", ""):
                rec[name] = v if name in text_cols else float(v)
                break
    return rec


def parse_ndbc_txt(text: str) -> dict | None:
    return _fill(_ndbc_rows(text), {"wdir": 5, "wspd_ms": 6, "gst_ms": 7, "wvht_m": 8, "dpd_s": 9,
                                    "apd_s": 10, "mwd": 11, "wtmp_c": 14})


def parse_ndbc_spec(text: str) -> dict | None:
    return _fill(_ndbc_rows(text), {"wvht_m": 5, "swh_m": 6, "swp_s": 7, "wwh_m": 8, "wwp_s": 9, "swd": 10, "wwd": 11},
                 text_cols=frozenset({"swd", "wwd"}))


def buoy(station: str, name: str, now: datetime | None = None) -> dict:
    base = f"https://www.ndbc.noaa.gov/data/realtime2/{station}"
    label = f"Buoy {station} {name}"

    def run():
        std = parse_ndbc_txt(_get(base + ".txt").text)
        try:
            spec = parse_ndbc_spec(_get(base + ".spec").text)
        except Exception:  # noqa: BLE001  some stations have no spectral file
            spec = None
        if not std:
            raise RuntimeError("no rows")
        return source(f"buoy_{station}", label, f"https://www.ndbc.noaa.gov/station_page.php?station={station}",
                      ok=True, time=std["time"], data=buoy_record(std, spec), now=now)
    return _guard(f"buoy_{station}", label, base + ".txt", run)


def buoy_record(std: dict, spec: dict | None) -> dict:
    rec = {k: v for k, v in std.items() if k != "time"}
    if spec and abs((spec["time"] - std["time"]).total_seconds()) < 3 * 3600:
        rec.update({k: v for k, v in spec.items() if k not in ("time", "wvht_m")})
    return rec


def coops_water_temp(station: str, name: str, now: datetime | None = None) -> dict:
    url = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=water_temperature"
           f"&station={station}&date=latest&units=english&time_zone=gmt&format=json&application=shouldidive")

    def run():
        d = _get(url).json()["data"][0]
        t = datetime.strptime(d["t"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        return source(f"coops_{station}", f"NOAA CO-OPS {name}",
                      f"https://tidesandcurrents.noaa.gov/stationhome.html?id={station}",
                      ok=True, time=t, data={"temp_f": float(d["v"])}, now=now)
    return _guard(f"coops_{station}", f"NOAA CO-OPS {name}", url, run)


# --- Rain, ENSO --------------------------------------------------------------

def rain_72h(station: str, network: str, name: str, today_local: date, now: datetime | None = None) -> dict:
    """Sum the gauge's daily precipitation over the last three local days
    (today so far, yesterday, the day before). Trace counts as zero."""
    days = [today_local - timedelta(days=i) for i in range(3)]
    months = sorted({(d.year, d.month) for d in days})
    label = f"Rain gauge, {name} (IEM daily climate)"
    url0 = f"https://mesonet.agron.iastate.edu/api/1/daily.json?station={station}&network={network}"

    def run():
        by_date: dict[str, float | None] = {}
        for y, m in months:
            for row in _get(f"{url0}&year={y}&month={m}").json().get("data", []):
                by_date[row["date"]] = row.get("precip")
        vals = [by_date.get(d.isoformat()) for d in days]
        known = [v for v in vals if v is not None]
        if not known:
            raise RuntimeError("no precipitation values for the window")
        total = round(sum(v for v in known if v >= 0.001), 2)
        return source("rain", label, f"https://mesonet.agron.iastate.edu/sites/site.php?station={station}&network={network}",
                      ok=True, time=now or utcnow(),
                      data={"inches": total, "days": [d.isoformat() for d in days], "missing_days": vals.count(None)},
                      now=now)
    return _guard("rain", label, url0, run)


def enso(now: datetime | None = None) -> dict:
    url = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml"
    label = "NOAA CPC ENSO diagnostic discussion"

    def run():
        t = html.unescape(re.sub(r"<[^>]+>", " ", _get(url).text))
        t = re.sub(r"\s+", " ", t)
        status = re.search(r"ENSO Alert System Status:\s*([A-Za-zñ ]+?)\s+Synopsis", t)
        syn = re.search(r"Synopsis:\s*(.+?\.)\s", t)
        n34 = re.search(r"([+-]\d\.\d)\s*°?C in Niño-3\.4", t)
        issued = re.search(r"(\d{1,2} (?:January|February|March|April|May|June|July|August|September|October|November|December) \d{4})", t)
        when = datetime.strptime(issued.group(1), "%d %B %Y").replace(tzinfo=timezone.utc) if issued else None
        return source("enso", label, url, ok=bool(status), time=when,
                      data={"status": status.group(1).strip() if status else None,
                            "synopsis": syn.group(1).strip() if syn else None,
                            "nino34_c": float(n34.group(1)) if n34 else None}, now=now)
    return _guard("enso", label, url, run)


# --- Observed thermocline: Spray gliders (IOOS Glider DAC, near real time) ----

GLIDER_ERDDAP = "https://gliders.ioos.us/erddap/tabledap"
SAN_DIEGO = (32.72, -117.23)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math
    dy = (a[0] - b[0]) * 111.0
    dx = (a[1] - b[1]) * 111.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def thermocline_from_profile(rows: list[tuple[float, float]]) -> dict | None:
    """(depth_m, temp_c) samples from one profile -> surface temp, the depth of
    the sharpest drop in the top 100 m, and temperatures at diver depths."""
    pts = sorted((d, t) for d, t in rows if d is not None and t is not None and 0 <= d <= 150)
    if len(pts) < 10 or pts[0][0] > 5:
        return None
    # 2 m bins, then the steepest drop between neighbouring bins.
    bins: dict[int, list[float]] = {}
    for d, t in pts:
        bins.setdefault(int(d // 2), []).append(t)
    prof = sorted((k * 2 + 1, sum(v) / len(v)) for k, v in bins.items())

    def at(depth_m: float) -> float | None:
        for (d0, t0), (d1, t1) in zip(prof, prof[1:]):
            if d0 <= depth_m <= d1:
                return t0 + (t1 - t0) * (depth_m - d0) / (d1 - d0)
        return None
    grads = [((d0 + d1) / 2, (t0 - t1) / (d1 - d0)) for (d0, t0), (d1, t1) in zip(prof, prof[1:]) if d1 <= 100]
    if not grads:
        return None
    zc, g = max(grads, key=lambda x: x[1])
    surface = sum(t for d, t in prof[:2]) / min(2, len(prof))
    # Mixed layer: first depth where the water is 1.1 degC (2 degF) colder than the surface.
    mld = next((d for d, t in prof if t <= surface - 1.1), None)
    return {"surface_c": round(surface, 2), "thermocline_m": round(zc, 1), "max_drop_c_per_m": round(g, 3),
            "mixed_layer_m": round(mld, 1) if mld is not None else None,
            "t_9m_c": at(9.1), "t_18m_c": at(18.3), "t_30m_c": at(30.5), "max_depth_m": round(prof[-1][0])}


def glider_thermocline(now: datetime | None = None, max_km: float = 160) -> dict:
    label = "Spray glider temperature profile (Scripps CUGN via IOOS Glider DAC)"
    url = GLIDER_ERDDAP + "/allDatasets.csv"

    def run():
        now_ = now or utcnow()
        since = (now_ - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
        listing = _get(f"{url}?datasetID,maxTime,minLatitude,maxLatitude,minLongitude,maxLongitude&maxTime%3E={since}").text
        cands = []
        for line in listing.splitlines()[2:]:
            parts = line.split(",")
            try:
                la0, la1, lo0, lo1 = map(float, parts[2:6])
            except ValueError:
                continue
            if la1 > 31.5 and la0 < 34.5 and lo0 < -116.8 and lo1 > -121.5:
                cands.append(parts[0])
        best = None
        for ds in cands:
            pos = _get(f"{GLIDER_ERDDAP}/{ds}.csv?profile_id,time,latitude,longitude&time%3E={since}&distinct()").text
            rows = [r.split(",") for r in pos.splitlines()[2:] if r.count(",") == 3]
            if not rows:
                continue
            pid, t, la, lo = rows[-1]
            d = _km((float(la), float(lo)), SAN_DIEGO)
            if d <= max_km and (best is None or d < best[0]):
                best = (d, ds, pid, t, float(la), float(lo))
        if best is None:
            raise RuntimeError("no glider profile within range in the last 36 h")
        d, ds, pid, t, la, lo = best
        prof = _get(f"{GLIDER_ERDDAP}/{ds}.csv?depth,temperature&profile_id={pid}").text
        samples = []
        for r in prof.splitlines()[2:]:
            try:
                dep, tmp = r.split(",")
                samples.append((float(dep), float(tmp)))
            except ValueError:
                continue
        th = thermocline_from_profile(samples)
        if th is None:
            raise RuntimeError("profile too shallow or sparse")
        return source("glider", label, f"https://gliders.ioos.us/erddap/tabledap/{ds}.html", ok=True,
                      time=parse_iso(t), now=now,
                      data={**th, "glider": ds.split("-")[0], "lat": la, "lng": lo, "km_from_san_diego": round(d)})
    return _guard("glider", label, url, run)


# --- Local files (our own published layers) ----------------------------------

def _local_json(sid: str, label: str, path: Path, time_key: str = "generated_at", now: datetime | None = None) -> dict:
    def run():
        d = json.loads(path.read_text(encoding="utf-8"))
        return source(sid, label, f"/data/{path.name}", ok=True, time=parse_iso(d.get(time_key)), data=d, now=now)
    return _guard(sid, label, str(path), run)


def manifest(now=None) -> dict:
    return _local_json("manifest", "ShoudiDive layer manifest", DATA / "manifest.json", now=now)


def tides(now=None) -> dict:
    return _local_json("tides", "NOAA CO-OPS tide predictions (ShoudiDive tides.json)", DATA / "tides.json", now=now)


def viz_column(now=None) -> dict:
    return _local_json("viz_column", "ShoudiDive water-column heuristic", DATA / "viz_column_spots.json", now=now)


_POST_DATE = re.compile(r"(january|february|march|april|may|june|july|august|september|october|november|december)"
                        r"-(\d{1,2})(?:st|nd|rd|th)?(?:-(\d{4}))?", re.I)


def post_date(url: str | None, fallback: datetime) -> date:
    """Dive-shop posts carry their date in the slug ('october-14th',
    'october-9-2026'); the ingest timestamp is when we read it."""
    if url:
        m = _POST_DATE.search(url)
        if m:
            year = int(m.group(3)) if m.group(3) else fallback.year
            try:
                d = datetime.strptime(f"{m.group(1)} {m.group(2)} {year}", "%B %d %Y").date()
                if d > fallback.date():
                    d = d.replace(year=d.year - 1)
                return d
            except ValueError:
                pass
    return fallback.date()


def dive_observations(now: datetime | None = None, days: int = 5) -> dict:
    """Latest distinct dive-shop and forum posts with a visibility reading."""
    label = "Dive-shop condition posts (ShoudiDive ground-truth ingest)"

    def run():
        now_ = now or utcnow()
        cutoff = now_ - timedelta(days=days)
        rows = []
        for line in OBS.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            src = o.get("source", "")
            if not (src.startswith("dive-shop") or src.startswith("forum") or src.startswith("reddit")):
                continue
            rows.append(o)
        return source("observations", label, "/pipeline/validation/data/observations.jsonl", ok=True,
                      time=max((parse_iso(r["timestamp_utc"]) for r in rows), default=None),
                      data=summarize_posts(rows, cutoff, now_), now=now)
    return _guard("observations", label, str(OBS), run)


def summarize_posts(rows: list[dict], cutoff: datetime, now: datetime) -> dict:
    """One entry per post URL (the ingest re-reads the same post many times),
    newest first; sources whose text never changes are set aside as stale."""
    by_url: dict[str, dict] = {}
    first_seen: dict[str, datetime] = {}
    excerpts_by_source: dict[str, set] = {}
    days_by_source: dict[str, set] = {}
    for r in rows:
        ts = parse_iso(r.get("timestamp_utc"))
        if not ts:
            continue
        src = r.get("source", "")
        excerpts_by_source.setdefault(src, set()).add((r.get("raw_excerpt") or "").strip())
        days_by_source.setdefault(src, set()).add(ts.date())
        url_key = r.get("source_url") or f"{src}|{r.get('spot_name')}|{r.get('raw_excerpt')}"
        if url_key not in first_seen or ts < first_seen[url_key]:
            first_seen[url_key] = ts
        if ts < cutoff:
            continue
        if r.get("observed_secchi_ft") is None and not re.search(r"\bvis", r.get("raw_excerpt") or "", re.I):
            continue
        key = r.get("source_url") or f"{src}|{r.get('spot_name')}|{r.get('raw_excerpt')}"
        prev = by_url.get(key)
        if prev is None or ts > parse_iso(prev["timestamp_utc"]):
            by_url[key] = r
    stale = {s for s, ex in excerpts_by_source.items() if len(ex) == 1 and len(days_by_source.get(s, ())) >= 3}
    posts = []
    for key, r in by_url.items():
        src = r.get("source", "")
        # A post with no date in its URL is dated by the first time we read it:
        # a page the ingest re-reads for months must not pass as today's report.
        ts = first_seen.get(key) or parse_iso(r["timestamp_utc"])
        exc = (r.get("raw_excerpt") or "").strip()
        vis = re.search(r"Vis(?:ibility)?:?\s*([0-9]+\s*(?:-|to|–)\s*[0-9]+\+?\s*(?:ft|feet|')|[0-9]+\+?\s*(?:ft|feet|'))", exc, re.I)
        posts.append({
            "source": src, "site": r.get("spot_name"), "url": r.get("source_url"),
            "post_date": post_date(r.get("source_url"), ts).isoformat(),
            "vis_text": vis.group(1).replace("–", "-") if vis else None,
            "vis_ft": r.get("observed_secchi_ft"),
            "excerpt": exc[:280], "stale_source": src in stale,
        })
    posts.sort(key=lambda p: p["post_date"], reverse=True)
    return {"posts": posts, "stale_sources": sorted(stale)}
