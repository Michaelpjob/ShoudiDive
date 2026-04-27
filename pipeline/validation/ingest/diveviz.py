"""DiveViz — daily SoCal dive reports, two blogs (San Diego + LA/OC).

DiveViz (diveviz.com) publishes two daily dive-condition blogs on the
same Shopify storefront:

  * /blogs/daily-dive-report          — San Diego (La Jolla, Pt Loma)
  * /blogs/la-and-oc-dive-conditions  — LA + Orange County (Laguna,
                                         PV, Catalina day-trips)

Unlike Just Get Wet — which uses explicit ``Vis: 0-10ft`` labels and
parses cleanly with regex — DiveViz reports are prose:

    "Conditions are cleaning up! Reports of 20-30' viz at wreck alley,
     most kelp and shore dive spots will be a bit lower than that
     but still good."

LLM extraction is the right tool here. We send each post body to
``_llm_extract.extract_from_prose`` and emit one obs per spot the
model identifies, anchored to the lat/lng in ``_spot_lookup.json``.

Without ``ANTHROPIC_API_KEY`` set, the LLM extractor gracefully
returns ``[]`` and this scraper emits zero obs (no crash). Once the
key is added as a GitHub secret, the LA-OC + SD coverage flips on
automatically — no code change needed.

Confidence weight 0.85 — same as Just Get Wet (working dive shops).
"""
from __future__ import annotations

import json
import pathlib
import re
import time
from datetime import datetime, timezone

from ._base import BaseScraper, slugify
from . import _llm_extract


# Each entry is (blog_handle, default_spot_name, default_lat, default_lng,
# region_label). ``default_*`` is what we use when the LLM can't pin a
# specific spot in the body — we still emit one obs per post anchored
# to the regional centroid, so empty-cell zones don't go entirely
# uncovered.
BLOGS = [
    {
        "handle":       "daily-dive-report",
        "default_spot": "La Jolla",
        "default_lat":  32.852,
        "default_lng":  -117.272,
        "region":       "san-diego",
    },
    {
        "handle":       "la-and-oc-dive-conditions",
        "default_spot": "Laguna",
        "default_lat":  33.540,
        "default_lng":  -117.780,
        "region":       "la-oc",
    },
]

ROOT = "https://diveviz.com"
MAX_POSTS_PER_BLOG = 2  # 2 newest per blog × 2 blogs = 4 fetches/run


# Same Shopify pattern as Just Get Wet: blog index has anchors at
# ``/blogs/{handle}/{slug}`` linking each post.
def _post_link_re(handle: str) -> re.Pattern:
    return re.compile(
        rf'href="(/blogs/{re.escape(handle)}/[a-z0-9\-]+)"',
        re.IGNORECASE,
    )


class DiveVizScraper(BaseScraper):
    source_id = "dive-shop-diveviz"
    source_confidence = 0.85
    source_root_url = ROOT

    # Shopify storefront, same cadence policy as Just Get Wet.
    host_rate_limit_s = 30
    _INTRA_PAUSE_S = 30

    def __init__(self):
        super().__init__()
        self._spot_lookup = _load_spot_lookup()

    def fetch(self) -> list[dict]:
        # Skip the whole network round-trip if the LLM is offline —
        # the prose-only sources can't extract anything without it.
        if not _llm_extract.is_enabled():
            print(f"  {self.source_id}: ANTHROPIC_API_KEY unset, skipping prose-only source")
            return []

        out: list[dict] = []
        for blog in BLOGS:
            try:
                obs = self._fetch_blog(blog)
                out.extend(obs)
            except Exception as exc:  # noqa: BLE001
                print(f"  {self.source_id}: {blog['handle']} failed: {exc.__class__.__name__}")
        return out

    def _fetch_blog(self, blog: dict) -> list[dict]:
        index_url = f"{ROOT}/blogs/{blog['handle']}"
        try:
            r = self._polite_get(index_url)
        except Exception as exc:  # noqa: BLE001
            print(f"  {self.source_id}: {blog['handle']} index fetch: {exc.__class__.__name__}")
            return []

        slugs = self._post_slugs(r.text, blog["handle"])
        if not slugs:
            return []

        out: list[dict] = []
        for i, slug in enumerate(slugs[:MAX_POSTS_PER_BLOG]):
            if i > 0:
                time.sleep(self._INTRA_PAUSE_S)
            url = f"{ROOT}{slug}"
            try:
                post_r = self._polite_get(url)
            except Exception as exc:  # noqa: BLE001
                print(f"  {self.source_id}: {slug} fetch: {exc.__class__.__name__}")
                continue
            obs = self._parse_post(post_r.text, url, blog=blog)
            out.extend(obs)
        return out

    @staticmethod
    def _post_slugs(html: str, handle: str) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for m in _post_link_re(handle).finditer(html):
            slug = m.group(1)
            if slug.endswith(f"/{handle}") or slug == f"/blogs/{handle}":
                continue
            if slug in seen:
                continue
            seen.add(slug)
            ordered.append(slug)
        return ordered

    def _parse_post(self, html: str, url: str, *, blog: dict) -> list[dict]:
        try:
            from bs4 import BeautifulSoup  # noqa: PLC0415
        except ImportError:
            print(f"  {self.source_id}: bs4 not installed")
            return []

        soup = BeautifulSoup(html, "html.parser")
        body_el = soup.select_one(".rte") or soup.find("article")
        if body_el is None:
            return []
        prose = body_el.get_text(" ", strip=True)
        if not prose or len(prose) < 30:
            return []

        # LLM does the heavy lifting: prose → list[{spot, viz, sst, swell, excerpt}].
        extracted = _llm_extract.extract_from_prose(prose)
        if not extracted:
            return []

        now = datetime.now(timezone.utc)
        out: list[dict] = []
        for seq, e in enumerate(extracted):
            spot_name = e.get("spot_name") or ""
            lat_lng = self._resolve_spot(spot_name)
            if lat_lng is None:
                # LLM named a spot we don't have coords for. Per
                # working agreement: log + skip, don't guess.
                print(f"  {self.source_id}: unknown spot {spot_name!r} — skipped")
                continue
            canonical_name, lat, lng = lat_lng
            obs_seq = seq + (10 if blog["region"] == "la-oc" else 0)
            out.append({
                "obs_id":             self.make_obs_id(canonical_name, seq=obs_seq, when=now),
                "timestamp_utc":      now.isoformat(timespec="minutes").replace("+00:00", "Z"),
                "lat":                float(lat),
                "lng":                float(lng),
                "spot_name":          canonical_name,
                "observed_secchi_ft": _safe_num(e.get("observed_secchi_ft")),
                "observed_sst_f":     _safe_num(e.get("observed_sst_f")),
                "observed_swell_ft":  _safe_num(e.get("observed_swell_ft")),
                "source":             self.source_id,
                "source_url":         url,
                "source_confidence":  self.source_confidence,
                "extraction_method":  "llm-haiku",
                "raw_excerpt":        (e.get("raw_excerpt") or prose[:280])[:280],
                "notes":              f"region={blog['region']}",
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
