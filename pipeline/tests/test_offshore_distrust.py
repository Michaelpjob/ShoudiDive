"""Tests for the offshore over-optimism distrust adjustment (viz clarity honesty).

Diagnosed 2026-07-06: Northeast Bank off San Diego read ~44 ft while the water
was green at 15-20 ft. Ocean-color chl misses sub-pixel / subsurface green
offshore, so the derived clarity over-claims. High offshore visibility only
ever comes from a low satellite chl the sensor can't verify, so the distrust
keys on the SYMPTOM: offshore distance x over-optimistic viz.

Run: python -m pytest pipeline/tests/test_offshore_distrust.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from viz_predict import visibility, config  # noqa: E402


def test_nearshore_never_distrusted():
    """Within the offshore-start distance, distrust is zero regardless of viz."""
    d = visibility.offshore_chl_distrust(
        np.array([44.0, 60.0, 80.0]),
        np.array([0.0, 3.0, config.OFFSHORE_DISTRUST_START_KM]),
    )
    assert np.allclose(d, 0.0)


def test_offshore_implausibly_clear_is_fully_distrusted():
    d = visibility.offshore_chl_distrust(
        np.array([config.OFFSHORE_TRUST_FULL_FT, 70.0]),
        np.array([config.OFFSHORE_DISTRUST_FULL_KM, 60.0]),
    )
    assert np.allclose(d, 1.0)


def test_offshore_but_plausible_viz_not_distrusted():
    """Offshore cells reading a believable <=25 ft are trusted."""
    d = visibility.offshore_chl_distrust(
        np.array([15.0, 22.0, 25.0]),
        np.array([40.0, 40.0, 40.0]),
    )
    assert np.allclose(d, 0.0)


def test_northeast_bank_is_strongly_distrusted():
    """The actual failure case: ~44 ft, ~35 km offshore → strong distrust."""
    d = float(visibility.offshore_chl_distrust(np.array([44.0]), np.array([35.0]))[0])
    assert d > 0.85


def test_factor_monotonic_in_distance_and_optimism():
    dist = np.array([8.0, 12.0, 20.0, 25.0])
    d = visibility.offshore_chl_distrust(np.full_like(dist, 44.0), dist)
    assert np.all(np.diff(d) >= 0) and d[0] == 0.0 and d[-1] > 0.9
    viz = np.array([25.0, 32.0, 40.0, 45.0])
    d2 = visibility.offshore_chl_distrust(viz, np.full_like(viz, 40.0))
    assert np.all(np.diff(d2) >= 0) and d2[0] == 0.0 and d2[-1] == 1.0


def _apply_pull(p10_ft, p50_ft, p90_ft, viz_p50_for_d, dist):
    d = float(visibility.offshore_chl_distrust(np.array([viz_p50_for_d]), np.array([dist]))[0])
    floor_ft = config.OFFSHORE_GREEN_FLOOR_FT
    out = {"p10": p10_ft, "p50": p50_ft, "p90": p90_ft}
    for key, scale in (("p10", 1.0), ("p50", 0.75)):
        cur = out[key]
        target = min(cur, floor_ft)
        frac = min(max(config.OFFSHORE_DISTRUST_PULL * d * scale, 0.0), 1.0)
        out[key] = cur * (1 - frac) + target * frac
    out["p10"] = min(out["p10"], out["p50"])
    return out


def test_pull_brings_northeast_bank_into_green_water_range():
    out = _apply_pull(p10_ft=38.0, p50_ft=44.0, p90_ft=52.0, viz_p50_for_d=44.0, dist=35.0)
    assert 15.0 <= out["p50"] <= 30.0, out          # median now realistic
    assert out["p10"] <= out["p50"]                  # band ordered
    assert out["p90"] == 52.0                        # optimistic bound untouched


def test_nearshore_clear_day_is_left_alone():
    out = _apply_pull(p10_ft=18.0, p50_ft=25.0, p90_ft=33.0, viz_p50_for_d=25.0, dist=2.0)
    assert (out["p10"], out["p50"], out["p90"]) == (18.0, 25.0, 33.0)
