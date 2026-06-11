"""South Coast Divers — daily Laguna group posts.

South Coast Divers (southcoastdivers.com) maintains a daily-updated
blog at /blog. The blog is a directory of timestamped .txt files
named `YYYYMMDDHHMMSS-RichP.txt`, each viewable through a custom
PHP endpoint:

    https://southcoastdivers.com/php/display_blog_list.php?func=view&file=20260508124039-RichP.txt

Posts are informal prose — Rich Parker + Louis Umphenour write up
morning conditions for Laguna's coves (Bluebird Canyon, Woods Cove,
South/North Laguna). Quote from the 2026-05-08 post:

    "that leaves me with the viz being around 10 feet again"

The format is prose without labelled fields, so extraction goes
through ``_llm_extract.extract_from_prose`` like DiveViz / BdOutdoors.
Without ``ANTHROPIC_API_KEY`` set this scraper emits zero obs and
exits gracefully — same pattern as the other LLM-extracted sources.

Confidence weight 0.80 — slightly below the labelled-field shops
(Just Get Wet, Beach Cities Scuba) because (a) the text is
unstructured, and (b) reports are typically subjective viz estimates
("around 10 feet again") rather than instrument-derived numbers.

Coverage: complements BeachCitiesCubaScraper (Shaw's Cove). South
Coast Divers writes about Bluebird Canyon, Woods Cove, and live-cam
inferences from N/S Laguna — same zone, different reporting style.
"""
from __future__ import annotations

import json
import pathlib
import re
import time
from datetime import datetime, timezone

from . import _llm_extract
from ._base import BaseScraper


SOURCE_ROOT = "https://southcoastdivers.com"
BLOG_INDEX_URL = f"{SOURCE_ROOT}/blog"
POST_VIEW_URL = f"{SOURCE_ROOT}/php/display_blog_list.php?func=view&file="

# Default focal point for posts that don't explicitly name a spot —
# Laguna Beach centroid (between Shaw's Cove and Woods Cove). The
# spot lookup catches Woods Cove / Bluebird Canyon when named.
DEFAULT_SPOT = "Laguna"
DEFAULT_LAT, DEFAULT_LNG = 33.540, -117.780

# Filename pattern observed 2026-05-09: YYYYMMDDHHMMSS-<author>.txt
# Captured from `<a href="...?file=...">` anchors on the directory
# index. Tolerant of varying author suffixes (RichP today; could be
# something else in the future) so a future contributor change
# doesn't silently break the scraper.
_POST_FILE_RE = re.compile(
    r'file=(\d{14}-[A-Za-z0-9_]+\.txt)',
    re.IGNORECASE,
)

# How many recent posts to walk per cron tick. Same budget as Just
# Get Wet — anything older than 3 posts is past the 24h window
# score.py uses anyway.
MAX_POSTS_PER_RUN = 2

# Minimum prose length to attempt LLM extraction. Below this it's
# usually a "no dives this weekend" boilerplate where the LLM has
# nothing to extract and we'd just spend tokens for zero obs.
MIN_PROSE_CHARS = 60


def _load_spot_lookup() -> dict[str, tuple[float, float, str]]:
    """Reuse the shared spot lookup so all LLM-extracted sources
    resolve names the same way."""
    here = pathlib.Path(__file__).resolve().parent
    raw = json.loads((here / "_spot_lookup.json").read_text(encoding="utf-8"))
    out: dict[str, tuple[float, float, str]] = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list) and len(v) == 2:
            out[k.lower()] = (float(v[0]), float(v[1]), k)
    return out


def _resolve_spot(name: str, lookup: dict[str, tuple[float, float, str]]) -> tuple[str, float, float] | None:
    """Longest-substring-match spot resolver. Same shape as
    DiveViz uses; returns the canonical name + coords or None if
    we don't have coords for the LLM-named spot."""
    if not name:
        return None
    n = name.lower().strip()
    # Try exact match first.
    if n in lookup:
        lat, lng, canon = lookup[n]
        return (canon, lat, lng)
    # Longest substring match — "pt loma kelp" matches "Pt Loma Kelp"
    # before "Pt Loma" if both are in the text.
    candidates = [(k, v) for k, v in lookup.items() if k in n]
    if not candidates:
        return None
    candidates.sort(key=lambda x: -len(x[0]))
    _, (lat, lng, canon) = candidates[0]
    return (canon, lat, lng)


def _parse_post_filenames(html: str) -> list[str]:
    """Extract every `file=<timestamp>-author.txt` filename referenced
    on the index page, sorted newest-first. Dedup by filename."""
    seen: set[str] = set()
    ordered: list[str] = []
    for m in _POST_FILE_RE.finditer(html):
        fn = m.group(1)
        if fn in seen:
            continue
        seen.add(fn)
        ordered.append(fn)
    # Sort by leading 14-digit timestamp DESC.
    ordered.sort(key=lambda s: s[:14], reverse=True)
    return ordered


def _post_view_url(filename: str) -> str:
    return f"{POST_VIEW_URL}{filename}"


def _strip_html(html: str) -> str:
    """Cheap tag-stripper. The display_blog_list.php endpoint wraps
    plain prose in some HTML chrome; for LLM extraction we want
    just the body."""
    # Drop everything outside <body>...</body> if present.
    body = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    text = body.group(1) if body else html
    # Drop scripts + styles.
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags.
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace.
    text = re.sub(r"\s+", " ", text).strip()
    return text


class SouthCoastDiversScraper(BaseScraper):
    source_id = "dive-shop-southcoastdivers"
    source_confidence = 0.80
    source_root_url = BLOG_INDEX_URL

    # Same cadence policy as the other dive-shop blogs (Just Get
    # Wet, DiveViz). 30s between same-host requests so we walk the
    # index → 2 recent posts within one cron tick.
    host_rate_limit_s = 30
    _INTRA_PAUSE_S = 30

    def __init__(self):
        super().__init__()
        self._spot_lookup = _load_spot_lookup()

    def fetch(self) -> list[dict]:
        # No LLM key = no extraction possible. Same graceful skip
        # the other prose sources use.
        if not _llm_extract.is_enabled():
            print(f"  {self.source_id}: ANTHROPIC_API_KEY unset, skipping prose-only source")
            return []

        try:
            r_index = self._polite_get(BLOG_INDEX_URL)
        except Exception as exc:  # noqa: BLE001
            print(f"  {self.source_id}: index fetch: {exc.__class__.__name__}")
            return []

        filenames = _parse_post_filenames(r_index.text)
        if not filenames:
            print(f"  {self.source_id}: index has no recognized post filenames")
            return []

        out: list[dict] = []
        for i, fn in enumerate(filenames[:MAX_POSTS_PER_RUN]):
            if i > 0:
                time.sleep(self._INTRA_PAUSE_S)
            url = _post_view_url(fn)
            try:
                r_post = self._polite_get(url)
            except Exception as exc:  # noqa: BLE001
                print(f"  {self.source_id}: {fn}: {exc.__class__.__name__}")
                continue
            prose = _strip_html(r_post.text)
            if not prose or len(prose) < MIN_PROSE_CHARS:
                continue

            extracted = _llm_extract.extract_from_prose(prose)
            if not extracted:
                continue

            # Use the filename's timestamp as the observation time —
            # more accurate than the cron-fetch time for matching
            # against same-day predictions.
            ts_str = fn[:14]  # YYYYMMDDHHMMSS
            try:
                when = datetime.strptime(ts_str, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                when = datetime.now(timezone.utc)

            for seq, e in enumerate(extracted):
                spot_name = (e.get("spot_name") or "").strip()
                resolved = _resolve_spot(spot_name, self._spot_lookup)
                if resolved is None:
                    if spot_name:
                        print(f"  {self.source_id}: unknown spot {spot_name!r} — skipped")
                    continue
                canonical_name, lat, lng = resolved

                obs = {
                    "obs_id":               self.make_obs_id(canonical_name, seq=seq, when=when),
                    "timestamp_utc":        when.isoformat(timespec="minutes").replace("+00:00", "Z"),
                    "lat":                  float(lat),
                    "lng":                  float(lng),
                    "spot_name":            canonical_name,
                    "observed_secchi_ft":   e.get("observed_secchi_ft"),
                    "observed_sst_f":       e.get("observed_sst_f"),
                    "observed_swell_ft":    e.get("observed_swell_ft"),
                    "source":               self.source_id,
                    "source_url":           url,
                    "source_confidence":    self.source_confidence,
                    "extraction_method":    "llm",
                    "raw_excerpt":          (e.get("raw_excerpt") or "")[:240] or None,
                    "notes":                "South Coast Divers daily group post (Laguna)",
                }
                out.append(obs)

        return out
