"""Tests for the NaN-aware bilinear_sample in fetch_visibility.

The previous (plain) version propagated NaN: any one of the four corner
cells being NaN made the entire interpolated value NaN. That broke the
viz model whenever the lower-res chl_1d.png had a single-pixel bloom
speckle adjacent to NaN cells — bilinear_sample at the higher-res
viz grid returned NaN, the chl_2d/3d fallback then overwrote with
smoothed (clear-water) values, and viz predicted "Good" at the same
spot the chl LAYER painted as bloom. Discovered 2026-05-21 in Baja
production data.
"""
from __future__ import annotations

import os
import unittest.mock as mock

import numpy as np
import pytest

# bilinear_sample reads BBOX from the active region at import time;
# patch in a known one before importing.
_TEST_BBOX = {"lat_min": 22.0, "lat_max": 32.6, "lng_min": -118.0, "lng_max": -106.5}


@pytest.fixture()
def bilinear_sample():
    """Re-import fetch_visibility with a known BBOX so test grids are
    in the same coordinate system as the function expects."""
    os.environ.setdefault("SHOULDIDIVE_REGION", "baja")
    import importlib

    from pipeline import fetch_visibility as fv

    importlib.reload(fv)
    return fv.bilinear_sample


def _bbox_lng(fx: float) -> float:
    return _TEST_BBOX["lng_min"] + fx * (_TEST_BBOX["lng_max"] - _TEST_BBOX["lng_min"])


def _bbox_lat(fy: float) -> float:
    return _TEST_BBOX["lat_max"] - fy * (_TEST_BBOX["lat_max"] - _TEST_BBOX["lat_min"])


def test_all_corners_finite_uses_normal_bilinear(bilinear_sample):
    """Four-corner-finite case should match the textbook bilinear formula."""
    src = np.array([[10.0, 20.0],
                    [30.0, 40.0]], dtype=np.float64)
    # Sample at the geometric centre of the four cells.
    lng = np.array([_bbox_lng(0.5)])
    lat = np.array([_bbox_lat(0.5)])
    out = bilinear_sample(src, 2, 2, lng, lat)
    # Centre of (10, 20, 30, 40) bilinearly = 25.
    assert out[0] == pytest.approx(25.0)


def test_one_nan_corner_uses_valid_average(bilinear_sample):
    """The exact regression case: one NaN corner, three finite. Previous
    code returned NaN; new code returns the mean of the valid corners."""
    src = np.array([[np.nan, 4.74],
                    [0.08,   0.06]], dtype=np.float64)
    lng = np.array([_bbox_lng(0.5)])
    lat = np.array([_bbox_lat(0.5)])
    out = bilinear_sample(src, 2, 2, lng, lat)
    expected = (4.74 + 0.08 + 0.06) / 3
    assert out[0] == pytest.approx(expected, rel=1e-6)
    assert np.isfinite(out[0])


def test_three_nan_corners_returns_lone_valid_value(bilinear_sample):
    """If three corners are NaN, the bilinear collapses to the lone
    finite corner. (No interpolation possible; treat the cell as
    'sampled at the centroid of valid neighbours'.)"""
    src = np.array([[np.nan, np.nan],
                    [np.nan, 5.0]], dtype=np.float64)
    lng = np.array([_bbox_lng(0.5)])
    lat = np.array([_bbox_lat(0.5)])
    out = bilinear_sample(src, 2, 2, lng, lat)
    assert out[0] == pytest.approx(5.0)


def test_all_nan_corners_returns_nan(bilinear_sample):
    """Genuine no-data cells stay NaN — viz pipeline relies on this to
    classify cells as 'climatology only'."""
    src = np.full((2, 2), np.nan, dtype=np.float64)
    lng = np.array([_bbox_lng(0.5)])
    lat = np.array([_bbox_lat(0.5)])
    out = bilinear_sample(src, 2, 2, lng, lat)
    assert np.isnan(out[0])


def test_speckle_in_low_res_chl_no_longer_collapses_to_nan(bilinear_sample):
    """The exact production failure: chl=4.74 at one pixel, NaN
    neighbours, clear water elsewhere. Sampling at the speckle pixel
    centre must return a finite value the viz model can use, not NaN
    (which would let the chl_2d/3d fallback override with climo).
    """
    # 5x5 chl grid: NaN cluster + 4.74 speckle + clear water surround.
    src = np.full((5, 5), 0.08, dtype=np.float64)
    src[2, 2] = 4.74
    src[1, 1] = np.nan
    src[1, 2] = np.nan
    src[2, 1] = np.nan
    # Sample at the speckle position (grid-frac 0.5, 0.5 of the 5x5 grid).
    lng = np.array([_bbox_lng(2 / 4)])
    lat = np.array([_bbox_lat(2 / 4)])
    out = bilinear_sample(src, 5, 5, lng, lat)
    assert np.isfinite(out[0]), (
        "speckle position should sample to a finite value; "
        "regression of the 2026-05-21 NaN-propagation bug"
    )
