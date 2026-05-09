"""Beach Cities Scuba — Laguna Beach dive-conditions page.

Beach Cities Scuba (beachcitiescuba.com) publishes a single daily
conditions snapshot at ``/pages/current-conditions``. Format observed
2026-05-09:

    Shaw's Cove, Laguna Beach
    Reported: 8am, May 9, 2026
    Visibility: 5-8ft
    Waves: 2-3ft
    Surge: Light
    Water Temperature: 62°F
    Air Temperature: 63°F

A single spot per page, refreshed manually by shop staff. Lower-volume
than Just Get Wet (1 obs/day vs ~3) but it's the ONLY non-buoy source
covering Laguna Beach — `bight_offshore` and `bight_nearshore` zones
have been carrying Just Get Wet (SD-focused) and DiveViz (LA/OC) but
Laguna's central-OC kelp beds were a coverage gap.

Confidence weight 0.85 — same as the other working dive shops. Format
is structured (labelled fields with explicit units), so extraction is
regex-based; no LLM dependency.

Earlier validation handoff comment in __init__.py said this site was
"JS-rendered, BS4 sees empty body". That was either wrong or the site
got re-platformed. Re-probed 2026-05-09 — static HTML works, the
labelled fields parse cleanly with the same regex pattern Just Get
Wet uses.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ._base import BaseScraper


# Single conditions URL. If the shop ever publishes per-spot pages
# we'll add an index-walk like Just Get Wet — for now the front page
# carries one spot at a time.
CONDITIONS_URL = "https://beachcitiescuba.com/pages/current-conditions"

# Default focal point: Shaw's Cove is the shop's home reef and what
# the page reports on most. If the parser ever sees a different spot
# name we resolve it through _spot_lookup.json (same path the LLM
# scrapers take).
SHAWS_COVE = ("Shaw's Cove", 33.547, -117.792)


# Field-label parsers. Same shape as justgetwet.py's regexes — the
# data shop publishes a labeled-field block, just with slightly
# different label names ("Visibility" vs "Vis", "Water Temperature"
# vs "Water Temp"). The regexes below are tolerant to both.
_VIS_RE = re.compile(
    r"(?:vis(?:ibility)?)\s*[:\-]?\s*"
    r"(\d{1,3})(?:\s*[\-–to]\s*(\d{1,3}))?\s*(?:ft|feet|')",
    re.IGNORECASE,
)
_WAVES_RE = re.compile(
    r"waves?\s*[:\-]?\s*"
    r"(\d{1,2}(?:\.\d+)?)(?:\s*[\-–to]\s*(\d{1,2}(?:\.\d+)?))?\s*"
    r"(?:ft|feet|')?",
    re.IGNORECASE,
)
_TEMP_RE = re.compile(
    r"(?:water\s+temp|water\s+temperature)\s*[:\-]?\s*"
    r"(\d{2,3}(?:\.\d+)?)\s*[º°⁰]?\s*([cf])?",
    re.IGNORECASE,
)
# Pull the "Reported: 8am, May 9, 2026" line so we can preserve the
# observation timestamp instead of overwriting with cron-fetch time.
_REPORTED_RE = re.compile(
    r"reported\s*[:\-]?\s*"
    r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*,?\s*"
    r"([A-Za-z]{3,9})\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?,?\s*"
    r"(\d{4})",
    re.IGNORECASE,
)
# Spot-name probe: Beach Cities Scuba's reports lead with the spot
# name as a header. If that ever changes to a less-deterministic
# format we'll fall back to the SHAWS_COVE default.
_SPOT_HEADER_RE = re.compile(
    r"(?:^|>|\n)\s*(shaw'?s\s+cove|crescent\s+bay|divers'?\s+cove|"
    r"woods'?\s+cove|fisherman'?s\s+cove|moss\s+cove|treasure\s+island)",
    re.IGNORECASE,
)


def _avg_or_first(a: str, b: str | None) -> float:
    if b is None:
        return float(a)
    return (float(a) + float(b)) / 2


def _parse_visibility(text: str) -> tuple[float | None, str | None]:
    m = _VIS_RE.search(text)
    if not m:
        return None, None
    return _avg_or_first(m.group(1), m.group(2)), m.group(0).strip()


def _parse_waves(text: str) -> float | None:
    m = _WAVES_RE.search(text)
    if not m:
        return None
    return _avg_or_first(m.group(1), m.group(2))


def _parse_temp_f(text: str) -> float | None:
    m = _TEMP_RE.search(text)
    if not m:
        return None
    v = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit == "c":
        v = v * 9 / 5 + 32
    if v < 30 or v > 100:
        return None
    return round(v, 1)


def _parse_reported_at(text: str) -> datetime | None:
    """Return the observation timestamp the shop wrote on the report, in
    UTC. Falls back to None — the orchestrator stamps now() if missing."""
    m = _REPORTED_RE.search(text)
    if not m:
        return None
    hour_str, month_str, day_str, year_str = m.groups()
    try:
        # Heuristic local time parse: the shop is in Laguna (PT). We
        # parse as PT-naive, then shift to UTC. Accuracy doesn't have
        # to be perfect — the score.py joiner uses a 24h window.
        from datetime import timedelta
        dt_naive = datetime.strptime(
            f"{month_str} {day_str} {year_str} {hour_str}".strip(),
            "%B %d %Y %I%p",
        )
    except ValueError:
        try:
            dt_naive = datetime.strptime(
                f"{month_str} {day_str} {year_str} {hour_str}".strip(),
                "%B %d %Y %I:%M%p",
            )
        except ValueError:
            return None
    # PT to UTC: standard time offset only — not worth the dst lib for
    # an hour-bucket join.
    return (dt_naive + timedelta(hours=8)).replace(tzinfo=timezone.utc)


def _identify_spot(text: str) -> tuple[str, float, float]:
    """Resolve which Laguna spot the report names. Falls back to Shaw's
    Cove (the shop's home reef + most-frequent report subject)."""
    m = _SPOT_HEADER_RE.search(text)
    if not m:
        return SHAWS_COVE
    name = m.group(1).strip().lower()
    # Spot lookup — Laguna's coves cluster on a ~2 mi stretch; even
    # if we miss the exact pin the zone-level join is still right.
    if "shaw" in name:
        return ("Shaw's Cove", 33.547, -117.792)
    if "crescent" in name:
        return ("Crescent Bay", 33.550, -117.795)
    if "divers" in name:
        return ("Divers Cove", 33.545, -117.789)
    if "woods" in name:
        return ("Woods Cove", 33.530, -117.780)
    if "fisherman" in name:
        return ("Fisherman's Cove", 33.548, -117.793)
    if "moss" in name:
        return ("Moss Cove", 33.529, -117.778)
    if "treasure" in name:
        return ("Treasure Island", 33.525, -117.775)
    return SHAWS_COVE


class BeachCitiesCubaScraper(BaseScraper):
    source_id = "dive-shop-beachcitiescuba"
    source_confidence = 0.85
    source_root_url = CONDITIONS_URL
    # One URL, one fetch per cron — the standard 5-minute floor is fine.

    def fetch(self) -> list[dict]:
        try:
            r = self._polite_get(CONDITIONS_URL)
        except Exception:
            # Network blip or page moved. Empty list = "no obs this run",
            # the watchdog will flag the source-silent rule if we go
            # silent for >24h.
            return []
        text = r.text

        viz_ft, viz_excerpt = _parse_visibility(text)
        if viz_ft is None:
            # Whole point of this scraper is the visibility number. If
            # the shop's page format changed and we can't find one,
            # don't emit a low-quality observation.
            return []

        spot_name, lat, lng = _identify_spot(text)
        waves_ft = _parse_waves(text)
        temp_f = _parse_temp_f(text)
        when = _parse_reported_at(text) or datetime.now(timezone.utc)

        obs = {
            "obs_id": self.make_obs_id(spot_name, seq=0, when=when),
            "timestamp_utc": when.isoformat(timespec="minutes").replace("+00:00", "Z"),
            "lat": lat,
            "lng": lng,
            "spot_name": spot_name,
            "observed_secchi_ft": viz_ft,
            "observed_sst_f": temp_f,
            "observed_swell_ft": waves_ft,
            "observed_swell_period_s": None,
            "observed_swell_dir_deg": None,
            "source": self.source_id,
            "source_url": CONDITIONS_URL,
            "source_confidence": self.source_confidence,
            "extraction_method": "regex-labelled-fields",
            "raw_excerpt": viz_excerpt,
            "notes": "Beach Cities Scuba daily conditions page",
        }
        return [obs]
