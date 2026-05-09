"""Unit tests for the South Coast Divers scraper's parsing helpers.

The scraper hits the network in fetch() + calls the LLM extractor;
this test file exercises only the deterministic helpers (filename
parsing, HTML stripping, spot resolution) against fixtures.

Run:
    python -m pytest pipeline/tests/test_southcoastdivers_parsers.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.ingest.southcoastdivers import (  # noqa: E402
    _parse_post_filenames,
    _post_view_url,
    _strip_html,
    _resolve_spot,
    _load_spot_lookup,
)


# Fixture: realistic shape of the index page returned by /blog. The
# actual page is a Linux-style directory listing of timestamped files.
INDEX_HTML = """
<html><body>
<h2>Index of /blog</h2>
<a href="/php/display_blog_list.php?func=view&file=20260507130022-RichP.txt">May 7</a><br>
<a href="/php/display_blog_list.php?func=view&file=20260508124039-RichP.txt">May 8</a><br>
<a href="/php/display_blog_list.php?func=view&file=20260506094500-RichP.txt">May 6</a><br>
<a href="/php/display_blog_list.php?func=view&file=20260101000000-LouisU.txt">Jan 1</a><br>
</body></html>
"""

POST_HTML = """
<html><head><title>South Coast Divers Blog</title></head>
<body>
<header>HOT DOGS!!!</header>
<script>var nope = "ignored";</script>
<style>body { color: red; }</style>
<div class="post">
Saturday 5/8/26. Got out for an early dive at Bluebird Canyon, just up
the beach from Woods Cove. Water was cold, around 60°F. Vis was about
8 feet — group consensus was that leaves me with the viz being around
10 feet again across the cove.
</div>
<footer>Visit our Facebook group for more.</footer>
</body></html>
"""


# ---------------------------------------------------------------------------
# Filename parsing — sort by timestamp DESC, dedup
# ---------------------------------------------------------------------------


def test_parse_post_filenames_returns_newest_first():
    fns = _parse_post_filenames(INDEX_HTML)
    # 4 unique entries
    assert len(fns) == 4
    # Newest first: May 8 → May 7 → May 6 → Jan 1
    assert fns[0] == "20260508124039-RichP.txt"
    assert fns[1] == "20260507130022-RichP.txt"
    assert fns[2] == "20260506094500-RichP.txt"
    assert fns[3] == "20260101000000-LouisU.txt"


def test_parse_post_filenames_dedups_repeated_anchors():
    html = INDEX_HTML + INDEX_HTML  # same anchors repeated twice
    fns = _parse_post_filenames(html)
    assert len(fns) == 4  # not 8


def test_parse_post_filenames_handles_empty():
    assert _parse_post_filenames("<html><body>nothing here</body></html>") == []


def test_parse_post_filenames_tolerates_other_authors():
    """A future contributor change shouldn't break the regex."""
    html = '<a href="?file=20260509083000-NewAuthor.txt">x</a>'
    assert _parse_post_filenames(html) == ["20260509083000-NewAuthor.txt"]


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


def test_post_view_url_includes_filename():
    url = _post_view_url("20260508124039-RichP.txt")
    assert url.endswith("file=20260508124039-RichP.txt")
    assert "display_blog_list.php" in url
    assert url.startswith("https://southcoastdivers.com/")


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------


def test_strip_html_drops_script_and_style():
    out = _strip_html(POST_HTML)
    assert "ignored" not in out  # <script> body
    assert "color: red" not in out  # <style> body
    assert "Vis was about 8 feet" in out  # body content preserved


def test_strip_html_collapses_whitespace():
    html = "<p>foo\n\n\n   bar</p>"
    assert _strip_html(html) == "foo bar"


def test_strip_html_handles_no_body_tag():
    html = "<div>hello world</div>"
    assert _strip_html(html) == "hello world"


# ---------------------------------------------------------------------------
# Spot resolution against the shared lookup
# ---------------------------------------------------------------------------


def test_resolve_spot_finds_woods_cove():
    lookup = _load_spot_lookup()
    out = _resolve_spot("Woods Cove", lookup)
    assert out is not None
    canon, lat, lng = out
    assert canon == "Woods Cove"
    assert lat == 33.530
    assert lng == -117.780


def test_resolve_spot_longest_match_wins():
    """When the LLM names "Pt Loma Kelp", we should match the kelp
    entry, not the more general "Pt Loma"."""
    lookup = _load_spot_lookup()
    out = _resolve_spot("Pt Loma Kelp", lookup)
    assert out is not None
    canon = out[0]
    # All three Pt Loma Kelp variants share the same coords
    assert "Kelp" in canon


def test_resolve_spot_returns_none_for_unknown():
    lookup = _load_spot_lookup()
    assert _resolve_spot("Nonexistent Reef", lookup) is None
    assert _resolve_spot("", lookup) is None


def test_resolve_spot_case_insensitive():
    lookup = _load_spot_lookup()
    a = _resolve_spot("La Jolla Cove", lookup)
    b = _resolve_spot("LA JOLLA COVE", lookup)
    c = _resolve_spot("la jolla cove", lookup)
    assert a == b == c
    assert a is not None


# ---------------------------------------------------------------------------
# Roster wiring
# ---------------------------------------------------------------------------


def test_scraper_is_in_orchestrator_roster():
    from validation.ingest import SCRAPERS  # noqa: WPS433
    from validation.ingest.southcoastdivers import SouthCoastDiversScraper

    found = any(isinstance(s, SouthCoastDiversScraper) for s in SCRAPERS)
    assert found, (
        "SouthCoastDiversScraper is not registered in SCRAPERS — "
        "the cron will skip it. Add it to the roster in "
        "pipeline/validation/ingest/__init__.py."
    )


def test_eagle4_scraper_no_longer_in_roster():
    """Eagle4 was retired 2026-05-09 (eagle4pacific.com DNS-fails).
    Make sure it doesn't accidentally come back."""
    from validation.ingest import SCRAPERS  # noqa: WPS433

    names = [getattr(s, "source_id", s.__class__.__name__) for s in SCRAPERS]
    assert "ingest_eagle4" not in names
    # The class name is what the orchestrator reports when source_id
    # is missing — also gate on the type to be safe.
    assert not any(s.__class__.__name__ == "Eagle4Scraper" for s in SCRAPERS)
