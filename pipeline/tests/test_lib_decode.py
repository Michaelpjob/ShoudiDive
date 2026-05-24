"""Unit tests for pipeline/lib/decode.py.

Strategy: round-trip lib/encode → lib/decode on hand-crafted arrays
and verify the recovered values are within the per-pixel quantisation
budget (1/254 of the lo..hi span). For the UV and wave decoders
(which don't have lib/encode counterparts yet) we hand-construct the
input PNG byte arrays and verify the decoder reads them as expected.

Run:
    python -m pytest pipeline/tests/test_lib_decode.py -v
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import decode, encode  # noqa: E402


# ---------------------------------------------------------------------
# decode_linear_png round-trip
# ---------------------------------------------------------------------

def test_decode_linear_round_trips_through_encode():
    """encode_linear_png → decode_linear_png should recover values
    within the 8-bit quantisation budget."""
    rng = np.random.default_rng(seed=0)
    arr = rng.uniform(5.0, 25.0, size=(10, 12)).astype(np.float32)
    # Drop a few cells to NaN — the encoder writes them as pixel 0,
    # decoder should bring them back as NaN.
    arr[0, 0] = np.nan
    arr[5, 7] = np.nan

    with tempfile.TemporaryDirectory() as tmp:
        png_path = Path(tmp) / "linear.png"
        encode.encode_linear_png(arr, 0.0, 30.0, png_path)
        out = decode.decode_linear_png(png_path, 0.0, 30.0)

    # NaN cells should round-trip.
    assert np.isnan(out[0, 0])
    assert np.isnan(out[5, 7])
    # Valid cells should round-trip within one quantisation step
    # (1/254 of the lo..hi span = 30/254 ≈ 0.12).
    valid = ~np.isnan(arr)
    diff = np.abs(arr[valid] - out[valid])
    assert diff.max() <= 30.0 / 254.0 + 1e-6


def test_decode_linear_lo_and_hi_endpoints():
    """Pixel 1 should decode to lo; pixel 255 should decode to hi."""
    px = np.array([[1, 255]], dtype=np.uint8)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "endpoints.png"
        Image.fromarray(px, mode="L").save(path)
        out = decode.decode_linear_png(path, lo=10.0, hi=20.0)
    assert out[0, 0] == pytest.approx(10.0, abs=1e-6)
    assert out[0, 1] == pytest.approx(20.0, abs=1e-6)


# ---------------------------------------------------------------------
# decode_log10_png round-trip
# ---------------------------------------------------------------------

def test_decode_log10_round_trips_through_encode():
    rng = np.random.default_rng(seed=1)
    arr = rng.uniform(0.1, 15.0, size=(8, 10)).astype(np.float32)
    arr[2, 3] = np.nan

    with tempfile.TemporaryDirectory() as tmp:
        png_path = Path(tmp) / "log10.png"
        encode.encode_log10_png(arr, 0.05, 20.0, png_path)
        out = decode.decode_log10_png(png_path, 0.05, 20.0)

    assert np.isnan(out[2, 3])
    # In log10 space the quantisation is (log10(20) - log10(0.05)) / 254
    # ≈ 0.0103. In linear space the bound varies — check ratio instead.
    valid = ~np.isnan(arr)
    ratio = np.abs(np.log10(arr[valid]) - np.log10(out[valid]))
    assert ratio.max() <= (np.log10(20.0) - np.log10(0.05)) / 254 + 1e-4


# ---------------------------------------------------------------------
# decode_uv_png — hand-crafted RGBA
# ---------------------------------------------------------------------

def test_decode_uv_recovers_lo_hi_endpoints():
    """RGBA where R=0,G=0,A=255 should decode to (lo, lo).
    R=255,G=255,A=255 should decode to (hi, hi). A=0 should give NaN."""
    rgba = np.array(
        [
            [[0, 0, 0, 255], [255, 255, 0, 255]],
            [[127, 64, 0, 0],   [0, 0, 0, 255]],
        ],
        dtype=np.uint8,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "uv.png"
        Image.fromarray(rgba, mode="RGBA").save(path)
        u, v = decode.decode_uv_png(path, lo=-10.0, hi=10.0)
    assert u[0, 0] == pytest.approx(-10.0, abs=1e-5)
    assert v[0, 0] == pytest.approx(-10.0, abs=1e-5)
    assert u[0, 1] == pytest.approx(10.0, abs=1e-5)
    assert v[0, 1] == pytest.approx(10.0, abs=1e-5)
    assert np.isnan(u[1, 0])  # alpha=0 → NaN
    assert np.isnan(v[1, 0])


# ---------------------------------------------------------------------
# decode_wave_png — hand-crafted RGBA
# ---------------------------------------------------------------------

def test_decode_wave_unpacks_height_period_direction():
    # R = height byte (0..12 m), G = period (0..25 s), B = direction (0..360°)
    rgba = np.array(
        [
            [[0, 0, 0, 255], [255, 255, 255, 255]],
        ],
        dtype=np.uint8,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "wave.png"
        Image.fromarray(rgba, mode="RGBA").save(path)
        h, p, d = decode.decode_wave_png(path)
    assert h[0, 0] == pytest.approx(0.0, abs=1e-5)
    assert p[0, 0] == pytest.approx(0.0, abs=1e-5)
    assert d[0, 0] == pytest.approx(0.0, abs=1e-5)
    assert h[0, 1] == pytest.approx(12.0, abs=1e-5)
    assert p[0, 1] == pytest.approx(25.0, abs=1e-5)
    assert d[0, 1] == pytest.approx(360.0, abs=1e-5)


# ---------------------------------------------------------------------
# decode_age_png — round-trip through encode_age_sidecar
# ---------------------------------------------------------------------

def test_decode_age_round_trips_through_encode():
    """encode_age_sidecar_png writes age days; decode_age_png reads them back.
    Cells with input 255 (encode's missing sentinel) decode to the 999.0
    sentinel through decode_age_png."""
    # encode_age_sidecar_png treats input value 255 as missing → pixel 0.
    # Other inputs write min(input+1, 255). decode_age_png reads
    # pixel - 1, so the round-trip should recover the original input
    # for values 0..254.
    ages = np.array(
        [[0, 1, 5, 10], [255, 2, 3, 7]],  # 255 = missing
        dtype=np.int16,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "age.png"
        encode.encode_age_sidecar_png(ages, path)
        out = decode.decode_age_png(path)

    # Sentinel for missing cells.
    assert out[1, 0] == pytest.approx(999.0)
    # Non-missing cells should decode back exactly (ages are integer days).
    assert out[0, 0] == pytest.approx(0.0)
    assert out[0, 1] == pytest.approx(1.0)
    assert out[0, 2] == pytest.approx(5.0)
    assert out[0, 3] == pytest.approx(10.0)
    assert out[1, 1] == pytest.approx(2.0)
    assert out[1, 2] == pytest.approx(3.0)
    assert out[1, 3] == pytest.approx(7.0)
