"""Eagle 4 Pacific dive-shop daily logs.

Eagle 4 publishes daily reports from their dive boat trips with site
name + visibility (ft) + water temp (°F). Confidence weight 0.85 —
working divers reporting actual conditions, with mild marketing
incentive to overstate.

This scraper is BEST-EFFORT. The handoff URL
``eagle4pacific.com/dive-reports/`` was unverified; in our first run
it didn't resolve, so the scraper:

* Tries the documented URL plus a couple of common WP-blog fallbacks
* On any HTTP / parse failure, logs a one-line warning and returns []
* Catches "site moved or restructured" without taking down the loop

The orchestrator already wraps this in try/except — what matters here
is that we don't silently emit garbage. When somebody verifies a
working URL + selector we replace ``CANDIDATE_URLS`` and the parsing
selectors below.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ._base import BaseScraper, parse_visibility_ft, parse_temp_f, slugify


CANDIDATE_URLS = [
    # As-listed in 02-report-sources.md.
    "https://eagle4pacific.com/dive-reports/",
    # Common WordPress fallbacks if the path moved:
    "https://eagle4pacific.com/blog/",
    "https://www.eagle4pacific.com/dive-reports/",
]

# Local spot-name lookup — this is intentionally small for v1; the
# bigger ``_spot_lookup.json`` is for the prose-extracted sources.
# Eagle 4's daily reports are mostly Channel Islands + nearshore SD,
# so we hand-curate a tiny lookup. If the scraper sees a spot name
# we don't know, it logs (in ``notes``) and skips that observation —
# we never guess coordinates.
SPOT_LATLNG: dict[str, tuple[float, float]] = {
    "Anacapa":              (34.013, -119.398),
    "Anacapa Island":       (34.013, -119.398),
    "Santa Cruz Island":    (34.000, -119.740),
    "Santa Cruz":           (34.000, -119.740),
    "Santa Rosa Island":    (33.970, -120.100),
    "San Miguel Island":    (34.040, -120.370),
    "Catalina":             (33.388, -118.420),
    "Catalina Island":      (33.388, -118.420),
    "San Clemente Island":  (32.920, -118.500),
    "Pt Loma":              (32.671, -117.273),
    "Pt. Loma":             (32.671, -117.273),
    "Point Loma":           (32.671, -117.273),
    "La Jolla":             (32.852, -117.272),
    "La Jolla Cove":        (32.852, -117.272),
    "Coronado Islands":     (32.420, -117.270),
    "Las Coronados":        (32.420, -117.270),
}


class Eagle4Scraper(BaseScraper):
    source_id = "dive-shop-eagle4"
    source_confidence = 0.85
    source_root_url = CANDIDATE_URLS[0]

    def fetch(self) -> list[dict]:
        # Lazy-import bs4 so a missing dependency in CI doesn't take
        # down the entire ingest run via the top-level orchestrator's
        # import — the bash workflow `pip install`s BeautifulSoup4
        # before invoking ingest.
        try:
            from bs4 import BeautifulSoup  # noqa: PLC0415
        except ImportError:
            print(f"  {self.source_id}: bs4 not installed, skipping")
            return []

        text = None
        used_url = None
        for url in CANDIDATE_URLS:
            try:
                r = self._polite_get(url)
                text = r.text
                used_url = url
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  {self.source_id}: {url} -> {exc.__class__.__name__}")
                continue

        if text is None:
            print(f"  {self.source_id}: all candidate URLs failed; emitting 0 obs")
            return []

        soup = BeautifulSoup(text, "html.parser")
        return list(self._parse(soup, used_url))

    # ---- parsing --------------------------------------------------

    def _parse(self, soup, source_url: str):
        """Try a couple of likely WP/blog structures.

        v1 is conservative: we only emit observations we're confident
        about. If the site uses an unfamiliar structure, this returns
        nothing and the orchestrator continues with the rest of the
        sources. A future PR can teach this scraper the actual
        markup once we verify it.
        """
        emitted = 0

        # Pattern 1: explicit `.dive-report-entry` cards (handoff schema).
        for entry in soup.select(".dive-report-entry, .dive-report, .report-entry"):
            spot_el = entry.select_one(".location, .site, .dive-site, .spot-name, h2, h3")
            viz_el  = entry.select_one(".visibility, .viz, [data-field=visibility]")
            sst_el  = entry.select_one(".water-temp, .temp, [data-field=temp]")
            if not spot_el:
                continue
            obs = self._maybe_obs(
                spot_text=spot_el.get_text(" ", strip=True),
                viz_text=viz_el.get_text(" ", strip=True) if viz_el else None,
                sst_text=sst_el.get_text(" ", strip=True) if sst_el else None,
                excerpt=entry.get_text(" ", strip=True),
                source_url=source_url,
                seq=emitted,
            )
            if obs is not None:
                emitted += 1
                yield obs

        # Pattern 2: WordPress posts with structured `<article>` cards.
        # We only fall back to this if Pattern 1 found nothing — saves
        # us double-yielding the same observation in mixed layouts.
        if emitted == 0:
            for article in soup.select("article.post, article.dive-report"):
                heading = article.find(["h1", "h2", "h3"])
                if not heading:
                    continue
                excerpt = article.get_text(" ", strip=True)
                obs = self._maybe_obs(
                    spot_text=heading.get_text(" ", strip=True),
                    viz_text=excerpt,
                    sst_text=excerpt,
                    excerpt=excerpt,
                    source_url=source_url,
                    seq=emitted,
                )
                if obs is not None:
                    emitted += 1
                    yield obs

        if emitted == 0:
            print(f"  {self.source_id}: parsed {used_url(soup)} but found 0 known structures")

    # ---- helpers --------------------------------------------------

    def _maybe_obs(
        self,
        *,
        spot_text: str,
        viz_text: str | None,
        sst_text: str | None,
        excerpt: str,
        source_url: str,
        seq: int,
    ):
        """Build an observation if (a) we recognise the spot name and
        (b) we got at least one numeric reading."""
        spot = _resolve_spot(spot_text)
        if spot is None:
            return None
        name, (lat, lng) = spot
        viz_ft = parse_visibility_ft(viz_text) if viz_text else None
        sst_f  = parse_temp_f(sst_text) if sst_text else None
        if viz_ft is None and sst_f is None:
            return None
        now = datetime.now(timezone.utc)
        return {
            "obs_id":             self.make_obs_id(name, seq=seq, when=now),
            "timestamp_utc":      now.isoformat(timespec="minutes").replace("+00:00", "Z"),
            "lat":                float(lat),
            "lng":                float(lng),
            "spot_name":          name,
            "observed_secchi_ft": viz_ft,
            "observed_sst_f":     sst_f,
            "observed_swell_ft":  None,
            "source":             self.source_id,
            "source_url":         source_url,
            "source_confidence":  self.source_confidence,
            "extraction_method":  "html-card",
            "raw_excerpt":        (excerpt or "")[:280],
            "notes":              None,
        }


def _resolve_spot(text: str):
    """Return (canonical_name, (lat, lng)) or None.

    Looks for any of the lookup keys as a substring of the heading
    text; longest match wins so 'Pt. Loma' beats 'Loma' if both
    were ever in the table.
    """
    if not text:
        return None
    candidates = sorted(SPOT_LATLNG.keys(), key=len, reverse=True)
    for k in candidates:
        if k.lower() in text.lower():
            return k, SPOT_LATLNG[k]
    return None


def used_url(_soup):
    # Tiny shim used only in the "no rows parsed" log line — the
    # caller already has the URL but nesting it through the parser
    # closure was awkward.
    return "<page>"
