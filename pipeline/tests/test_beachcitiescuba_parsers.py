"""Unit tests for the Beach Cities Scuba scraper's regex parsers.

The scraper itself hits the network in fetch(), so this test file
exercises the *parser* helpers against synthetic HTML in the exact
format observed at https://beachcitiescuba.com/pages/current-conditions
on 2026-05-09. If the shop's page format ever changes, these tests
break loud + fast — better than silently returning zero observations
and waiting for the watchdog's "source silent >24h" rule to fire 24
hours later.

Run:
    python -m pytest pipeline/tests/test_beachcitiescuba_parsers.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.ingest.beachcitiescuba import (  # noqa: E402
    SHAWS_COVE,
    _identify_spot,
    _parse_reported_at,
    _parse_temp_f,
    _parse_visibility,
    _parse_waves,
)


# Realistic snippet matching the on-page format observed 2026-05-09.
# Whitespace + ordering is what the live site emits.
SAMPLE_HTML = """
<html><body>
<div class="conditions">
<h2>Shaw's Cove, Laguna Beach</h2>
<p>Reported: 8am, May 9, 2026</p>
<ul>
  <li>Visibility: 5-8ft</li>
  <li>Waves: 2-3ft</li>
  <li>Surge: Light</li>
  <li>Water Temperature: 62°F</li>
  <li>Air Temperature: 63°F</li>
</ul>
</div>
</body></html>
"""


def test_visibility_range_averages_to_midpoint():
    viz, excerpt = _parse_visibility("Visibility: 5-8ft")
    assert viz == 6.5
    assert "5" in excerpt and "8" in excerpt


def test_visibility_single_number():
    viz, _ = _parse_visibility("Visibility: 25ft")
    assert viz == 25.0


def test_visibility_with_ft_word_form():
    viz, _ = _parse_visibility("vis: 30 feet")
    assert viz == 30.0


def test_visibility_returns_none_when_absent():
    viz, excerpt = _parse_visibility("Air Temperature: 70F. No conditions today.")
    assert viz is None
    assert excerpt is None


def test_waves_range_averages():
    assert _parse_waves("Waves: 2-3ft") == 2.5


def test_waves_single():
    assert _parse_waves("Waves: 4ft") == 4.0


def test_temp_fahrenheit():
    assert _parse_temp_f("Water Temperature: 62°F") == 62.0


def test_temp_celsius_converts():
    # 17°C → 62.6°F
    assert _parse_temp_f("Water Temp: 17°C") == 62.6


def test_temp_implausible_returns_none():
    # 200°F is implausible — regex shouldn't return a phantom number
    # from "200 fishing reports posted" if the page changes shape.
    assert _parse_temp_f("Water Temperature: 200°F") is None


def test_reported_at_parses_morning():
    when = _parse_reported_at("Reported: 8am, May 9, 2026")
    assert when is not None
    # PT 8am = UTC 16:00 (PST) — the parser uses a flat +8h shift,
    # not real DST. Acceptable for a 24h-window join.
    assert when.day == 9
    assert when.month == 5
    assert when.year == 2026


def test_reported_at_returns_none_on_garbled():
    assert _parse_reported_at("no date here") is None


def test_identify_spot_default_is_shaws_cove():
    name, lat, lng = _identify_spot("the cove was great today")
    assert name == SHAWS_COVE[0]
    assert (lat, lng) == (SHAWS_COVE[1], SHAWS_COVE[2])


def test_identify_spot_recognizes_crescent_bay():
    name, lat, lng = _identify_spot("Crescent Bay had great visibility")
    assert name == "Crescent Bay"
    assert lat == 33.550
    assert lng == -117.795


def test_identify_spot_recognizes_woods_cove():
    name, _, _ = _identify_spot("Woods Cove had heavy surge")
    assert name == "Woods Cove"


def test_full_sample_extracts_all_fields():
    """End-to-end: every parser pulls its expected value from the
    realistic sample HTML."""
    viz, _ = _parse_visibility(SAMPLE_HTML)
    waves = _parse_waves(SAMPLE_HTML)
    temp = _parse_temp_f(SAMPLE_HTML)
    when = _parse_reported_at(SAMPLE_HTML)
    spot, _, _ = _identify_spot(SAMPLE_HTML)
    assert viz == 6.5
    assert waves == 2.5
    assert temp == 62.0
    assert when is not None and when.day == 9
    assert spot == "Shaw's Cove"


# ---------------------------------------------------------------------------
# Roster wiring — the scraper must be in SCRAPERS, otherwise the unit
# test passes but the cron never runs the scraper.
# ---------------------------------------------------------------------------


def test_scraper_is_in_orchestrator_roster():
    from validation.ingest import SCRAPERS  # noqa: WPS433
    from validation.ingest.beachcitiescuba import BeachCitiesCubaScraper

    found = any(isinstance(s, BeachCitiesCubaScraper) for s in SCRAPERS)
    assert found, (
        "BeachCitiesCubaScraper is not registered in SCRAPERS — the cron will skip it. "
        "Add it to the roster in pipeline/validation/ingest/__init__.py."
    )


def test_scraper_emits_secchi_under_realistic_input(monkeypatch):
    """Mock the polite_get to return our sample HTML; assert we get
    one well-formed observation back."""
    from validation.ingest.beachcitiescuba import BeachCitiesCubaScraper

    class FakeResponse:
        text = SAMPLE_HTML
        status_code = 200

        def raise_for_status(self):
            return None

    scraper = BeachCitiesCubaScraper()
    monkeypatch.setattr(scraper, "_polite_get", lambda url: FakeResponse())

    out = scraper.fetch()
    assert len(out) == 1
    obs = out[0]
    assert obs["observed_secchi_ft"] == 6.5
    assert obs["observed_swell_ft"] == 2.5
    assert obs["observed_sst_f"] == 62.0
    assert obs["spot_name"] == "Shaw's Cove"
    assert obs["source"] == "dive-shop-beachcitiescuba"
    assert obs["source_confidence"] == 0.85
    assert obs["lat"] == 33.547
    assert obs["lng"] == -117.792
    assert "obs_id" in obs and obs["obs_id"].startswith("dive-shop-beachcitiescuba-")
