"""Pacific Wilderness: weekly conditions posts from the San Pedro dive shop.

Pacific Wilderness (pacificwilderness.com, San Pedro) posts a conditions
report most Fridays on its Facebook page, and in Aug-Sep 2026 also on
Instagram. Its own WordPress site shows both feeds:

* the homepage carries the Instagram feed (the last 20 posts, each with an
  exact Unix timestamp in ``data-date`` and the full caption in the image
  ``alt``);
* the Dive Conditions page (``?page_id=676``) carries the Facebook feed (the
  last 5 posts, dated only "6 days ago", "2 weeks ago").

Both are rendered into the shop's HTML on the shop's server by the Smash
Balloon WordPress plugins, using the shop's own connection to Meta. This
scraper reads those two pacificwilderness.com pages and nothing else: it
never requests facebook.com, instagram.com, the Graph API or Meta's image
CDN. Meta's terms on automated collection cover Meta's own products; the
shop's site publishes no terms of use and no robots.txt rules (checked
2026-10-15, see CANDIDATES.md).

Posts name several sites at once:

    • Oil Rigs: 60+ ft visibility | 63°F at 60 ft
    we found 30–40 ft VIZ at Point Fermin and 15–25 ft VIZ at the breakwall.

``extract`` emits one reading per named site, at that site's coordinates,
with the raw sentence kept as the excerpt. Rules (deterministic, no LLM):

* the number must sit next to a visibility word ("25 ft VIZ", "visibility
  of 70 ft"), so surf, swell, depth and temperature numbers are never read;
* upper bounds ("up to", "less than") and plans or forecasts ("expect",
  "forecast", "looking ahead") are skipped; a floor ("60+", "over 40") is
  read as its number, as the other shop scrapers do;
* second-hand lines ("reported", "reports from") are skipped;
* week-long summaries ("all week", "throughout the week", "average ... over
  the last week", "last weekend") are skipped: no single day to pair;
* a reading is dated to the dive day the text names ("today", "last night",
  "yesterday", "last Sunday"), counted back from the post's own date in
  Pacific time; a sentence that continues the previous one ("On the way
  back, ...") keeps its day; a bullet with no day ("Point Fermin: 50 ft
  visibility") is a recent report from the week before the post, dated to
  the post and marked ``day not stated`` in notes;
* site names come from SITES below (the shared _spot_lookup.json maps
  "Cabrillo" to San Diego's, so this shop keeps its own table); an
  unknown site is skipped.

Facebook posts on the shop page carry only a relative age, so a post is
read only while that age is in minutes or hours (exact to the day). The
ingest cron runs every 6 hours, so each Friday post is caught on its day;
once it reads "1 day ago" it is left alone (and the orchestrator has
already stored it).

Confidence weight 0.80, as South Coast Divers: first-hand shop dives, but
prose and weekly recaps.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ._base import BaseScraper, slugify


SOURCE_ROOT = "https://pacificwilderness.com"
HOME_URL = f"{SOURCE_ROOT}/"
CONDITIONS_URL = f"{SOURCE_ROOT}/?page_id=676"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

# Sites the shop names, with coordinates. Shore entries are placed from the
# chart and street addresses to about 1 km; the viz grid is ~8 km, so that is
# well inside one cell. Rigs: Ellen/Elly from the Coast Guard safety-zone rule
# (33 CFR 147.1104), Edith offset from them by BOEM's state-plane table.
# Olympic II: the wreck lies about 3 mi south of Angels Gate light.
# (name, lat, lng, pattern); patterns are matched case-insensitively and the
# longest overlapping match wins ("Edith Oil Rig" over "Oil Rig").
SITES = [
    ("Veterans Park", 33.8385, -118.3915, r"veteran\W{0,2}s?\W{0,2}\s*park|\bvets park\b|\bredondo\b"),
    ("RAT Beach", 33.8080, -118.3930, r"\brat beach\b"),
    ("Malaga Cove", 33.8030, -118.3940, r"\bmalaga\b"),
    ("Flat Rock Point", 33.7985, -118.4070, r"\bflat rock\b"),
    ("Haggerty's", 33.7950, -118.4100, r"\bhagg?ert(?:y|ie)\W{0,2}s?\b"),
    ("Bluff Cove", 33.7900, -118.4140, r"\bbluff cove\b"),
    ("Honeymoon Cove", 33.7840, -118.4190, r"\bhoneymoon cove\b"),
    ("Lunada Bay", 33.7710, -118.4230, r"\blunada\b"),
    ("Terranea", 33.7375, -118.3970, r"\bterranea\b(?:\s*/\s*oml\b)?|\boml\b|old marineland|\bmarineland\b|\blong point\b"),
    ("Abalone Cove", 33.7380, -118.3770, r"\babalone cove\b"),
    ("Royal Palms", 33.7155, -118.3215, r"\broyal palms\b"),
    ("White Point", 33.7160, -118.3170, r"\bwhite\W{0,2}s? point\b|\bwhite point\b"),
    ("Point Fermin", 33.7055, -118.2935, r"\b(?:point|pt\.?) fermin\b|\bfermin\b"),
    ("Cabrillo Beach", 33.7085, -118.2835, r"\bcabrillo(?: beach)?\b"),
    ("Cabrillo breakwall", 33.7095, -118.2790, r"(?:\bcabrillo )?\bbreak ?(?:wall|water)\b"),
    ("Olympic II wreck", 33.6650, -118.2510, r"\bolympic(?: ii| 2)?\b(?: wreck)?"),
    ("Oil Rigs", 33.5825, -118.1283, r"\boil rigs?\b|\brigs\b|\b(?:platforms? )?(?:ellen|elly|eureka)\b"),
    ("Edith oil rig", 33.5960, -118.1410, r"\bedith\b(?:\W{0,3}\s*(?:oil\s+)?(?:rig|platform)s?)?"),
    ("Catalina", 33.3880, -118.4200, r"\bcatalina(?: island)?\b|\bavalon\b|\bcasino point\b|\bisthmus\b|\btwo harbors\b"),
]
_SITE_RES = [(name, lat, lng, re.compile(pat, re.I)) for name, lat, lng, pat in SITES]

_FT = r"(?:ft|feet|foot|')"
_VIZ_WORD = r"(?:viz|vis|visibility)\b"
# "30–40 ft VIZ", "60+ ft visibility", "50+ Feet of VIZ", "20 ft + VIZ"
_NUM_THEN_VIZ = re.compile(r"(?P<lo>\d{1,3})\s*\+?\s*(?:(?:-|–|—|to)\s*(?P<hi>\d{1,3})\s*\+?\s*)?" + _FT
                           + r"\s*\+?\s*(?:of\s+)?" + _VIZ_WORD, re.I)
# "visibility of 70 ft", "VIZ: 20 ft", "visibility of over of 40 feet", "viz is about 15 ft"
_VIZ_THEN_NUM = re.compile(_VIZ_WORD + r"\s*(?::|of|was|is|at|around|about|~)?\s*(?:(?:over|about|around)\s+)?(?:of\s+)?~?\s*"
                           r"(?P<lo>\d{1,3})\s*\+?\s*(?:(?:-|–|—|to)\s*(?P<hi>\d{1,3})\s*\+?\s*)?" + _FT, re.I)
_UPPER_BOUND = re.compile(r"(?:up to|less than|under|no (?:more|better) than|at most|as (?:high|good) as|below)\s*$", re.I)
_PLAN = re.compile(r"\bforecast|\bexpect|\blooking ahead\b|\bpredict|\banticipat|\bshould\b|\bwill\b|\bcould\b|\bhop(?:e|ing)\b"
                   r"|\bif\b|\blikely\b|\bpending\b|\bneed", re.I)
_SECOND_HAND = re.compile(r"\breported\b|\breports? (?:from|by)\b|\bheard\b|\baccording to\b|\bwe(?:'|’)?ve been told\b", re.I)
_SPAN = re.compile(r"\ball week\b|\bthroughout the week\b|\bthis week\b|\bover the (?:last|past) (?:week|month|few days)\b"
                   r"|\bthe (?:last|past) (?:week|month)\b|\blast week(?:end)?\b|\b(?:over )?the weekend\b|\bthis weekend\b"
                   r"|\bpast month\b|\baverage\b", re.I)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TODAY = re.compile(r"\btoday\b|\bthis (?:morning|afternoon|evening)\b|\btonight\b", re.I)
_YESTERDAY = re.compile(r"\byesterday\b|\blast night\b", re.I)
_LAST_DAY = re.compile(r"\b(?:last|this past|past|on)\s+(" + "|".join(_WEEKDAYS) + r")\b", re.I)
# A sentence that carries on from the one before it keeps that one's day.
_CONTINUES = re.compile(r"^\W*(?:on the way (?:back|home|out)|also\b|then\b|after(?:wards| that)?\b|later\b|there\b|that (?:afternoon|evening)\b)", re.I)
# Blank lines, rules ("====") and all-caps titles with no number ("WEEKEND FORECAST").
_HEADER = re.compile(r"^\W*$|^[=\-—_*~]{3,}|^[^a-z0-9]*$")

MIN_TEXT_CHARS = 40


@dataclass
class Post:
    url: str
    posted_at: datetime  # UTC
    text: str
    channel: str  # "instagram" or "facebook"


def _site_mentions(seg: str) -> list[tuple[int, int, str, float, float]]:
    found = []
    for name, lat, lng, rx in _SITE_RES:
        for m in rx.finditer(seg):
            found.append((m.start(), m.end(), name, lat, lng))
    found.sort(key=lambda f: (f[0], -(f[1] - f[0])))
    kept: list[tuple[int, int, str, float, float]] = []
    for f in found:
        if kept and f[0] < kept[-1][1]:
            if f[1] - f[0] > kept[-1][1] - kept[-1][0]:
                kept[-1] = f
            continue
        kept.append(f)
    return kept


def _site_for(seg: str, m: re.Match, mentions, bullet_site) -> tuple[str, float, float] | None:
    if bullet_site:
        return bullet_site
    after = [s for s in mentions if s[0] >= m.end() and s[0] - m.end() <= 30
             and re.match(r"^\W*(?:at|off|on|in|near|around|from|for|by)\b", seg[m.end():s[0]] + " ", re.I)]
    if after:
        return after[0][2:]
    before = [s for s in mentions if s[1] <= m.start()]
    if before:
        return before[-1][2:]
    return None


def _day_of(seg: str, posted_local: date) -> date | None:
    if _TODAY.search(seg):
        return posted_local
    if _YESTERDAY.search(seg):
        return posted_local - timedelta(days=1)
    m = _LAST_DAY.search(seg)
    if m:
        back = (posted_local.weekday() - _WEEKDAYS.index(m.group(1).lower())) % 7 or 7
        return posted_local - timedelta(days=back)
    return None


def _segments(text: str):
    """(segment, is_bullet, block_no): bullets and sentences, with a block
    counter that moves on at blank lines and header rules."""
    block = 0
    for line in text.replace("\r", "").split("\n"):
        raw = line.strip()
        if _HEADER.search(raw):
            block += 1
            continue
        bullet = bool(re.match(r"^[•*·\-–]\s*", raw)) or bool(re.match(r"^[^.!?:]{2,40}:\s*\d", raw))
        raw = re.sub(r"^[•*·\-–]\s*", "", raw)
        for sent in re.split(r"(?<=[.!?])\s+", raw):
            if sent.strip():
                yield sent.strip(), bullet, block


def extract(text: str, posted_at: datetime) -> list[dict]:
    """One reading per named site in a post: {site, lat, lng, lo_ft, hi_ft,
    sentence, day (local date), dated (bool)}."""
    posted_local = posted_at.astimezone(LOCAL_TZ).date()
    out: list[dict] = []
    seen: set[tuple[str, date]] = set()
    prev_day, prev_block = None, None
    for seg, bullet, block in _segments(text):
        day = _day_of(seg, posted_local)
        if day is None and prev_day and block == prev_block and _CONTINUES.search(seg):
            day = prev_day
        prev_day, prev_block = (day, block) if day else (prev_day if block == prev_block else None, block)
        if _PLAN.search(seg) or _SECOND_HAND.search(seg) or _SPAN.search(seg):
            continue
        mentions = _site_mentions(seg)
        bullet_site = None
        if bullet:
            head = re.match(r"^([^:|]{2,60}?)\s*:", seg)
            ms = _site_mentions(head.group(1)) if head else []
            bullet_site = ms[0][2:] if ms else None
            if head and not ms:
                continue  # a bullet about a site we have no coordinates for
        matches = sorted(list(_NUM_THEN_VIZ.finditer(seg)) + list(_VIZ_THEN_NUM.finditer(seg)), key=lambda m: m.start())
        last_end = -1
        for m in matches:
            if m.start() < last_end:
                continue  # the same number read by both patterns
            last_end = m.end()
            if _UPPER_BOUND.search(seg[max(0, m.start() - 20):m.start()]):
                continue
            site = _site_for(seg, m, mentions, bullet_site)
            if site is None:
                continue
            lo = float(m.group("lo"))
            hi = float(m.group("hi")) if m.group("hi") else lo
            lo, hi = min(lo, hi), max(lo, hi)
            if not 0 < hi <= 150:
                continue
            d = day or posted_local
            if (site[0], d) in seen:
                continue
            seen.add((site[0], d))
            out.append({"site": site[0], "lat": site[1], "lng": site[2], "lo_ft": lo, "hi_ft": hi,
                        "sentence": seg[:240], "day": d, "dated": day is not None})
    return out


def when_utc(reading: dict, posted_at: datetime) -> datetime:
    """The reading's time: the post time for a same-day reading, else noon
    Pacific on the dive day (the hour is not stated)."""
    if reading["day"] == posted_at.astimezone(LOCAL_TZ).date():
        return posted_at
    return datetime.combine(reading["day"], time(12), tzinfo=LOCAL_TZ).astimezone(timezone.utc)


# --- The two feeds on the shop's site --------------------------------------------

def _alt_text(item_html: str) -> str:
    m = re.search(r'<img\b[^>]*\balt="([^"]*)"', item_html, re.S)
    return html.unescape(m.group(1)) if m else ""


def instagram_posts(page_html: str) -> list[Post]:
    """The homepage's Instagram feed (Smash Balloon): exact timestamps."""
    out = []
    for m in re.finditer(r'<div class="sbi_item\b[^>]*?id="sbi_(\d+)"\s+data-date="(\d+)"[^>]*>(.*?)(?=<div class="sbi_item\b|\Z)',
                         page_html, re.S):
        body = m.group(3)
        text = _alt_text(body)
        if len(text) < MIN_TEXT_CHARS:
            continue
        link = re.search(r'href="(https://www\.instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+/?)"', body)
        url = link.group(1) if link else f"{HOME_URL}#sbi_{m.group(1)}"
        out.append(Post(url=url, posted_at=datetime.fromtimestamp(int(m.group(2)), timezone.utc), text=text,
                        channel="instagram"))
    return out


_AGE = re.compile(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago", re.I)


def facebook_posts(page_html: str, now: datetime) -> list[Post]:
    """The Dive Conditions page's Facebook feed (Smash Balloon). Only posts
    whose age still reads in minutes or hours: their day is exact."""
    out = []
    for chunk in re.split(r'<div role="listitem" id="cff_', page_html)[1:]:
        age = re.search(r'<p class="cff-date"\s*>\s*([^<]*)<', chunk)
        a = _AGE.search(age.group(1)) if age else None
        if not a or a.group(2).lower() not in ("second", "minute", "hour"):
            continue
        unit = {"second": 1, "minute": 60, "hour": 3600}[a.group(2).lower()]
        posted = now - timedelta(seconds=int(a.group(1)) * unit)
        # The text span, up to the "See More" toggle the plugin appends.
        body = re.search(r'<div class="cff-post-text"\s*>\s*<span class="cff-text"[^>]*>(.*?)(?:<span class="cff-expand"|</div>)',
                         chunk, re.S)
        if not body:
            continue
        text = re.sub(r'<img class="cff-linebreak"[^>]*>|<br\s*/?>', "\n", body.group(1))
        text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
        if len(text) < MIN_TEXT_CHARS:
            continue
        link = re.search(r'class="cff-viewpost-facebook" href="(https://www\.facebook\.com/[^"]+)"', chunk)
        url = link.group(1) if link else f"{CONDITIONS_URL}#cff_{chunk.split(chr(34), 1)[0]}"
        out.append(Post(url=url, posted_at=posted, text=text, channel="facebook"))
    return out


class PacificWildernessScraper(BaseScraper):
    source_id = "dive-shop-pacificwilderness"
    source_confidence = 0.80
    source_root_url = CONDITIONS_URL

    # Two pages per run (homepage, then the conditions page), 30 s apart.
    host_rate_limit_s = 30

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        posts: list[Post] = []
        for url, parse in ((HOME_URL, instagram_posts), (CONDITIONS_URL, lambda h: facebook_posts(h, now))):
            try:
                posts += parse(self._polite_get(url).text)
            except Exception as exc:  # noqa: BLE001
                print(f"  {self.source_id}: {url}: {exc.__class__.__name__}")
        out: list[dict] = []
        for post in posts:
            for r in extract(post.text, post.posted_at):
                when = when_utc(r, post.posted_at)
                out.append(self.row(post, r, when))
        return out

    def row(self, post: Post, r: dict, when: datetime) -> dict:
        channel = "Instagram" if post.channel == "instagram" else "Facebook"
        day = "dive day stated" if r["dated"] else "day not stated: a recent report from the week before the post"
        return {
            "obs_id":               self.make_obs_id(r["site"], seq=0, when=when),
            "timestamp_utc":        when.isoformat(timespec="minutes").replace("+00:00", "Z"),
            "lat":                  r["lat"],
            "lng":                  r["lng"],
            "spot_name":            r["site"],
            "observed_secchi_ft":   (r["lo_ft"] + r["hi_ft"]) / 2,
            "observed_lo_ft":       r["lo_ft"],
            "observed_hi_ft":       r["hi_ft"],
            "day_stated":           r["dated"],
            "observed_sst_f":       None,
            "observed_swell_ft":    None,
            "source":               self.source_id,
            # One link per site so the report keeps each site's reading apart.
            "source_url":           f"{post.url}#{slugify(r['site'])}",
            "source_confidence":    self.source_confidence,
            "extraction_method":    "regex",
            "raw_excerpt":          r["sentence"],
            "notes":                f"Pacific Wilderness weekly conditions post ({channel}, read on the shop's site); {day}",
        }
