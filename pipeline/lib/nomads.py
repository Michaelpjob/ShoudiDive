"""NOMADS (NOAA Operational Model Archive and Distribution System) helpers.

Three pipeline fetchers — fetch_wind_5day.py, fetch_swell_5day.py,
fetch_waves.py — all do the same dance:

  1. Compose a `.idx` URL from a base + cycle date/hour.
  2. HEAD-probe the URL to find the latest cycle whose forecast is
     published yet.
  3. Walk backward in 6-hour cycle steps until something resolves.

The three fetchers had subtly different copies of step 2 — the simplest
was a single `requests.head()` call (fetch_wind_5day, fetch_waves) and
the most robust was a 3-retry HEAD+range-GET fallback that survives
NOMADS' bursty HEAD-throttling on GitHub runners (fetch_swell_5day).

This module exposes the robust version once so all three fetchers can
share it. The migration is mechanical — each callsite swaps its local
`_head_ok` + cycle-walking loop for `head_ok` + `find_latest_run`.

Extracted 2026-05-24 as Stage 6a of the pipeline refactor.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional, Tuple

import requests

NOMADS_HRRR = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod"
NOMADS_GFS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"

# A single shared session per process. NOMADS rate-limiting is per-IP,
# and the runners share an outbound IP, so connection pooling here
# is the cheapest correctness improvement we can make.
_SESSION = requests.Session()
_SESSION.headers.update({
    "Accept": "*/*",
    "User-Agent": "shouldidive/0.1 (+github.com/Michaelpjob/ShoudiDive)",
})


def session() -> requests.Session:
    """Return the shared NOMADS session — same UA + connection pool
    across every caller. Use this instead of constructing a fresh
    `requests.Session()` per module."""
    return _SESSION


def head_ok(url: str, *, timeout: int = 30) -> bool:
    """True if `url` returns 200.

    Tries HEAD first; on any non-decisive response, falls back to a
    1-byte range GET. Repeats up to 3 times before giving up. This
    pattern survives NOMADS' HEAD-throttling on busy days (GitHub
    runners get rate-limited from time to time even when straight
    curl works fine).

    The first non-decisive response per call is logged so CI surfaces
    *why* we bailed (403 / 429 / 5xx / TimeoutError) instead of always
    reporting "not yet published".

    Args:
        url: Full URL to probe (typically a `.grib2.idx` file).
        timeout: Per-request timeout in seconds.

    Returns:
        True if any attempt resolves to 200/206. False on hard 404
        or 3 consecutive failures.
    """
    last_diag: Optional[str] = None
    for attempt in range(3):
        try:
            r = _SESSION.head(url, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                return True
            if r.status_code == 404:
                return False
            last_diag = f"HEAD attempt {attempt}: {r.status_code}"
        except requests.RequestException as e:
            last_diag = f"HEAD attempt {attempt}: {type(e).__name__}"
        # HEAD didn't decisively succeed/fail — try a 1-byte range GET.
        try:
            r = _SESSION.get(
                url,
                headers={"Range": "bytes=0-0"},
                timeout=timeout,
                allow_redirects=True,
            )
            if r.status_code in (200, 206):
                return True
            if r.status_code == 404:
                return False
            last_diag = f"GET attempt {attempt}: {r.status_code}"
        except requests.RequestException as e:
            last_diag = f"GET attempt {attempt}: {type(e).__name__}"
        # Gentle backoff between attempts — NOMADS' rate-limiter resets
        # on a short window, so even 1-2 seconds is usually enough.
        if attempt < 2:
            time.sleep(0.5 * (attempt + 1))
    if last_diag:
        # Keep the diagnostic compact — strip the URL prefix so the
        # CI log line stays under one terminal width.
        tail = url.rsplit("/", 1)[-1]
        print(f"    head_ok({tail}): {last_diag}")
    return False


def find_latest_run(
    idx_url_for: Callable[[date, int], str],
    *,
    max_lookback_cycles: int = 8,
    label: str = "model",
    now: Optional[datetime] = None,
) -> Tuple[date, int]:
    """Walk backward through 6-hour NOMADS cycles until one resolves.

    NOMADS publishes model output (HRRR/GFS/gfswave) on a 6-hour cycle
    (00z/06z/12z/18z), but cycles are not immediately available — the
    most recent one is usually still running when we check. This helper
    starts at the current cycle and walks backward in 6-hour steps,
    HEAD-probing each candidate `.idx` URL, until one resolves to 200.

    Args:
        idx_url_for: Callable taking (run_date, run_hour) and returning
            the URL of a candidate `.idx` resource that signals "this
            cycle is published". Typically a `.grib2.idx`.
        max_lookback_cycles: How many 6-hour steps to walk back before
            giving up. Default 8 = 48 hours of history.
        label: Human-readable model name for log messages and the
            error string (e.g. "HRRR f48", "GFS f120").
        now: Optional clock injection point for tests. Defaults to
            datetime.now(timezone.utc).

    Returns:
        (run_date, run_hour) of the latest published cycle.

    Raises:
        RuntimeError if no cycle in the lookback window resolves.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now = now.replace(minute=0, second=0, microsecond=0)
    cycle = (now.hour // 6) * 6
    candidate = now.replace(hour=cycle)
    for _ in range(max_lookback_cycles):
        url = idx_url_for(candidate.date(), candidate.hour)
        if head_ok(url):
            return candidate.date(), candidate.hour
        print(
            f"  miss: {label} {candidate.strftime('%Y-%m-%d %H')}z "
            f"not yet published"
        )
        candidate -= timedelta(hours=6)
    raise RuntimeError(
        f"No {label} run found in last {max_lookback_cycles * 6} hours"
    )


def hrrr_sfc_idx_url(run_date: date, run_hour: int, *, fhour: int) -> str:
    """Compose an HRRR surface forecast `.idx` URL.

    Used by find_latest_run to probe for HRRR cycles with a given
    forecast horizon (e.g. fhour=48 → wrfsfcf48.grib2.idx). The 5-day
    wind fetcher uses this; intra-cycle forecast files share the same
    base layout.
    """
    return (
        f"{NOMADS_HRRR}/hrrr.{run_date.strftime('%Y%m%d')}/conus/"
        f"hrrr.t{run_hour:02d}z.wrfsfcf{fhour:02d}.grib2.idx"
    )


def gfs_pgrb2_idx_url(
    run_date: date,
    run_hour: int,
    *,
    fhour: int,
    resolution: str = "0p25",
) -> str:
    """Compose a GFS pgrb2 forecast `.idx` URL.

    Used by the 5-day wind fetcher to probe for the latest GFS cycle
    that has the f120 forecast published (the f120 lookahead is what
    differentiates the 5-day window from HRRR's 48-hour ceiling).
    """
    return (
        f"{NOMADS_GFS}/gfs.{run_date.strftime('%Y%m%d')}/{run_hour:02d}/atmos/"
        f"gfs.t{run_hour:02d}z.pgrb2.{resolution}.f{fhour:03d}.idx"
    )


def gfswave_idx_url(
    run_date: date,
    run_hour: int,
    *,
    fhour: int,
    subset: str,
) -> str:
    """Compose a gfswave forecast `.idx` URL for a given subset.

    NOAA publishes gfswave in geographically-narrow subsets so consumers
    don't have to download the global grid. CA + PNW use `wcoast.0p16`
    (US West Coast); tropical regions use `atlocn.0p16` (Atlantic +
    Gulf + Caribbean). The caller picks the subset.
    """
    return (
        f"{NOMADS_GFS}/gfs.{run_date.strftime('%Y%m%d')}/{run_hour:02d}/"
        f"wave/gridded/gfswave.t{run_hour:02d}z.{subset}.f{fhour:03d}.grib2.idx"
    )
