"""Ground Truth Engine PR-1 — ingestion + scoring de-risk + honesty.

Locks the four bug-class fixes that PR-1 ships:
  * Just Get Wet visibility routes through the shared metric-aware parser
  * South Coast Divers reads the canonical extractor keys (not viz_ft)
  * per_zone_metrics de-dups identical residuals (n / Pearson r integrity)
  * the ingest runner distinguishes empty from ok (silently-dead scrapers)
    and tags sources by reliability tier
"""
from __future__ import annotations

import pathlib

from pipeline.validation.ingest import _source_status, _tier, STRUCTURED_SOURCES
from pipeline.validation.ingest.justgetwet import _extract_visibility
from pipeline.validation.score import per_zone_metrics


# ---- Just Get Wet: metric-aware visibility via the shared parser ----

def test_jgw_visibility_feet_range_midpoint():
    assert _extract_visibility("Vis: 0-10ft, swell 3 at 5s") == 5.0
    assert _extract_visibility("Visibility 15-20 feet today") == 17.5
    assert _extract_visibility("vis: 25'") == 25.0


def test_jgw_visibility_metric_no_longer_dropped():
    # The old per-source regex required ft and silently dropped this.
    v = _extract_visibility("Vis: 6-10 meters at the cove")
    assert v is not None, "metric visibility report should not be dropped"
    assert 25.0 <= v <= 27.5  # midpoint 8 m -> ~26.2 ft


def test_jgw_visibility_absent_returns_none():
    assert _extract_visibility("Great dive, saw a sea lion") is None


# ---- South Coast Divers: canonical extractor keys --------------------

def test_scd_reads_canonical_extractor_keys():
    src = (
        pathlib.Path(__file__).resolve().parents[1]
        / "validation" / "ingest" / "southcoastdivers.py"
    ).read_text(encoding="utf-8")
    # Must read the keys the extractor actually emits...
    assert 'e.get("observed_secchi_ft")' in src
    assert 'e.get("observed_sst_f")' in src
    assert 'e.get("raw_excerpt")' in src
    # ...and must NOT read the old wrong keys that nulled every observation.
    assert 'e.get("viz_ft")' not in src
    assert 'e.get("excerpt")' not in src


# ---- score.py: residual de-dup ---------------------------------------

def _resid(source, p50, observed, zone="bight_nearshore"):
    return {
        "source": source,
        "predicted_p50_ft": p50,
        "observed_ft": observed,
        "residual_ft": p50 - observed,
        "in_p10_p90": False,
        "zone": zone,
        "source_confidence": 0.85,
    }


def test_per_zone_metrics_collapses_identical_residuals():
    # Two byte-identical residuals from one source + one genuinely different
    # reading -> n should be 2, not 3 (the prod la-jolla-0/la-jolla-2 bug).
    residuals = [
        _resid("dive-shop-justgetwet", 14.9, 10.0),
        _resid("dive-shop-justgetwet", 14.9, 10.0),  # exact dup
        _resid("dive-shop-diveviz", 9.8, 25.0),
    ]
    m = per_zone_metrics(residuals)
    assert m["bight_nearshore"]["n"] == 2


def test_per_zone_metrics_keeps_distinct_observations():
    # Same source + same prediction but DIFFERENT observed value = a real
    # second data point, not a dup.
    residuals = [
        _resid("dive-shop-justgetwet", 14.9, 10.0),
        _resid("dive-shop-justgetwet", 14.9, 18.0),
    ]
    m = per_zone_metrics(residuals)
    assert m["bight_nearshore"]["n"] == 2


# ---- ingest: empty vs ok vs failed + reliability tiers ---------------

def test_source_status_distinguishes_empty():
    assert _source_status(0, None) == "empty"
    assert _source_status(3, None) == "ok"
    assert _source_status(0, "RuntimeError: boom") == "failed"
    assert _source_status(5, "RuntimeError: boom") == "failed"  # error wins


def test_tier_classification():
    assert _tier("cdip-buoy") == "A_structured"
    assert _tier("ndbc-buoy") == "A_structured"
    assert _tier("dive-shop-justgetwet") == "B_community"
    assert _tier("reddit-ca-divers") == "B_community"
    assert {"cdip-buoy", "ndbc-buoy", "cencoos", "rcca-mpa-baseline"} <= set(STRUCTURED_SOURCES)
