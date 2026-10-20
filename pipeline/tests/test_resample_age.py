"""Regression tests for fetch_visibility.resample_age.

decode_age_png encodes "no data" as 999.0 (finite), and bilinear_sample only
skips NaN corners, so resampling the raw sidecar averaged real ages with 999
along every coast/cloud edge. On the 2026-10-19 CA run the model saw chl as
30.06 days old on average (true: 1.04) and kd490 as 102.43 (true: 11.00), and
sent those nearshore cells to climatology. A resampled age must never exceed
the oldest real age in the source.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

_TEST_BBOX = {"lat_min": 22.0, "lat_max": 32.6, "lng_min": -118.0, "lng_max": -106.5}


@pytest.fixture()
def fv():
    os.environ.setdefault("SHOULDIDIVE_REGION", "baja")
    import importlib

    from pipeline import fetch_visibility as mod

    importlib.reload(mod)
    return mod


def _grid(n):
    lng = np.linspace(_TEST_BBOX["lng_min"], _TEST_BBOX["lng_max"], n)
    lat = np.linspace(_TEST_BBOX["lat_max"], _TEST_BBOX["lat_min"], n)
    return np.meshgrid(lng, lat)


def test_sentinel_does_not_bleed_into_real_ages(fv):
    # 1-day-old observations with a no-data (land) column on the right.
    age_src = np.full((4, 4), 1.0, dtype=np.float32)
    age_src[:, 3] = 999.0
    lng, lat = _grid(9)
    value = np.ones_like(lng)          # observation present everywhere it's sampled
    out = fv.resample_age(age_src, value, lng, lat)
    real = out[out < 999]
    assert real.size > 0
    assert real.max() == pytest.approx(1.0)   # old code produced up to ~999


def test_missing_observation_gets_sentinel(fv):
    age_src = np.full((4, 4), 2.0, dtype=np.float32)
    lng, lat = _grid(5)
    value = np.ones_like(lng)
    value[0, 0] = np.nan
    out = fv.resample_age(age_src, value, lng, lat)
    assert out[0, 0] == 999.0
    assert out[2, 2] == pytest.approx(2.0)


def test_all_sentinel_source_stays_sentinel(fv):
    age_src = np.full((3, 3), 999.0, dtype=np.float32)
    lng, lat = _grid(4)
    out = fv.resample_age(age_src, np.ones_like(lng), lng, lat)
    assert np.all(out == 999.0)
