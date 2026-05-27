"""Unit tests for pipeline/check_kelp_storm_risk.py — PR-K5-3 of the
kelp roadmap.

Validates the find_peak_event helper which scans the 5-day swell
summary for the worst storm window. Pure-function tests; no network,
no filesystem.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add pipeline/ to the import path so we can import check_kelp_storm_risk
# without needing it to be a package.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import check_kelp_storm_risk as ckr  # noqa: E402


def _summary(buckets_per_day):
    """Build a minimal swell summary structure suitable for
    find_peak_event. `buckets_per_day` is a list of lists of dicts
    with at least max_hs_ft + bucket name.
    """
    return {
        "anchor_date": "2026-05-26",
        "days": [
            {
                "day": d,
                "date": f"2026-05-2{6 + d}",
                "buckets": buckets,
            }
            for d, buckets in enumerate(buckets_per_day)
        ],
    }


def test_no_event_below_threshold():
    """All buckets below 8 ft → no peak."""
    s = _summary([
        [{"bucket": "midday", "max_hs_ft": 4.0}],
        [{"bucket": "morning", "max_hs_ft": 6.5}],
    ])
    assert ckr.find_peak_event(s) is None


def test_single_event_at_threshold_boundary():
    """Exactly at the threshold (8.0) is below the > comparison, so no
    event. This mirrors the script which uses < THRESHOLD as the bail
    (so >= triggers)."""
    s = _summary([
        [{"bucket": "afternoon", "max_hs_ft": 8.0,
          "mean_hs_ft": 7.5, "mean_tp_s": 14, "mean_dp_deg": 290}],
    ])
    peak = ckr.find_peak_event(s)
    assert peak is not None
    assert peak["max_hs_ft"] == 8.0
    assert peak["bucket"] == "afternoon"


def test_peak_picks_highest_across_all_days():
    """Multiple over-threshold buckets — peak is the global max."""
    s = _summary([
        [{"bucket": "midday", "max_hs_ft": 9.0}],
        [{"bucket": "afternoon", "max_hs_ft": 12.5, "mean_hs_ft": 11},
         {"bucket": "evening", "max_hs_ft": 10.0}],
        [{"bucket": "morning", "max_hs_ft": 8.5}],
    ])
    peak = ckr.find_peak_event(s)
    assert peak is not None
    assert peak["max_hs_ft"] == 12.5
    assert peak["bucket"] == "afternoon"
    assert peak["day"] == 1


def test_skips_buckets_with_invalid_max_hs():
    """A bucket missing or non-numeric max_hs_ft is skipped, not
    crashed on."""
    s = _summary([
        [{"bucket": "midday"}],  # no max_hs_ft
        [{"bucket": "afternoon", "max_hs_ft": None}],
        [{"bucket": "morning", "max_hs_ft": "n/a"}],
        [{"bucket": "evening", "max_hs_ft": 9.5}],
    ])
    peak = ckr.find_peak_event(s)
    assert peak is not None
    assert peak["max_hs_ft"] == 9.5


def test_empty_summary_returns_none():
    assert ckr.find_peak_event({}) is None
    assert ckr.find_peak_event({"days": []}) is None
    assert ckr.find_peak_event({"days": [{"date": "x"}]}) is None  # no buckets


def test_threshold_constant_matches_spec():
    """The 8.0 ft threshold + 7-day tail are spec values — assert them
    here so a future tweak is at least visible in a test diff."""
    assert ckr.STORM_HS_FT_THRESHOLD == 8.0
    assert ckr.WARNING_TAIL_DAYS == 7
