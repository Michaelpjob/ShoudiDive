"""Unit tests for pipeline/lib/encode.py.

This test file is the gate that protects the byte-identical-output
contract for the PNG encoders. Stage 6 of the refactor centralises
the encoders in lib/encode.py; these tests assert that running the
new helpers produces the exact same bytes as the legacy in-file
encoders did, for a representative set of input arrays.

Strategy:
  We re-implement the LEGACY encoders inline (copied verbatim from
  fetch.py / fetch_climatology.py at the time of the refactor) and
  diff the on-disk byte content of (legacy, new) for the same input.
  If anyone ever tweaks lib/encode.py's pixel math, this test fires —
  catching exactly the failure class the constraint protects against.

Run:
    python -m pytest pipeline/tests/test_lib_encode.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.encode import (  # noqa: E402
    encode_age_sidecar_png,
    encode_linear_png,
    encode_log10_png,
)


# ---------------------------------------------------------------------------
# Legacy encoders — verbatim copies of the encoders the new lib replaces.
# DO NOT EDIT these to "match" any new behavior; they're the contract
# this test suite locks in. If the new helpers drift, that's a bug.
# ---------------------------------------------------------------------------


def _legacy_fetch_py_encode_png(arr, cfg, out):
    """Verbatim from pipeline/fetch.py::encode_png as of pre-refactor."""
    lo, hi = cfg["range"]
    if cfg["scale"] == "log10":
        with np.errstate(divide="ignore", invalid="ignore"):
            scaled = (np.log10(arr) - np.log10(lo)) / (np.log10(hi) - np.log10(lo))
    else:
        scaled = (arr - lo) / (hi - lo)
    valid = np.isfinite(scaled)
    px = np.zeros(arr.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out, optimize=True)


def _legacy_climatology_encode_linear(arr, lo, hi, out_path):
    """Verbatim from pipeline/fetch_climatology.py::encode_linear."""
    valid = np.isfinite(arr)
    scaled = (arr - lo) / (hi - lo)
    px = np.zeros(arr.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def _legacy_climatology_encode_log10(arr, lo, hi, out_path):
    """Verbatim from pipeline/fetch_climatology.py::encode_log10."""
    valid = np.isfinite(arr) & (arr > 0)
    log_lo, log_hi = np.log10(lo), np.log10(hi)
    px = np.zeros(arr.shape, dtype=np.uint8)
    if valid.any():
        scaled = (np.log10(arr[valid]) - log_lo) / (log_hi - log_lo)
        px[valid] = np.clip(np.round(scaled * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def _legacy_fetch_py_encode_age_png(age_arr, out):
    """Verbatim from pipeline/fetch.py::encode_age_png."""
    px = np.where(
        age_arr == 255, 0,
        np.minimum(age_arr.astype(np.int16) + 1, 255),
    ).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out, optimize=True)


# ---------------------------------------------------------------------------
# Sample arrays — representative of real fetcher outputs.
# ---------------------------------------------------------------------------


@pytest.fixture
def sst_like_array():
    """SST-shaped float32 array: most values inside [9, 25]°C, some NaN,
    some at endpoints, some slightly outside the range to exercise
    clipping."""
    np.random.seed(42)
    arr = np.random.uniform(9.0, 25.0, size=(50, 60)).astype(np.float32)
    arr[0, 0] = np.nan        # NaN cell
    arr[1, 0] = 5.0           # below range
    arr[1, 1] = 30.0          # above range
    arr[2, 0] = 9.0           # exact lo
    arr[2, 1] = 25.0          # exact hi
    arr[3, 0] = 17.0          # mid
    arr[5:7, 5:7] = np.nan    # NaN patch
    return arr


@pytest.fixture
def chl_like_array():
    """chl-shaped float32 array: log-distributed values inside [0.05, 20]
    mg/m³, some NaN, some zero (log10 undefined), some negative."""
    np.random.seed(7)
    log_vals = np.random.uniform(np.log10(0.05), np.log10(20.0), size=(40, 50))
    arr = (10 ** log_vals).astype(np.float32)
    arr[0, 0] = np.nan
    arr[0, 1] = 0.0           # log10(0) = -inf → pixel 0
    arr[0, 2] = -1.0          # log10(<0) = NaN → pixel 0
    arr[0, 3] = 0.05          # exact lo
    arr[0, 4] = 20.0          # exact hi
    arr[0, 5] = 0.5           # mid
    arr[2:4, 2:4] = np.nan
    return arr


@pytest.fixture
def age_like_array():
    """Age-days array: uint8 with sentinel 255 = no data, values
    0..30 = fresh..stale."""
    np.random.seed(11)
    arr = np.random.randint(0, 15, size=(30, 40), dtype=np.uint8)
    arr[0, 0] = 255           # sentinel
    arr[0, 1] = 0             # today's obs
    arr[0, 2] = 14            # 2 weeks back
    arr[1, :5] = 255          # row of sentinels
    return arr


# ---------------------------------------------------------------------------
# Byte-identical contract tests
# ---------------------------------------------------------------------------


def test_encode_linear_matches_fetch_py(sst_like_array, tmp_path):
    """Linear encoder output must be byte-identical to fetch.py's
    pre-refactor encode_png (linear branch)."""
    cfg = {"range": (9.0, 25.0), "scale": "linear"}

    legacy_path = tmp_path / "legacy.png"
    new_path = tmp_path / "new.png"
    _legacy_fetch_py_encode_png(sst_like_array, cfg, legacy_path)
    encode_linear_png(sst_like_array, 9.0, 25.0, new_path)

    assert legacy_path.read_bytes() == new_path.read_bytes()


def test_encode_linear_matches_fetch_climatology(sst_like_array, tmp_path):
    legacy_path = tmp_path / "legacy.png"
    new_path = tmp_path / "new.png"
    _legacy_climatology_encode_linear(sst_like_array, 9.0, 25.0, legacy_path)
    encode_linear_png(sst_like_array, 9.0, 25.0, new_path)

    assert legacy_path.read_bytes() == new_path.read_bytes()


def test_encode_log10_matches_fetch_py(chl_like_array, tmp_path):
    cfg = {"range": (0.05, 20.0), "scale": "log10"}

    legacy_path = tmp_path / "legacy.png"
    new_path = tmp_path / "new.png"
    _legacy_fetch_py_encode_png(chl_like_array, cfg, legacy_path)
    encode_log10_png(chl_like_array, 0.05, 20.0, new_path)

    assert legacy_path.read_bytes() == new_path.read_bytes()


def test_encode_log10_matches_fetch_climatology(chl_like_array, tmp_path):
    """fetch_climatology's encode_log10 uses a slightly different code
    path (mask-first, log10 on subset). Both produce the same pixel
    array."""
    legacy_path = tmp_path / "legacy.png"
    new_path = tmp_path / "new.png"
    _legacy_climatology_encode_log10(chl_like_array, 0.05, 20.0, legacy_path)
    encode_log10_png(chl_like_array, 0.05, 20.0, new_path)

    assert legacy_path.read_bytes() == new_path.read_bytes()


def test_encode_age_matches_fetch_py(age_like_array, tmp_path):
    legacy_path = tmp_path / "legacy.png"
    new_path = tmp_path / "new.png"
    _legacy_fetch_py_encode_age_png(age_like_array, legacy_path)
    encode_age_sidecar_png(age_like_array, new_path)

    assert legacy_path.read_bytes() == new_path.read_bytes()


# ---------------------------------------------------------------------------
# Sanity tests — decode round-trip
# ---------------------------------------------------------------------------


def test_encode_linear_round_trip_within_quantization(tmp_path):
    """Encode a known array, decode it, and assert recovered values are
    within one quantization step of the input."""
    arr = np.array([[10.0, 15.0, 20.0], [25.0, 17.0, np.nan]], dtype=np.float32)
    out = tmp_path / "x.png"
    encode_linear_png(arr, 9.0, 25.0, out)

    img = np.asarray(Image.open(out).convert("L"), dtype=np.float32)
    decoded = np.where(
        img == 0,
        np.nan,
        9.0 + (img - 1) / 254.0 * (25.0 - 9.0),
    )
    # NaN cells stay NaN
    assert np.isnan(decoded[1, 2])
    # Finite cells within one bucket
    bucket = (25.0 - 9.0) / 254.0
    diff = np.abs(decoded[~np.isnan(decoded)] - arr[~np.isnan(arr)])
    assert (diff < bucket * 1.1).all()


def test_encode_log10_round_trip_within_quantization(tmp_path):
    arr = np.array([[0.1, 1.0, 10.0], [5.0, 0.5, np.nan]], dtype=np.float32)
    out = tmp_path / "x.png"
    encode_log10_png(arr, 0.05, 20.0, out)

    img = np.asarray(Image.open(out).convert("L"), dtype=np.float32)
    log_lo, log_hi = np.log10(0.05), np.log10(20.0)
    decoded = np.where(
        img == 0,
        np.nan,
        10 ** (log_lo + (img - 1) / 254.0 * (log_hi - log_lo)),
    )
    assert np.isnan(decoded[1, 2])
    # log-bucket
    log_bucket = (log_hi - log_lo) / 254.0
    log_diff = np.abs(np.log10(decoded[~np.isnan(decoded)])
                      - np.log10(arr[~np.isnan(arr)]))
    assert (log_diff < log_bucket * 1.1).all()


def test_encode_age_sentinel_handling(tmp_path):
    """Sentinel (255) writes pixel 0; values shift up by 1."""
    arr = np.array([[255, 0, 1, 14, 254]], dtype=np.uint8)
    out = tmp_path / "x.png"
    encode_age_sidecar_png(arr, out)
    pixels = np.asarray(Image.open(out).convert("L"))
    assert pixels[0, 0] == 0       # sentinel → 0
    assert pixels[0, 1] == 1       # today's obs (age 0) → pixel 1
    assert pixels[0, 2] == 2       # 1 day → pixel 2
    assert pixels[0, 3] == 15      # 14 days → pixel 15
    assert pixels[0, 4] == 255     # max age → pixel 255 (clamped)


def test_encode_all_nan_produces_all_zero_png(tmp_path):
    """A fully-NaN input writes a fully-zero PNG. No crash, no
    accidental clipping."""
    arr = np.full((10, 10), np.nan, dtype=np.float32)
    out = tmp_path / "x.png"
    encode_linear_png(arr, 9.0, 25.0, out)
    pixels = np.asarray(Image.open(out).convert("L"))
    assert (pixels == 0).all()


def test_encode_log10_all_invalid_produces_all_zero_png(tmp_path):
    """Log10 of an all-non-positive array → empty valid mask. Must
    still write a clean zero PNG, matching legacy behavior."""
    arr = np.zeros((10, 10), dtype=np.float32)  # log10(0) = -inf everywhere
    out_new = tmp_path / "new.png"
    out_legacy = tmp_path / "legacy.png"
    encode_log10_png(arr, 0.05, 20.0, out_new)
    _legacy_climatology_encode_log10(arr, 0.05, 20.0, out_legacy)
    assert out_new.read_bytes() == out_legacy.read_bytes()
    pixels = np.asarray(Image.open(out_new).convert("L"))
    assert (pixels == 0).all()


def test_encode_clipping_at_endpoints(tmp_path):
    """Values beyond the encoding range clip to pixel 1 / 255."""
    arr = np.array([[0.0, 9.0, 17.0, 25.0, 50.0]], dtype=np.float32)
    out = tmp_path / "x.png"
    encode_linear_png(arr, 9.0, 25.0, out)
    pixels = np.asarray(Image.open(out).convert("L"))
    # 0 below range → clipped to pixel 1
    assert pixels[0, 0] == 1
    # 9 at lo → pixel 1
    assert pixels[0, 1] == 1
    # 17 at mid → pixel ~128
    assert 120 <= pixels[0, 2] <= 135
    # 25 at hi → pixel 255
    assert pixels[0, 3] == 255
    # 50 above range → clipped to pixel 255
    assert pixels[0, 4] == 255
