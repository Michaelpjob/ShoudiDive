"""Just Get Wet — San Diego dive shop daily reports.

Just Get Wet (justgetwet.com) is a San Diego dive shop that publishes
daily reports covering La Jolla, Point Loma, the Coronado Islands,
North County, and offshore. Their format is consistent and explicitly
labelled — every post starts with three lines:

    Vis: 0-10ft
    Swell: 3.2 at 5s
    Water Temp: 64 ºF

…followed by a prose body that names locations ("the cove again is
looking the most clear", "Pt Loma kelp had decent vis"). That means
we get the headline numbers via regex (cheap + reliable) and only
need the spot lookup for body resolution.

Index page lists the latest 5–10 posts at predictable URLs:
``/blogs/dive-reports-and-conditions/{slug}`` where ``{slug}`` is
``april-26-2026``-style. We pull the index, walk into the most
recent 3 posts (anything older is past the 24-hour cutoff anyway),
extract the headline numbers, and emit one observation per post
keyed off the body's strongest spot signal.

Confidence weight 0.85 — local dive shop with direct buoy access
and a strong "we get the best sources" framing on their about page.
"""
from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timezone

from ._base import BaseScraper


SOURCE_ROOT = "https://justgetwet.com/blogs/dive-reports-and-conditions"

# Default focal point if the body doesn't name a specific spot — Just
# Get Wet is a SD shop and most reports default to La Jolla.
DEFAULT_LAT, DEFAULT_LNG = 32.852, -117.272
DEFAULT_NAME = "La Jolla"


# Headline label parsers. Each picks the FIRST matching numeric (or
# numeric range — averaged) and tolerates the unicode degree sign,
# period markers, and the occasional "ft" / "feet" / quote-mark
# spelling Just Get Wet uses.
_VIS_RE = re.compile(
    r"vis(?:ibility)?\s*[:\-]?\s*"
    r"(\d{1,3})(?:\s*[\-–to]\s*(\d{1,3}))?\s*(?:ft|feet|')",
    re.IGNORECASE,
)
_SWELL_RE = re.compile(
    r"swell\s*[:\-]?\s*"
    r"(\d{1,2}(?:\.\d+)?)(?:\s*[\-–to]\s*(\d{1,2}(?:\.\d+)?))?\s*"
    r"(?:ft|feet|')?\s*(?:at\s+(\d{1,2}(?:\.\d+)?)\s*s(?:ec)?)?",
    re.IGNORECASE,
)
_TEMP_RE = re.compile(
    r"(?:water\s+temp|water\s+temperature|sst)\s*[:\-]?\s*"
    r"(\d{2,3}(?:\.\d+)?)\s*[º°⁰]?\s*([cf])?",
    re.IGNORECASE,
)

# Anchor links on the index page point at individual post URLs. The
# slug always lives directly under /blogs/dive-reports-and-conditions/.
_POST_LINK_RE = re.compile(
    r'href="(/blogs/dive-reports-and-conditions/[a-z0-9\-]+)"',
    re.IGNORECASE,
)

# How many recent posts to walk into per cron — anything past the 3rd
# is older than today and the 24-hour cutoff in score.py drops it
# anyway. Keeping this small also keeps us comfortably under the
# polite-rate-limit budget.
MAX_POSTS_PER_RUN = 3


class JustGetWetScraper(BaseScraper):
    source_id = "dive-shop-justgetwet"
    source_confidence = 0.85
    source_root_url = SOURCE_ROOT

    # Just Get Wet is a Shopify storefront — handles aggressive scraping
    # well in normal use, but we still pace ourselves. 30s between
    # post fetches keeps total run cost under 2 minutes per cron tick.
    # Override the BaseScraper 5-minute default so we can walk the
    # index → recent-3-posts in one fetch() call.
    host_rate_limit_s = 30
    _INTRA_PAUSE_S = 30

    def __init__(self):
        super().__init__()
        self._spot_lookup = _load_spot_lookup()

    def fetch(self) -> list[dict]:
        try:
            r = self._polite_get(SOURCE_ROOT)
        except Exception as exc:  # noqa: BLE001
            print(f"  {self.source_id}: index fetch failed: {exc.__class__.__name__}")
            return []

        slugs = self._post_slugs(r.text)
        if not slugs:
            print(f"  {self.source_id}: index parsed but found 0 post links")
            return []

        out: list[dict] = []
        import time
        for i, slug in enumerate(slugs[:MAX_POSTS_PER_RUN]):
            if i > 0:
                time.sleep(self._INTRA_PAUSE_S)
            url = f"https://justgetwet.com{slug}"
            try:
                post_r = self._polite_get(url)
            except Exception as exc:  # noqa: BLE001
                print(f"  {self.source_id}: {slug} failed: {exc.__class__.__name__}")
                continue
            obs = self._parse_post(post_r.text, url, seq=i)
            if obs is not None:
                out.append(obs)
        return out

    # ---- parsing --------------------------------------------------

    @staticmethod
    def _post_slugs(html: str) -> list[str]:
        """Return ordered list of unique post slugs (most recent first)."""
        seen: set[str] = set()
        ordered: list[str] = []
        for m in _POST_LINK_RE.finditer(html):
            slug = m.group(1)
            # The index links every post twice (heading + thumbnail),
            # plus the page itself links to itself. Drop the listing
            # URL and dedup.
            if slug == "/blogs/dive-reports-and-conditions":
                continue
            if slug in seen:
                continue
            seen.add(slug)
            ordered.append(slug)
        return ordered

    def _parse_post(self, html: str, url: str, *, seq: int) -> dict | None:
        try:
            from bs4 import BeautifulSoup  # noqa: PLC0415
        except ImportError:
            print(f"  {self.source_id}: bs4 not installed, skipping")
            return None

        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article")
        if not article:
            return None
        # Body lives in .rte (Shopify's Rich Text Editor wrapper). The
        # article element ALSO includes the tag list ("La Jolla
        # diving · San Clemente Island spearfishing · ..."), which
        # absolutely poisons substring spot resolution if we feed the
        # whole article to it. Restrict to the body proper.
        body_el = article.select_one(".rte")
        if body_el is None:
            # Fallback: full article. Spot resolution will be sketchy
            # but the headline regex still fires.
            body_el = article
        text = body_el.get_text(" ", strip=True)
        if not text:
            return None

        viz_ft  = _extract_visibility(text)
        sst_f   = _extract_temp(text)
        swell_ft, _swell_period = _extract_swell(text)

        if viz_ft is None and sst_f is None and swell_ft is None:
            return None

        spot_name, lat, lng = self._resolve_spot(text)

        # Each post has its own date in the URL slug ("april-26-2026")
        # but for ingest-cadence purposes we anchor obs to "now" — the
        # score.py join is on (date(timestamp), spatial); a post about
        # yesterday read at 04:00 today still scores against yesterday's
        # archive cleanly. Keeping it simple beats trying to back-date.
        now = datetime.now(timezone.utc)

        return {
            "obs_id":             self.make_obs_id(spot_name, seq=seq, when=now),
            "timestamp_utc":      now.isoformat(timespec="minutes").replace("+00:00", "Z"),
            "lat":                float(lat),
            "lng":                float(lng),
            "spot_name":          spot_name,
            "observed_secchi_ft": viz_ft,
            "observed_sst_f":     sst_f,
            "observed_swell_ft":  swell_ft,
            "source":             self.source_id,
            "source_url":         url,
            "source_confidence":  self.source_confidence,
            "extraction_method":  "regex-headline",
            "raw_excerpt":        text[:280],
            "notes":              None,
        }

    def _resolve_spot(self, text: str) -> tuple[str, float, float]:
        """Pick the spot name with the longest substring match in the
        body. Falls back to ``DEFAULT_NAME`` (La Jolla) if no spot
        keyword appears — that's correct for Just Get Wet's
        SD-default reports."""
        candidates = sorted(self._spot_lookup.keys(), key=len, reverse=True)
        text_lc = text.lower()
        for k in candidates:
            if k.startswith("_"):  # _meta etc.
                continue
            if k.lower() in text_lc:
                lat, lng = self._spot_lookup[k]
                return k, float(lat), float(lng)
        return DEFAULT_NAME, DEFAULT_LAT, DEFAULT_LNG


def _extract_visibility(text: str) -> float | None:
    m = _VIS_RE.search(text)
    if not m:
        return None
    a = float(m.group(1))
    b = m.group(2)
    v = (a + float(b)) / 2 if b else a
    if not (1 <= v <= 100):
        return None
    return round(v, 1)


def _extract_swell(text: str) -> tuple[float | None, float | None]:
    m = _SWELL_RE.search(text)
    if not m:
        return None, None
    a = float(m.group(1))
    b = m.group(2)
    v = (a + float(b)) / 2 if b else a
    if not (0 <= v <= 30):
        v = None
    period = float(m.group(3)) if m.group(3) else None
    if period is not None and not (1 <= period <= 30):
        period = None
    return (round(v, 1) if v is not None else None,
            round(period, 1) if period is not None else None)


def _extract_temp(text: str) -> float | None:
    m = _TEMP_RE.search(text)
    if not m:
        return None
    v = float(m.group(1))
    unit = (m.group(2) or "F").upper()
    if unit == "C":
        v = v * 9 / 5 + 32
    if not (50 <= v <= 80):
        return None
    return round(v, 1)


def _load_spot_lookup() -> dict[str, list[float]]:
    """Read _spot_lookup.json once at scraper-init time."""
    p = pathlib.Path(__file__).parent / "_spot_lookup.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}
