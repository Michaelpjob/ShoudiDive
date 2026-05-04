"""BD Outdoors — XenForo fishing/spear forum RSS feeds.

BD Outdoors hosts active fishing + spearfishing forums whose region
sub-forums have stable RSS endpoints at
``/forums/forum/<slug>/index.rss``. Spear posts in particular are
the highest-density public source of California water-visibility
prose anywhere on the open web — the audience is divers reporting
their own observations to peers.

Why this fills a real gap:

  * Central CA had **zero** dive-shop scrapers — Monterey/Big Sur
    posts here are the first signal we'll see for that lat band.
  * Channel Islands lost Truth Aquatics + Eagle 4. The spear and
    SoCal-offshore feeds cover Anacapa/SCI/SBI on liveaboard trip
    reports (Sundiver, Pacific Star, Magician, etc).
  * Bight-offshore (Cortes/Tanner/Farnsworth) has no dive shop at
    all — but spearos do post viz from those banks.

Like DiveViz, the post bodies are unstructured prose, so we route
through the same Haiku extractor. Two cost guards on top:

  1. Window — only LLM-process items with ``pubDate`` newer than
     ``MAX_AGE_HOURS`` (default 72). Old posts get dedup'd by obs_id
     anyway, but skipping them saves the LLM round-trip.
  2. Keyword pre-filter — if the post body mentions zero CA spot
     keywords AND no viz/temp number, skip the LLM call. About half
     of forum posts are "FOUND gear" / "Gun ID" / off-topic threads.

Confidence weight 0.85 — same as Just Get Wet and DiveViz. Forum
posts are individuals' first-hand observations, not professional
reports, so probably should bias slightly lower than dive shops
once we have enough data to score per-source calibration; for now
holding parity until n grows.
"""
from __future__ import annotations

import html
import pathlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import _llm_cache, _llm_extract
from ._base import BaseScraper


FEEDS = [
    {
        "id": "central-ca-fishing",
        "url": "https://www.bdoutdoors.com/forums/forum/central-california-fishing-reports/index.rss",
        "label": "Central CA fishing reports",
    },
    {
        "id": "socal-offshore",
        "url": "https://www.bdoutdoors.com/forums/forum/southern-california-offshore-fishing-reports/index.rss",
        "label": "SoCal offshore reports",
    },
    {
        "id": "ca-sport",
        "url": "https://www.bdoutdoors.com/forums/forum/california-sport-fishing-reports/index.rss",
        "label": "CA sport fishing reports",
    },
    {
        "id": "spear",
        "url": "https://www.bdoutdoors.com/forums/forum/spear-fishing-reports/index.rss",
        "label": "Spearfishing reports (CA-filtered)",
    },
]

MAX_AGE_HOURS = 72         # only LLM-process posts < 72h old
MAX_ITEMS_PER_FEED = 10    # cap LLM cost per run (cache dedup keeps repeat
                           # cost ~zero, so this can be wider than the
                           # original 6 without blowing the budget)

# RSS namespace used by XenForo for <content:encoded>
_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
}

# Cheap pre-filter: if the body mentions any of these keywords we're
# fairly sure it's worth the LLM call. Otherwise it's likely off-topic
# (gear-found, gun ID, etc). Widened 2026-05-04 to catch diving slang
# the original regex missed ("blown out", "epic", "crystal", etc.).
_VIZ_KEYWORDS = re.compile(
    r"\b(?:vis(?:ibility)?|viz|water clarity|gin[\s-]?clear|murk|murky|"
    r"dirty water|clean water|warm water|cold water|temp(?:erature)?|sst|°|"
    r"blown[\s-]?out|crystal|epic|stoked|gnar|nuked|"
    r"green water|brown water|chocolate|chunky|"
    r"[0-9]{1,3}\s*(?:ft|feet|degrees|°[FC]?))\b",
    re.IGNORECASE,
)


class BdOutdoorsScraper(BaseScraper):
    source_id = "forum-bdoutdoors"
    source_confidence = 0.85
    source_root_url = "https://www.bdoutdoors.com"

    # XenForo is on Cloudflare; it tolerates a tighter cadence than the
    # default 5-min host floor, but each fetch() iteration only hits one
    # URL per feed (the RSS itself) so the floor is irrelevant in
    # practice. Keep the default.

    def __init__(self):
        super().__init__()
        self._spot_lookup = _load_spot_lookup()
        # Pre-build a single regex matching any spot keyword so we can
        # cheaply pre-filter posts before paying for the LLM round-trip.
        keys = sorted(self._spot_lookup.keys(), key=len, reverse=True)
        if keys:
            self._spot_re = re.compile(
                r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b",
                re.IGNORECASE,
            )
        else:
            self._spot_re = None

    def fetch(self) -> list[dict]:
        if not _llm_extract.is_enabled():
            print(f"  {self.source_id}: ANTHROPIC_API_KEY unset, skipping prose-only source")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        # Per-post LLM-call dedup. The hourly cron sees the same posts
        # for ~3-4 days; without this we'd LLM each one ~24-96 times.
        cache = _llm_cache.load()
        out: list[dict] = []
        cache_skips = 0
        for feed in FEEDS:
            try:
                items = self._fetch_feed(feed["url"], cutoff)
            except Exception as exc:  # noqa: BLE001
                print(f"  {self.source_id}: {feed['id']} feed fetch: "
                      f"{exc.__class__.__name__}: {exc}")
                continue

            kept = 0
            for item in items[:MAX_ITEMS_PER_FEED]:
                cache_key = f"bdoutdoors:{item.get('guid') or item['link']}"
                if _llm_cache.seen(cache, cache_key):
                    cache_skips += 1
                    continue
                obs = self._parse_item(item, feed=feed)
                # Mark as LLM'd whether or not we got obs back — the
                # purpose is to skip re-LLM, not to force a re-try on
                # zero-obs (a no-quantitative-data post stays no-data).
                _llm_cache.mark(cache, cache_key)
                out.extend(obs)
                if obs:
                    kept += 1
            print(f"  {self.source_id}: {feed['id']} → "
                  f"{len(items)} fresh items, {kept} produced obs")
        if cache_skips:
            print(f"  {self.source_id}: cache-skipped {cache_skips} posts (already LLM'd)")
        _llm_cache.save(_llm_cache.prune(cache))
        return out

    # ---- RSS fetch + parse ---------------------------------------------

    def _fetch_feed(self, url: str, cutoff: datetime) -> list[dict]:
        """Fetch one RSS, parse, return items newer than cutoff."""
        r = self._polite_get(url)
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as exc:
            print(f"  {self.source_id}: XML parse failed: {exc}")
            return []

        channel = root.find("channel")
        if channel is None:
            return []

        items: list[dict] = []
        for el in channel.findall("item"):
            pub = _parse_pubdate(_text(el.find("pubDate")))
            if pub is None or pub < cutoff:
                continue
            content_el = el.find("content:encoded", _NS)
            body_html = _text(content_el) or _text(el.find("description"))
            body_text = _strip_html(body_html)
            if not body_text or len(body_text) < 30:
                continue
            items.append({
                "title":   _text(el.find("title")) or "",
                "link":    _text(el.find("link")) or "",
                "pubdate": pub,
                "guid":    _text(el.find("guid")) or "",
                "author":  _text(el.find("dc:creator", _NS)) or "",
                "body":    body_text,
            })
        # Newest first — XenForo puts them that way already, but normalize
        # to defend against feed reorderings.
        items.sort(key=lambda x: x["pubdate"], reverse=True)
        return items

    # ---- Per-item parsing ----------------------------------------------

    def _parse_item(self, item: dict, *, feed: dict) -> list[dict]:
        body = item["body"]
        # Cheap pre-filter: skip if no spot keyword AND no viz/temp keyword.
        # Saves ~half the LLM calls on a typical run.
        spot_hit = bool(self._spot_re and self._spot_re.search(body))
        kw_hit = bool(_VIZ_KEYWORDS.search(body))
        if not spot_hit and not kw_hit:
            return []

        extracted = _llm_extract.extract_from_prose(body)
        if not extracted:
            return []

        out: list[dict] = []
        # Use the post's own pubDate so re-runs on the same UTC day
        # collapse via obs_id, while a post that crosses UTC midnight
        # still gets two distinct ids (matches the existing scrapers'
        # convention).
        when = item["pubdate"]
        for seq, e in enumerate(extracted):
            spot_name = (e.get("spot_name") or "").strip()
            resolved = self._resolve_spot(spot_name)
            if resolved is None:
                # Spear forum names are messy ("the Bait", "the Wall");
                # log + skip rather than fabricate coords.
                if spot_name:
                    print(f"  {self.source_id}: unknown spot "
                          f"{spot_name!r} — skipped")
                continue
            canonical, lat, lng = resolved
            # Per-feed seq offset so a post that names the same spot
            # across two feeds doesn't dedup-collide.
            obs_seq = seq + _feed_seq_offset(feed["id"])
            out.append({
                "obs_id":             self.make_obs_id(canonical, seq=obs_seq, when=when),
                "timestamp_utc":      when.strftime("%Y-%m-%dT%H:%MZ"),
                "lat":                float(lat),
                "lng":                float(lng),
                "spot_name":          canonical,
                "observed_secchi_ft": _safe_num(e.get("observed_secchi_ft")),
                "observed_sst_f":     _safe_num(e.get("observed_sst_f")),
                "observed_swell_ft":  _safe_num(e.get("observed_swell_ft")),
                "source":             self.source_id,
                "source_url":         item["link"],
                "source_confidence":  self.source_confidence,
                "extraction_method":  "llm-haiku",
                "raw_excerpt":        (e.get("raw_excerpt") or body[:280])[:280],
                "notes":              f"feed={feed['id']}; author={item['author']}",
            })
        return out

    def _resolve_spot(self, name: str) -> tuple[str, float, float] | None:
        if not name:
            return None
        candidates = sorted(self._spot_lookup.keys(), key=len, reverse=True)
        name_lc = name.lower()
        for k in candidates:
            if k.lower() in name_lc or name_lc in k.lower():
                lat, lng = self._spot_lookup[k]
                return k, float(lat), float(lng)
        return None


# ---- Helpers ---------------------------------------------------------

def _text(el) -> str | None:
    if el is None:
        return None
    t = el.text or ""
    return t.strip() or None


def _parse_pubdate(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str | None) -> str:
    """Strip tags + decode entities. XenForo bodies are tame HTML — no
    embedded scripts — so a regex strip is fine and avoids pulling
    BeautifulSoup just for this."""
    if not s:
        return ""
    text = _TAG_RE.sub(" ", s)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _feed_seq_offset(feed_id: str) -> int:
    """Stable seq offset per feed so two feeds tagging the same spot
    on the same day don't dedup-collide on obs_id."""
    return {
        "central-ca-fishing": 100,
        "socal-offshore":     200,
        "ca-sport":           300,
        "spear":              400,
    }.get(feed_id, 0)


def _safe_num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_spot_lookup() -> dict[str, list[float]]:
    import json  # local — only this scraper needs it
    p = pathlib.Path(__file__).parent / "_spot_lookup.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}
