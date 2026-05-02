"""Reddit — CA-tagged dive/spear post scraper (r/scuba, r/spearfishing).

Reddit's per-subreddit search endpoint (``/r/{sub}/search.json``)
returns 403 from GitHub Actions IP ranges — Reddit aggressively
bot-blocks cloud egress. The Atom feed at ``/r/{sub}/.rss`` sits
on a separate path with looser rate-limit policy and works from
the CI runner.

Trade-off: Atom doesn't accept a search query, so we fetch the
newest ~25 posts from each subreddit and rely on the spot/viz
keyword pre-filter to drop non-CA noise before paying for an
LLM round-trip. Maybe 30% of posts pass the pre-filter; of those
maybe 1 in 10 has a quantitative viz number.

Subreddits ingested:

    r/scuba         — global, post-hoc CA filter
    r/spearfishing  — global, post-hoc CA filter

Why not r/sandiego / r/socal: those are geographic but
overwhelmingly non-diving content. Diver-population subs with a
CA-spot-keyword filter is the right shape — high signal-to-noise.

Why not Reddit comments: most viz reports actually live in
comments rather than post bodies. But fetching every comment
tree blows up LLM cost ~10x. Posts-only first; comments are a
follow-up if the signal proves thin.

Confidence weight 0.80 — slightly below dive shops (0.85) since
random users have wider variance than working professionals.
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from . import _llm_extract
from ._base import BaseScraper


SUBREDDITS = [
    {"sub": "scuba",        "tag": "r-scuba"},
    {"sub": "spearfishing", "tag": "r-spear"},
]

MAX_AGE_HOURS = 168           # last week
MAX_ITEMS_PER_SUB = 12        # pre-filter is cheap, so cast a wider net here than RSS
MIN_BODY_CHARS = 60           # too-short bodies are usually image-only posts

# Atom namespace (what Reddit uses for /r/.rss)
_NS = {"atom": "http://www.w3.org/2005/Atom"}

# Reddit wraps the actual post body between these markers in the
# `<content type="html">` payload. Stripping outside them removes
# image previews + "submitted by /u/X" boilerplate.
_SELFTEXT_RE = re.compile(r"<!--\s*SC_OFF\s*-->(.*?)<!--\s*SC_ON\s*-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# Cheap pre-filter: skip posts that don't even mention a viz/temp keyword.
_VIZ_KEYWORDS = re.compile(
    r"\b(?:vis(?:ibility)?|viz|water clarity|gin clear|murk|dirty water|clean water|"
    r"warm water|cold water|temp(?:erature)?|sst|°|[0-9]{1,3}\s*(?:ft|feet|degrees))\b",
    re.IGNORECASE,
)


class RedditCAScraper(BaseScraper):
    source_id = "reddit-ca-divers"
    source_confidence = 0.80
    source_root_url = "https://www.reddit.com"

    def __init__(self):
        super().__init__()
        self._spot_lookup = _load_spot_lookup()
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
            print(f"  {self.source_id}: ANTHROPIC_API_KEY unset, "
                  f"skipping prose-only source")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        out: list[dict] = []
        for sr in SUBREDDITS:
            try:
                items = self._fetch_subreddit(sr["sub"], cutoff)
            except Exception as exc:  # noqa: BLE001
                print(f"  {self.source_id}: r/{sr['sub']} fetch failed: "
                      f"{exc.__class__.__name__}: {exc}")
                continue

            kept = 0
            ca_relevant = 0
            for item in items[:MAX_ITEMS_PER_SUB]:
                # Pre-filter happens here so we can count CA-relevant
                # vs total in the log line.
                if not self._ca_relevant(item["body"]):
                    continue
                ca_relevant += 1
                obs = self._parse_item(item, source_tag=sr["tag"])
                out.extend(obs)
                if obs:
                    kept += 1
            print(f"  {self.source_id}: r/{sr['sub']} → "
                  f"{len(items)} fresh posts, {ca_relevant} CA-relevant, "
                  f"{kept} produced obs")
        return out

    # ---- Reddit Atom fetch + parse ------------------------------------

    def _fetch_subreddit(self, sub: str, cutoff: datetime) -> list[dict]:
        url = f"https://www.reddit.com/r/{sub}/.rss"
        r = self._polite_get(url)
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as exc:
            print(f"  {self.source_id}: r/{sub} XML parse: {exc}")
            return []

        out: list[dict] = []
        for entry in root.findall("atom:entry", _NS):
            published_el = entry.find("atom:published", _NS)
            posted = _parse_atom_date(_text(published_el))
            if posted is None or posted < cutoff:
                continue

            title = _text(entry.find("atom:title", _NS)) or ""
            content_el = entry.find("atom:content", _NS)
            content_html = _text(content_el) or ""
            selftext = _extract_selftext(content_html)
            full_text = f"{title}\n\n{selftext}".strip()
            if len(full_text) < MIN_BODY_CHARS and not selftext:
                # Very short title-only post (likely image). Title alone
                # rarely contains both spot AND number, so skip.
                continue

            link_el = entry.find("atom:link", _NS)
            href = link_el.get("href") if link_el is not None else ""
            author_el = entry.find("atom:author/atom:name", _NS)

            out.append({
                "title":   title,
                "body":    full_text,
                "pubdate": posted,
                "url":     href or "",
                "author":  _text(author_el) or "",
            })
        out.sort(key=lambda x: x["pubdate"], reverse=True)
        return out

    # ---- CA pre-filter ------------------------------------------------

    def _ca_relevant(self, body: str) -> bool:
        spot_hit = bool(self._spot_re and self._spot_re.search(body))
        kw_hit = bool(_VIZ_KEYWORDS.search(body))
        # Want BOTH a CA spot AND a viz/temp keyword. Spear/scuba
        # subs are global so a viz number alone is usually a non-CA
        # trip report, and a spot mention alone (e.g. "I'm thinking
        # of diving Catalina next month") has no quantitative signal.
        return spot_hit and kw_hit

    # ---- Per-item parsing ---------------------------------------------

    def _parse_item(self, item: dict, *, source_tag: str) -> list[dict]:
        extracted = _llm_extract.extract_from_prose(item["body"])
        if not extracted:
            return []

        when = item["pubdate"]
        out: list[dict] = []
        for seq, e in enumerate(extracted):
            spot_name = (e.get("spot_name") or "").strip()
            resolved = self._resolve_spot(spot_name)
            if resolved is None:
                if spot_name:
                    print(f"  {self.source_id}: unknown spot "
                          f"{spot_name!r} — skipped")
                continue
            canonical, lat, lng = resolved
            obs_seq = seq + _sub_seq_offset(source_tag)
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
                "source_url":         item["url"],
                "source_confidence":  self.source_confidence,
                "extraction_method":  "llm-haiku",
                "raw_excerpt":        (e.get("raw_excerpt") or item["body"][:280])[:280],
                "notes":              f"sub={source_tag}; author={item['author']}",
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


def _parse_atom_date(s: str | None) -> datetime | None:
    """Atom timestamps are ISO 8601 like '2026-05-02T18:23:45+00:00'.
    Python's fromisoformat handles the +00:00 form natively in 3.11+."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _extract_selftext(content_html: str) -> str:
    """Reddit wraps the actual post body in <!-- SC_OFF -->...<!-- SC_ON -->
    inside the entry's <content type="html"> payload. Everything outside
    those markers is image previews and 'submitted by /u/X' chrome.
    """
    if not content_html:
        return ""
    decoded = html.unescape(content_html)
    m = _SELFTEXT_RE.search(decoded)
    if not m:
        return ""
    body_html = m.group(1)
    # Strip remaining tags + collapse whitespace
    text = _TAG_RE.sub(" ", body_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _sub_seq_offset(tag: str) -> int:
    """Stable seq offset per subreddit so the same spot mentioned in
    two subs the same day doesn't dedup-collide on obs_id."""
    return {
        "r-scuba": 500,
        "r-spear": 600,
    }.get(tag, 0)


def _safe_num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_spot_lookup() -> dict[str, list[float]]:
    p = pathlib.Path(__file__).parent / "_spot_lookup.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}
