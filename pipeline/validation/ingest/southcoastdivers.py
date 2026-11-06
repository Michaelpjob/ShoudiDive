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

The format is prose without labelled fields. ``extract_viz`` reads the
observed range deterministically from the first paragraph (current
conditions; the second paragraph is plans, e.g. "gets back to 10-15
feet"). Posts with no number there fall back to
``_llm_extract.extract_from_prose`` when ``ANTHROPIC_API_KEY`` is set.

2026-10-15 fixes: the LLM rows were read with the wrong keys (viz_ft /
excerpt instead of observed_secchi_ft / raw_excerpt), so all 176 rows
since May carried no number; and the filename time is Pacific local,
not UTC. Posts are read from the plain-text /blog/<file>.txt copy.

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
from zoneinfo import ZoneInfo

from . import _llm_extract
from ._base import BaseScraper


SOURCE_ROOT = "https://southcoastdivers.com"
BLOG_INDEX_URL = f"{SOURCE_ROOT}/blog"
POST_VIEW_URL = f"{SOURCE_ROOT}/php/display_blog_list.php?func=view&file="
POST_TEXT_URL = f"{SOURCE_ROOT}/blog/"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

# Observed visibility in the conditions paragraph: "the viz still looks low
# at around 5-7 feet", "3-5 feet at best", "10-12 feet of viz".
_VIZ_WORD = r"(?:viz|vis|visibility)"
_NUM = r"(?P<lo>\d{1,3})\+?(?:\s*(?:-|to|\u2013)\s*(?P<hi>\d{1,3})\+?)?\s*(?:feet|foot|ft)\b"
_VIZ_THEN_NUM = re.compile(_VIZ_WORD + r"\b(?P<gap>[^.!?]{0,90}?)" + _NUM, re.I)
_NUM_THEN_VIZ = re.compile(r"(?P<gap>)" + _NUM + r"\s+of\s+" + _VIZ_WORD + r"\b", re.I)
# Words between "viz" and the number that make it a plan, a hope, a bound,
# the past, or another quantity rather than what the water is now.
_NOT_NOW = re.compile(r"at least|\bneed|gets? back|\bhop(?:e|ing)|\bcould\b|\bshould\b|\bwould\b|\bwill\b|forecast|\bif\b"
                      r"|less than|\bunder\b|no better than|up to|as high as|\bwas\b|\bwere\b|\bhad\b"
                      r"|surf|swell|wave|tide|wind|depth|deep", re.I)
_BOUND_AFTER = re.compile(r"^\s*(?:(?:&|and|or)\s+less|(?:of\s+)?(?:surf|swell|waves?)\b)", re.I)
# Just before "viz": generic or hoped-for statements ("usually the viz is").
_LEAD_NOT_NOW = re.compile(r"usually|typically|normally|\bshould\b|\bhop(?:e|ing)\b|\bif\b", re.I)
# Second-hand reports ("I read Luis' dive report ...") describe an earlier dive.
_SECOND_HAND = re.compile(r"dive reports?\b|\breport(?:ed|s)? (?:from|by)\b", re.I)
# Sentences about the weekend or yesterday: a Monday post's "Over the w/e,
# viz was 15-20 feet" is not Monday's water.
_PAST_LEAD = re.compile(r"^(?:over the w/?e|over the weekend|(?:well, )?yesterday|last week)", re.I)

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


def _post_text_url(filename: str) -> str:
    return f"{POST_TEXT_URL}{filename}"


def post_time_utc(filename: str) -> datetime | None:
    """The filename's YYYYMMDDHHMMSS is Pacific local time."""
    try:
        local = datetime.strptime(filename[:14], "%Y%m%d%H%M%S").replace(tzinfo=LOCAL_TZ)
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def conditions_paragraph(text: str) -> str:
    """The first paragraph with substance after the greeting line, with line
    breaks inside it joined (a number can wrap: "10\nfeet")."""
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|<br>\s*<br>", text)]
    for p in paras:
        body = re.sub(r"^good morning divers!*\s*[\d/]+,?\s*\w*day\s*", "", p, flags=re.I).strip()
        if len(body) >= 60:
            return body
    return ""


def extract_viz(text: str) -> dict | None:
    """Observed visibility range from a post's conditions paragraph, or None.
    Returns {lo_ft, hi_ft, sentence}; skips plans, hopes, upper bounds and
    ranges that belong to surf or depth."""
    para = conditions_paragraph(text)
    for sent in re.split(r"(?<=[.!?])\s+", para):
        if _PAST_LEAD.search(sent) or _SECOND_HAND.search(sent):
            continue
        found = sorted(list(_VIZ_THEN_NUM.finditer(sent)) + list(_NUM_THEN_VIZ.finditer(sent)), key=lambda m: m.start("lo"))
        for m in found:
            if (_NOT_NOW.search(m.group("gap")) or _BOUND_AFTER.search(sent[m.end():m.end() + 14])
                    or _LEAD_NOT_NOW.search(sent[max(0, m.start() - 30):m.start()])):
                continue
            lo = float(m.group("lo"))
            hi = float(m.group("hi")) if m.group("hi") else lo
            if hi < lo:
                lo, hi = hi, lo
            if 0 < hi <= 100:
                return {"lo_ft": lo, "hi_ft": hi, "sentence": sent[:240]}
    return None


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
                prose = self._polite_get(_post_text_url(fn)).text
            except Exception as exc:  # noqa: BLE001
                print(f"  {self.source_id}: {fn}: {exc.__class__.__name__}")
                continue
            if not prose or len(prose) < MIN_PROSE_CHARS:
                continue
            # The filename's timestamp is the observation time (Pacific
            # local), more accurate than the cron-fetch time.
            when = post_time_utc(fn) or datetime.now(timezone.utc)

            viz = extract_viz(prose)
            if viz:
                out.append({
                    "obs_id":               self.make_obs_id(DEFAULT_SPOT, seq=0, when=when),
                    "timestamp_utc":        when.isoformat(timespec="minutes").replace("+00:00", "Z"),
                    "lat":                  DEFAULT_LAT,
                    "lng":                  DEFAULT_LNG,
                    "spot_name":            DEFAULT_SPOT,
                    "observed_secchi_ft":   (viz["lo_ft"] + viz["hi_ft"]) / 2,
                    "observed_sst_f":       None,
                    "observed_swell_ft":    None,
                    "source":               self.source_id,
                    "source_url":           url,
                    "source_confidence":    self.source_confidence,
                    "extraction_method":    "regex",
                    "raw_excerpt":          viz["sentence"],
                    "notes":                "South Coast Divers daily post (Laguna); mostly webcam estimates",
                })
                continue
            if not _llm_extract.is_enabled():
                continue
            extracted = _llm_extract.extract_from_prose(_strip_html(prose))
            if not extracted:
                continue

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
