"""Regression tests for sst_predict.nearshore input loaders.

The upwelling correction shipped reading `wind_now_speed.png` while
fetch_wind.py publishes `wind_speed_now.png` — the loader returned None
every cycle under the module's fault-tolerant contract, so the manifest
carried `nearshore_correction.layers: []` from birth and nobody noticed
until the 2026-07-04 review. These tests pin the loaders to the real
published artifact names + encoding so a rename on either side fails
unit CI instead of silently disabling the correction.

Run:
    python -m pytest pipeline/tests/test_nearshore_inputs.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sst_predict import nearshore  # noqa: E402

PUBLIC_DATA = ROOT.parent / "public" / "data"

needs_wind = pytest.mark.skipif(
    not (PUBLIC_DATA / "wind_speed_now.png").exists(),
    reason="no committed wind artifact in this checkout",
)
needs_bathy = pytest.mark.skipif(
    not (PUBLIC_DATA / "bathy.png").exists(),
    reason="no committed bathy artifact in this checkout",
)


def test_loader_targets_the_published_artifact_name():
    """fetch_wind.py writes wind_speed_now.png; the loader must read the
    same name. (It shipped reading wind_now_speed.png — always None.)"""
    src = (ROOT / "sst_predict" / "nearshore.py").read_text(encoding="utf-8")
    assert 'PUBLIC_DATA / "wind_speed_now.png"' in src
    fetch_wind = (ROOT / "fetch_wind.py").read_text(encoding="utf-8")
    # fetch_wind.py writes wind_speed_{slot}.png per slot ("now", "p6h", ...).
    assert "wind_speed_" in fetch_wind, (
        "fetch_wind.py no longer writes wind_speed_*.png — update "
        "nearshore._load_wind_uv_kt to the new artifact name."
    )


@needs_wind
def test_wind_loader_returns_plausible_speeds():
    wind = nearshore._load_wind_uv_kt()
    assert wind is not None, (
        "wind_speed_now.png exists but the loader returned None — "
        "filename or decode regression"
    )
    speed_kt, _v = wind
    assert np.isfinite(speed_kt).all()
    assert float(speed_kt.min()) >= 0.0
    assert float(speed_kt.max()) <= 80.0  # sanity: no decode-range blowup


@needs_wind
@needs_bathy
def test_upwelling_term_activates_with_published_inputs():
    depth = nearshore._load_bathy_depth_m()
    assert depth is not None
    layer = nearshore.upwelling_correction(
        bathy_grid_h=depth.shape[0], bathy_grid_w=depth.shape[1],
    )
    assert layer is not None, (
        "upwelling_correction returned None with both inputs present — "
        "the nearshore_correction manifest block would silently empty"
    )
    # Cooling-only term with the module's own clamp.
    assert float(np.nanmax(layer.delta_c)) <= 0.0 + 1e-6
