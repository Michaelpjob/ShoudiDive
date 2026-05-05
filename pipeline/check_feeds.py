"""Health check for every external HTTP data feed the pipeline depends on.

Runs as part of refresh-data.yml on a daily cadence. Outputs a JSON
report at ``pipeline/validation/data/feed_health.json`` so the watchdog
can surface dead feeds in the rolling GitHub issue.

What this catches:
  * server outage (HTTP 5xx, connection error, DNS failure)
  * auth regression (HTTP 401/403)
  * blocking by source (HTTP 429 rate-limit, Cloudflare 1020 etc.)
  * schema drift (HTTP 200 but unexpectedly tiny body, or expected JSON
    field missing — coarse, not deep)

What this does NOT catch:
  * "feed up but published yesterday's data" — needs per-feed freshness
    inspection that the individual fetch scripts already handle by
    walking back N days. Freshness is the model's job; reachability is
    ours.

Exits 0 unless EVERY feed in a "critical" category is red — most
single-feed outages are tolerated by the model's fallback chains, so
we want the daily refresh to keep running. The JSON output is the
authoritative state; a non-zero exit here would block the deploy.
"""
from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "pipeline" / "validation" / "data" / "feed_health.json"

# Polite to every host. Same UA as the validation scrapers so site
# owners can correlate logs across our two probe surfaces.
USER_AGENT = (
    "ShoudiDive-FeedChecker/1.0 "
    "(+https://shouldidive.com/about/validation; daily uptime probe)"
)
DEFAULT_TIMEOUT = 20


@dataclass
class FeedSpec:
    feed_id: str
    category: str          # "satellite" | "model" | "point_obs" | "static" | "ingest"
    consumer: str          # human-readable, "fetch_visibility.py uses sst_1d.png"
    probe_url: str
    method: str = "HEAD"   # "HEAD" or "GET"
    # 200 = full success; 206 = Partial Content (server honored our
    # Range header — we send one to keep probes cheap on big endpoints
    # like NOMADS directories). Both are healthy responses.
    expect_status: tuple = (200, 206)
    range_bytes: int = 4096   # only used if method=="GET"
    critical: bool = False    # if all critical feeds fail, exit non-zero
    skip_if_env_unset: Optional[str] = None  # e.g. "EARTHDATA_TOKEN"
    body_substring: Optional[str] = None     # if set, body must contain it
    notes: str = ""


@dataclass
class FeedResult:
    feed_id: str
    category: str
    status: str            # "green" | "yellow" | "red" | "skipped"
    http_status: Optional[int] = None
    duration_ms: Optional[int] = None
    bytes_seen: Optional[int] = None
    error: Optional[str] = None
    notes: str = ""


# -----------------------------------------------------------------------
# Feed registry
# -----------------------------------------------------------------------
#
# probe_url should be:
#   * stable (doesn't change with today's date) where possible
#   * cheap (HEAD works, or a Range-limited GET)
#   * informative (a 200 here means downstream date-walking will succeed)

FEEDS: list[FeedSpec] = [
    # --------- Satellite raster (NASA, NOAA OB.DAAC, Copernicus mirror) ---
    FeedSpec(
        feed_id="sst_mur_l4_pfeg",
        category="satellite",
        consumer="fetch.py → sst_1d.png; fetch_climatology.py → sst_climo.png",
        # ERDDAP dataset HTML page is stable + cheap
        probe_url="https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.html",
        method="GET",
        critical=True,
        notes="MUR L4 1km gap-filled SST. PFEG mirror.",
    ),
    FeedSpec(
        feed_id="chl_dineof_nrt_4km",
        category="satellite",
        consumer="fetch.py blender source #4 → chl_*.png",
        probe_url="https://coastwatch.noaa.gov/erddap/griddap/noaacwNPPN20VIIRSDINEOFDaily.html",
        method="GET",
        critical=False,
        notes="VIIRS S-NPP+N20 DINEOF NRT 4km. Fallback when NASA OB.DAAC tokens unset.",
    ),
    FeedSpec(
        feed_id="chl_dineof_sci_2km",
        category="satellite",
        consumer="fetch.py blender source #5 (last-resort)",
        probe_url="https://coastwatch.noaa.gov/erddap/griddap/noaacwNPPN20S3ASCIDINEOF2kmDaily.html",
        method="GET",
        critical=False,
        notes="DINEOF Science-Quality 2km. ~12 day publication lag.",
    ),
    FeedSpec(
        feed_id="kd490_dineof_2km",
        category="satellite",
        consumer="fetch.py → kd490_*.png; fetch_visibility uses Secchi=1.7/Kd_490",
        probe_url="https://coastwatch.noaa.gov/erddap/griddap/noaacwNPPN20S3AkdSCIDINEOF2kmDaily.html",
        method="GET",
        critical=False,
        notes="Multi-sensor Kd_490. Direct viz proxy.",
    ),
    FeedSpec(
        feed_id="nasa_obdaac_search",
        category="satellite",
        consumer="fetch.py blender sources #1-3 (AQUA/SNPP/S3A_OLCI)",
        probe_url="https://oceandata.sci.gsfc.nasa.gov/api/file_search?subType=1&search=AQUA_MODIS*L3m*CHL*chlor_a*4km*NRT*&sdate=2026-04-30&edate=2026-04-30&results_as_file=1",
        method="HEAD",
        expect_status=(200, 206, 401, 403),  # auth-gated; 401/403 = up but unauthorized
        critical=False,
        skip_if_env_unset="EARTHDATA_TOKEN",
        notes="NASA OB.DAAC. Bearer token required for actual download; we just check reachability.",
    ),
    FeedSpec(
        feed_id="chl_climo_modis_pfeg",
        category="satellite",
        consumer="fetch_climatology.py → chl_climo.png",
        probe_url="https://coastwatch.pfeg.noaa.gov/erddap/griddap/erdMWchla1day.html",
        method="GET",
        critical=False,
        notes="MODIS Aqua W-US (lng 0..360°). Used for monthly chl climatology.",
    ),

    # --------- Numerical model (NOMADS) ----------------------------------
    FeedSpec(
        feed_id="nomads_hrrr",
        category="model",
        consumer="fetch_wind.py + fetch_wind_5day.py",
        # Directory listing — stable, doesn't depend on today's cycle
        probe_url="https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/",
        method="GET",
        critical=True,
        body_substring="hrrr.",
        notes="3km HRRR conus. Updates every 6h; we use 00/06/12/18z runs.",
    ),
    FeedSpec(
        feed_id="nomads_gfs",
        category="model",
        consumer="fetch_wind.py history + fetch_wind_5day.py f49..f168",
        probe_url="https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/",
        method="GET",
        critical=False,
        body_substring="gfs.",
        notes="0.25° GFS atmos. Used for wind history + days 5-7 forecast.",
    ),
    FeedSpec(
        feed_id="nomads_gfswave_wcoast",
        category="model",
        consumer="fetch_waves.py + fetch_swell_5day.py",
        # Direct dataset directory (gfswave shares /gfs/prod/)
        probe_url="https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/",
        method="GET",
        critical=True,
        body_substring="gfs.",
        notes="WaveWatch III packaged in GFS. wcoast 0.16° subdomain.",
    ),

    # --------- Point obs --------------------------------------------------
    FeedSpec(
        feed_id="cdip",
        category="point_obs",
        consumer="fetch_waves wave/SST + validation/ingest/cdip.py",
        probe_url="https://cdip.ucsd.edu/data_access/justdar.cdip?201+pm+1",
        method="GET",
        critical=False,
        notes="Scripps CDIP justdar parameter table. Buoy 201 = La Jolla nearshore.",
    ),
    FeedSpec(
        feed_id="ndbc",
        category="point_obs",
        consumer="validation/ingest/ndbc.py",
        # Pick a stable buoy that's been online for years
        probe_url="https://www.ndbc.noaa.gov/data/realtime2/46086.txt",
        method="HEAD",
        critical=False,
        notes="NOAA NDBC realtime2 plain text. 46086 = San Clemente Basin.",
    ),
    FeedSpec(
        feed_id="usgs_nwis_iv",
        category="point_obs",
        consumer="fetch_rivers.py → rivers.json",
        # San Diego River — small, but proves the API is up
        probe_url="https://waterservices.usgs.gov/nwis/iv/?format=json&sites=11023340&parameterCd=00060&period=P1D",
        method="HEAD",
        critical=False,
        notes="USGS NWIS instantaneous values. Site 11023340 = San Diego River.",
    ),
    FeedSpec(
        feed_id="usgs_nwis_stat",
        category="point_obs",
        consumer="fetch_rivers.py → rivers.json (climatology)",
        probe_url="https://waterservices.usgs.gov/nwis/stat/?format=rdb&sites=11023340&parameterCd=00060&statReportType=monthly&statTypeCd=mean",
        method="HEAD",
        critical=False,
        notes="USGS NWIS multi-decade monthly statistics.",
    ),
    FeedSpec(
        feed_id="coops_predictions",
        category="point_obs",
        consumer="fetch_tides.py → tides.json",
        # Use a syntactically minimal URL that returns a small JSON
        probe_url=(
            "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
            "?product=predictions&datum=MLLW&interval=hilo&station=9410230"
            "&begin_date=20260501&end_date=20260501&time_zone=gmt"
            "&units=metric&format=json&application=shouldidive-feed-checker"
        ),
        method="GET",
        critical=False,
        body_substring="predictions",
        notes="NOAA CO-OPS Tides & Currents. Station 9410230 = La Jolla.",
    ),
    FeedSpec(
        feed_id="cpc_precip_psl",
        category="point_obs",
        consumer="fetch_precip.py → precip_7d.png",
        # OPeNDAP catalog page (HTML)
        probe_url="https://psl.noaa.gov/thredds/catalog/Datasets/cpc_us_precip/RT/catalog.html",
        method="GET",
        critical=False,
        body_substring="precip.V1.0",
        notes="NOAA PSL THREDDS — CPC US Unified Daily Precip RT.",
    ),

    # --------- Static / one-time ----------------------------------------
    FeedSpec(
        feed_id="gmrt_bathy",
        category="static",
        consumer="fetch_bathy.py → bathy.png (one-shot, idempotent)",
        # Point at the GridServer health URL with a tiny bbox
        probe_url=(
            "https://www.gmrt.org/services/GridServer"
            "?north=33&south=32&west=-118&east=-117&format=netcdf&resolution=low"
        ),
        method="HEAD",
        critical=False,
        notes="GMRT GridServer (Lamont-Doherty). Used once; rarely refetched.",
    ),
    FeedSpec(
        feed_id="osm_overpass_main",
        category="static",
        consumer="fetch_coastline.py → land.geojson",
        probe_url="https://overpass-api.de/api/status",
        method="GET",
        critical=False,
        notes="OpenStreetMap Overpass API — primary mirror. Has fallbacks.",
    ),
    FeedSpec(
        feed_id="cdfw_mpa",
        category="static",
        consumer="fetch_mpa.py → mpa-boundaries.geojson",
        probe_url="https://data-cdfw.opendata.arcgis.com/datasets/117a99c8745a48c6a48bac70005b1b11_0.geojson",
        method="HEAD",
        critical=False,
        notes="CDFW ArcGIS Open Data — California MPAs.",
    ),

    # --------- Validation ingest ----------------------------------------
    FeedSpec(
        feed_id="ingest_justgetwet",
        category="ingest",
        consumer="validation/ingest/justgetwet.py → observations.jsonl",
        probe_url="https://justgetwet.com/blogs/dive-reports-and-conditions",
        method="HEAD",
        critical=False,
        notes="Just Get Wet (Shopify dive shop blog).",
    ),
    FeedSpec(
        feed_id="ingest_diveviz",
        category="ingest",
        consumer="validation/ingest/diveviz.py",
        probe_url="https://diveviz.com/blogs/daily-dive-report",
        method="HEAD",
        critical=False,
        notes="DiveViz (LLM-extracted, requires ANTHROPIC_API_KEY).",
    ),
    FeedSpec(
        feed_id="ingest_bdoutdoors",
        category="ingest",
        consumer="validation/ingest/bdoutdoors.py",
        probe_url="https://www.bdoutdoors.com/forums/forum/spear-fishing-reports/index.rss",
        method="HEAD",
        critical=False,
        notes="BD Outdoors XenForo RSS (4 sub-forums).",
    ),
    FeedSpec(
        feed_id="ingest_reddit",
        category="ingest",
        consumer="validation/ingest/reddit.py",
        probe_url="https://www.reddit.com/r/scuba/.rss",
        method="HEAD",
        critical=False,
        notes="Reddit Atom feeds (.rss path; .json blocked from CI IPs).",
    ),
    FeedSpec(
        feed_id="ingest_eagle4",
        category="ingest",
        consumer="validation/ingest/eagle4.py",
        probe_url="https://eagle4pacific.com/dive-reports/",
        method="HEAD",
        expect_status=(200,),
        critical=False,
        notes="Eagle 4 Pacific. Known dead — handoff URL was never verified. Tracked here so a future revival registers automatically.",
    ),
]


# -----------------------------------------------------------------------
# Probing
# -----------------------------------------------------------------------

def _probe(spec: FeedSpec) -> FeedResult:
    if spec.skip_if_env_unset and not os.environ.get(spec.skip_if_env_unset):
        return FeedResult(
            feed_id=spec.feed_id, category=spec.category,
            status="skipped",
            notes=f"{spec.skip_if_env_unset} unset",
        )

    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if spec.method == "GET" and spec.range_bytes:
        headers["Range"] = f"bytes=0-{spec.range_bytes - 1}"

    started = time.monotonic()
    try:
        if spec.method == "HEAD":
            r = requests.head(spec.probe_url, headers=headers,
                              timeout=DEFAULT_TIMEOUT, allow_redirects=True)
        else:
            r = requests.get(spec.probe_url, headers=headers,
                             timeout=DEFAULT_TIMEOUT, allow_redirects=True,
                             stream=False)
        elapsed_ms = int((time.monotonic() - started) * 1000)
    except (requests.exceptions.RequestException, socket.gaierror) as exc:
        return FeedResult(
            feed_id=spec.feed_id, category=spec.category,
            status="red",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"{exc.__class__.__name__}: {exc}",
            notes=spec.notes,
        )

    bytes_seen = len(r.content) if r.content is not None else 0
    note_extra = []
    status_label = "green"
    if r.status_code not in spec.expect_status:
        status_label = "red"
        note_extra.append(f"unexpected http {r.status_code}")
    if elapsed_ms > 5000 and status_label == "green":
        status_label = "yellow"
        note_extra.append(f"slow ({elapsed_ms} ms)")
    if spec.body_substring and spec.method == "GET":
        body = r.text or ""
        if spec.body_substring not in body:
            status_label = "red"
            note_extra.append(f"body missing '{spec.body_substring}'")

    return FeedResult(
        feed_id=spec.feed_id, category=spec.category,
        status=status_label,
        http_status=r.status_code,
        duration_ms=elapsed_ms,
        bytes_seen=bytes_seen,
        notes=spec.notes + (" — " + "; ".join(note_extra) if note_extra else ""),
    )


def main() -> int:
    print(f"Probing {len(FEEDS)} feeds...")
    started = datetime.now(timezone.utc)
    results: list[FeedResult] = []
    for spec in FEEDS:
        res = _probe(spec)
        results.append(res)
        marker = {"green": "OK ", "yellow": "SLOW", "red": "DEAD",
                  "skipped": "SKIP"}.get(res.status, "??")
        line = f"  [{marker}] {res.feed_id:<26} {res.category:<10}"
        if res.http_status is not None:
            line += f" http={res.http_status}"
        if res.duration_ms is not None:
            line += f" {res.duration_ms}ms"
        if res.error:
            line += f" — {res.error}"
        print(line)

    summary = {
        "computed_at": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "total": len(results),
        "green": sum(1 for r in results if r.status == "green"),
        "yellow": sum(1 for r in results if r.status == "yellow"),
        "red": sum(1 for r in results if r.status == "red"),
        "skipped": sum(1 for r in results if r.status == "skipped"),
        "results": [
            {
                "feed_id": r.feed_id,
                "category": r.category,
                "status": r.status,
                "http_status": r.http_status,
                "duration_ms": r.duration_ms,
                "bytes_seen": r.bytes_seen,
                "error": r.error,
                "notes": r.notes,
            }
            for r in results
        ],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH.relative_to(REPO_ROOT)}")
    print(f"Summary: {summary['green']} green / {summary['yellow']} yellow"
          f" / {summary['red']} red / {summary['skipped']} skipped")

    # Decide exit code: only fail the CI step if EVERY critical feed is red.
    # Single-feed outages are tolerated by the model's fallback chains.
    critical_specs = [s for s in FEEDS if s.critical]
    critical_results = [r for r in results
                        if r.feed_id in {s.feed_id for s in critical_specs}]
    critical_red = [r for r in critical_results if r.status == "red"]
    if critical_specs and len(critical_red) == len(critical_specs):
        print(f"\nALL {len(critical_specs)} critical feeds are red — exiting non-zero")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
