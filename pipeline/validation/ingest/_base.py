"""Shared base + helpers for ground-truth scrapers.

Every scraper is a subclass of ``BaseScraper`` that returns a list of
normalized observation dicts. The base handles:

* polite rate-limiting (5 minutes per host minimum)
* a real User-Agent identifying the project + a contact URL
* a request timeout so a hung source can't stall the whole run
* a stable ``obs_id`` factory so dedup across days is trivial

The orchestrator (``ingest/__init__.py``) catches any exception from
``fetch()`` and continues — one broken source must never take down
the rest.
"""
from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests


# Identify the validator clearly so a site owner can find us in their
# logs and email if they'd rather we stop. The handoff explicitly
# mandates a contact URL — keep it accurate.
USER_AGENT = (
    "ShoudiDive-Validator/1.0 "
    "(+https://shouldidive.com/about/validation; ground-truth pull for visibility model accuracy)"
)

# 5 minutes per host between requests. The actual scrape volume is
# tiny (each source is hit at most once per cron tick) but the
# rate-limit floor is still here so no future bug can accidentally
# hammer a public site.
DEFAULT_RATE_LIMIT_S = 300

# Defensive timeout — a hung dive shop blog shouldn't stall the whole
# ingest cron.
HTTP_TIMEOUT_S = 30


class BaseScraper(ABC):
    """Common machinery for every scraper.

    Subclasses set the three class attributes (``source_id``,
    ``source_confidence``, ``source_root_url``) and implement
    ``fetch() -> list[dict]``. The dict shape is documented in
    ``01-architecture.md`` (lat, lng, observed_*_ft / _f, source,
    source_url, source_confidence, extraction_method, raw_excerpt,
    notes).

    Rate-limit override: a scraper that needs to make multiple
    requests to the same host within one ``fetch()`` call (e.g. Just
    Get Wet's index → top-3-posts walk) sets
    ``host_rate_limit_s`` lower than the default 300 s. The base
    class still enforces it, so this can never accidentally bypass
    polite cadence — only tighten it for sources we know tolerate it.
    """

    source_id: str = "unset"
    source_confidence: float = 0.0
    source_root_url: str = ""

    # How long to wait between requests to the same host. Defaults to
    # the conservative 5-minute floor; well-known APIs (CDIP) and
    # cooperative content shops (Just Get Wet) override this to a
    # lower cadence so a single fetch() can walk multiple URLs.
    host_rate_limit_s: float = float(DEFAULT_RATE_LIMIT_S)

    def __init__(self):
        # Per-host last-fetch time so even multi-URL scrapers respect
        # the polite cadence on each domain they touch.
        self._last_fetch: dict[str, float] = {}

    # ---- HTTP -----------------------------------------------------

    def _polite_get(self, url: str, **kwargs) -> requests.Response:
        host = urlparse(url).netloc
        now = time.time()
        last = self._last_fetch.get(host, 0)
        wait_floor = float(self.host_rate_limit_s)
        if now - last < wait_floor:
            time.sleep(wait_floor - (now - last))
        self._last_fetch[host] = time.time()

        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("User-Agent", USER_AGENT)
        kwargs.setdefault("timeout", HTTP_TIMEOUT_S)

        r = requests.get(url, headers=headers, **kwargs)
        r.raise_for_status()
        return r

    # ---- ID + helpers ---------------------------------------------

    def make_obs_id(self, spot_slug: str, seq: int = 0, *, when: datetime | None = None) -> str:
        """Stable obs_id keyed on (source, date, spot, seq).

        Ingesting the same source twice on the same UTC day produces
        the same id, so the orchestrator dedup keeps a single record
        per spot per day. ``when`` lets backfill scrapers tag
        historical days correctly.
        """
        when = when or datetime.now(timezone.utc)
        d = when.strftime("%Y%m%d")
        return f"{self.source_id}-{d}-{slugify(spot_slug)}-{seq}"

    # ---- Subclass contract ----------------------------------------

    @abstractmethod
    def fetch(self) -> list[dict]:
        """Return a list of observation dicts (one per spot+timestamp).

        Required keys per dict:

            obs_id, timestamp_utc, lat, lng, spot_name,
            observed_secchi_ft, observed_sst_f, observed_swell_ft,
            source, source_url, source_confidence, extraction_method,
            raw_excerpt, notes

        Any of the ``observed_*`` fields may be ``None`` — score.py
        only joins on the fields that are actually populated.
        """


# ---- Module-level helpers ----------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(s: str) -> str:
    """Lowercase, alphanum + dash, no leading/trailing dashes."""
    return _SLUG_RE.sub("-", (s or "").lower()).strip("-") or "unknown"


def parse_visibility_ft(s: str | None) -> float | None:
    """Best-effort feet extraction from prose like '20-25 ft', '~30''.

    Strategy: pull the first contiguous integer (or simple range,
    averaged), then look at the unit qualifier. ``meters`` / ``m``
    convert to feet. Anything that doesn't match returns None — we
    deliberately don't guess.
    """
    if not s:
        return None
    text = s.lower()

    # "20-25 ft" → midpoint
    m = re.search(r"(\d{1,3})\s*(?:to|-|–|—)\s*(\d{1,3})\s*(ft|feet|m|meters)?", text)
    if m:
        a, b, unit = float(m.group(1)), float(m.group(2)), (m.group(3) or "ft")
        v = (a + b) / 2
    else:
        # "25 ft" / "25'" / "25"
        m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(ft|feet|m|meters|')?", text)
        if not m:
            return None
        v = float(m.group(1))
        unit = m.group(2) or "ft"

    if unit and unit.startswith("m"):
        v = v * 3.281
    if v <= 0 or v > 200:
        return None
    return round(v, 1)


def parse_temp_f(s: str | None) -> float | None:
    """Best-effort °F extraction. ``°C`` converts; nothing else guesses."""
    if not s:
        return None
    text = s.lower()
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:°|deg(?:rees)?)?\s*(c|f)?", text)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    if unit == "c":
        v = v * 9 / 5 + 32
    if v < 30 or v > 100:
        # Below 30°F or above 100°F is implausible for CA coastal water;
        # almost certainly the regex grabbed something else (a date, a
        # depth, a wave height).
        return None
    return round(v, 1)
