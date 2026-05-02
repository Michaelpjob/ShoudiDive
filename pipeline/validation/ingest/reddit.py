"""Reddit — CA-tagged dive/spear post scraper (r/scuba, r/spearfishing).

Reddit's public JSON API exposes per-subreddit search at
``/r/{sub}/search.json?q=...`` with no auth required for read. We
scope each search to a subreddit and to the last week, OR-joining
California spot keywords so we only fetch posts likely to be
relevant.

Subreddits ingested:

    r/scuba         — global, CA keyword search
    r/spearfishing  — global, CA keyword search

Why not r/sandiego / r/socal / r/norcal: those are geographic but
overwhelmingly non-diving content (concerts, traffic, restaurant
recommendations). Diver-population subs with a CA filter is the
right shape — high signal-to-noise.

Why not Reddit comments: most viz reports actually live in comments
("vis was 30 ft today at La Jolla") rather than post bodies. But
fetching every comment tree blows up LLM cost ~10x. Posts-only first;
comments are a follow-up if the signal proves thin.

Confidence weight 0.80 — slightly below dive shops (0.85) since
random users have wider variance than working professionals. Once
n grows we can per-source-calibrate.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import _llm_extract
from ._base import BaseScraper


SUBREDDITS = [
    {"sub": "scuba",        "tag": "r-scuba"},
    {"sub": "spearfishing", "tag": "r-spear"},
]

# OR-joined search clause. Reddit's search URL-encodes itself, but we
# pass it through ``urllib.parse.quote`` to be explicit. These terms
# are picked to maximize CA-coverage recall while still being narrow
# enough that off-topic results (e.g. "Catalina" the airport) are rare
# in a diving-subreddit context.
SEARCH_TERMS = [
    "California", "SoCal", "NorCal",
    "San Diego", "La Jolla", "Pt Loma", "Point Loma",
    "Monterey", "Big Sur", "Carmel", "Pt Lobos", "Point Lobos",
    "Catalina", "Channel Islands", "Wreck Alley",
    "Cortes", "Tanner Bank", "Farnsworth",
    "Santa Barbara", "Santa Cruz Island", "Anacapa",
    "Malibu", "Palos Verdes", "Laguna",
]
SEARCH_QUERY = " OR ".join(f'"{t}"' if " " in t else t for t in SEARCH_TERMS)

MAX_AGE_HOURS = 168           # last week; Reddit search ``t=week`` already filters but enforce in-code too
MAX_ITEMS_PER_SUB = 8         # cap LLM cost per cron tick
MIN_BODY_CHARS = 60           # too-short bodies are usually image-only posts


# Same cheap pre-filter as BD Outdoors: skip posts that don't even
# mention a viz/temp keyword.
import re  # noqa: E402  (placed after constants for readability)
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
            for item in items[:MAX_ITEMS_PER_SUB]:
                obs = self._parse_item(item, source_tag=sr["tag"])
                out.extend(obs)
                if obs:
                    kept += 1
            print(f"  {self.source_id}: r/{sr['sub']} → "
                  f"{len(items)} fresh posts, {kept} produced obs")
        return out

    # ---- Reddit search + parse ----------------------------------------

    def _fetch_subreddit(self, sub: str, cutoff: datetime) -> list[dict]:
        url = (f"https://www.reddit.com/r/{sub}/search.json"
               f"?q={quote(SEARCH_QUERY)}"
               f"&restrict_sr=on&sort=new&t=week&limit=25")
        r = self._polite_get(url)
        try:
            data = r.json()
        except json.JSONDecodeError as exc:
            print(f"  {self.source_id}: r/{sub} JSON parse: {exc}")
            return []

        children = data.get("data", {}).get("children", [])
        out: list[dict] = []
        for ch in children:
            d = (ch or {}).get("data") or {}
            created = d.get("created_utc")
            if not isinstance(created, (int, float)):
                continue
            posted = datetime.fromtimestamp(created, tz=timezone.utc)
            if posted < cutoff:
                continue

            title = (d.get("title") or "").strip()
            body = (d.get("selftext") or "").strip()
            if not title:
                continue
            # Combine title + body — Reddit titles often carry the
            # location and body carries the conditions, e.g.
            # "Diving La Jolla today" / "vis was 25-30 ft, water 64°F"
            full_text = f"{title}\n\n{body}".strip()
            if len(full_text) < MIN_BODY_CHARS:
                continue

            permalink = d.get("permalink") or ""
            full_url = ("https://www.reddit.com" + permalink) if permalink else ""

            out.append({
                "title":   title,
                "body":    full_text,
                "pubdate": posted,
                "permalink_id": d.get("id") or "",
                "author":  d.get("author") or "",
                "url":     full_url,
            })
        # Already newest-first via sort=new, but normalize.
        out.sort(key=lambda x: x["pubdate"], reverse=True)
        return out

    # ---- Per-item parsing ----------------------------------------------

    def _parse_item(self, item: dict, *, source_tag: str) -> list[dict]:
        body = item["body"]
        spot_hit = bool(self._spot_re and self._spot_re.search(body))
        kw_hit = bool(_VIZ_KEYWORDS.search(body))
        if not spot_hit and not kw_hit:
            return []

        extracted = _llm_extract.extract_from_prose(body)
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
                "raw_excerpt":        (e.get("raw_excerpt") or body[:280])[:280],
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
