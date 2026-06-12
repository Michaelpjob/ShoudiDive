"""Unit tests for pipeline/viz_column/model.py (PRD water-column C1).

Pure-function tests — no network, no files. Mirrors the conventions of
the other pipeline unit tests (self-contained, run via
``bash pipeline/scripts/validate.sh --unit``).

The final test encodes the PRD acceptance anchor directly: at Point
Loma in June, typical conditions must reproduce clear ~20-30 ft over a
cliff ~22-28 ft with below-cliff vis ~5-12 ft.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from viz_column import config as C  # noqa: E402
from viz_column import model  # noqa: E402


# ---- upwelling ------------------------------------------------------------

def test_upwelling_favorable_nw_wind():
    """Wind FROM the northwest (blowing toward SE, the equatorward
    alongshore direction) must register as upwelling-favorable."""
    # Blowing toward 140 deg true at 8 m/s.
    theta = math.radians(C.ALONGSHORE_EQUATORWARD_DEG)
    u, v = 8.0 * math.sin(theta), 8.0 * math.cos(theta)
    idx = model.upwelling_index(np.array([u]), np.array([v]))
    assert 0.0 < idx[0] <= 1.0


def test_upwelling_downwelling_clamps_to_zero():
    """Poleward (toward NW) wind = downwelling: index clamps to 0."""
    theta = math.radians(C.ALONGSHORE_EQUATORWARD_DEG + 180.0)
    u, v = 8.0 * math.sin(theta), 8.0 * math.cos(theta)
    idx = model.upwelling_index(np.array([u]), np.array([v]))
    assert idx[0] == 0.0


def test_upwelling_zero_wind_is_zero():
    idx = model.upwelling_index(np.zeros(3), np.zeros(3))
    assert np.all(idx == 0.0)


def test_upwelling_monotonic_in_wind_speed():
    theta = math.radians(C.ALONGSHORE_EQUATORWARD_DEG)
    speeds = np.array([2.0, 5.0, 9.0])
    u, v = speeds * math.sin(theta), speeds * math.cos(theta)
    idx = model.upwelling_index(u, v)
    assert idx[0] < idx[1] < idx[2] or (idx[2] == 1.0 and idx[0] < idx[1])


# ---- waves / resuspension ---------------------------------------------------

def test_wavenumber_matches_deep_and_shallow_limits():
    """Hunt's approximation must land near the analytic limits:
    deep water k = omega^2/g; shallow water k = omega/sqrt(g d)."""
    T = 10.0
    omega = 2 * math.pi / T
    k_deep = model._wavenumber(T, np.array([4000.0]))[0]
    assert k_deep == pytest.approx(omega**2 / C.GRAVITY, rel=0.02)
    k_shallow = model._wavenumber(T, np.array([2.0]))[0]
    assert k_shallow == pytest.approx(omega / math.sqrt(C.GRAVITY * 2.0), rel=0.05)


def test_orbital_velocity_decays_with_depth():
    hs = np.array([2.0, 2.0, 2.0])
    depths = np.array([5.0, 20.0, 200.0])
    u_b = model.bottom_orbital_velocity(hs, 14.0, depths)
    assert u_b[0] > u_b[1] > u_b[2]
    assert u_b[2] < 0.05  # long swell barely felt at 200 m


def test_resuspension_ramp_bounds():
    """Below the critical velocity -> 0; far above -> saturates at 1."""
    calm = model.resuspension_index(np.array([0.2]), 14.0, np.array([30.0]))
    assert calm[0] == 0.0
    violent = model.resuspension_index(np.array([6.0]), 16.0, np.array([5.0]))
    assert violent[0] == 1.0


# ---- cliff depth + swing ------------------------------------------------------

def test_cliff_seasonal_cycle():
    """Summer cliff shallower than winter (stratified vs mixed)."""
    summer = model.cliff_depth_ft(7, 33.0, 0.0)
    winter = model.cliff_depth_ft(1, 33.0, 0.0)
    assert float(summer) < float(winter)


def test_cliff_upwelling_shoals():
    calm = model.cliff_depth_ft(6, 33.0, 0.0)
    upwelled = model.cliff_depth_ft(6, 33.0, 1.0)
    assert float(upwelled) == pytest.approx(
        float(calm) * (1 - C.UPWELLING_CLIFF_SHOALING_FRAC), rel=1e-6)


def test_cliff_norcal_deeper_than_socal():
    socal = model.cliff_depth_ft(6, 33.0, 0.0)
    norcal = model.cliff_depth_ft(6, 38.0, 0.0)
    assert float(norcal) > float(socal)


def test_cliff_respects_clamps():
    c = model.cliff_depth_ft(8, np.array([32.0, 41.0]), np.array([1.0, 0.0]))
    assert np.all(c >= C.CLIFF_MIN_FT) and np.all(c <= C.CLIFF_MAX_FT)


def test_swing_larger_in_summer():
    assert model.swing_amplitude_ft(7) > model.swing_amplitude_ft(1)


def test_cliff_series_bounds_and_phase():
    """Series stays within +/- amplitude/2 of the mean and, under the
    deepest-at-high-water assumption, is deepest at h=0 (high water)."""
    series = model.cliff_series_ft(25.0, 6, range(0, 13))
    amp = model.swing_amplitude_ft(6) / 2.0
    assert all(25.0 - amp - 0.05 <= s <= 25.0 + amp + 0.05 for s in series)
    assert series[0] == max(series)  # deepest at high water
    # Half an M2 period later it should be at/near the shallow extreme.
    assert series[6] == min(series)


# ---- below-cliff vis ------------------------------------------------------------

def test_below_never_exceeds_surface_and_floors():
    surface = np.array([25.0, 4.0, 60.0])
    below = model.below_cliff_vis_ft(surface, np.zeros(3), np.zeros(3))
    assert np.all(below <= surface)
    assert np.all(below >= np.minimum(C.BELOW_VIS_FLOOR_FT, surface))


def test_below_worsens_with_upwelling_and_resuspension():
    surface = np.full(3, 30.0)
    clean = model.below_cliff_vis_ft(surface, np.zeros(3), np.zeros(3))[0]
    upwelled = model.below_cliff_vis_ft(surface, np.ones(3), np.zeros(3))[0]
    stirred = model.below_cliff_vis_ft(surface, np.ones(3), np.ones(3))[0]
    assert clean > upwelled > stirred


# ---- column assembly --------------------------------------------------------------

def test_column_shallow_shelf_has_no_cliff():
    """Bottom shallower than the cliff: no murk layer; the surface
    number applies all the way down."""
    out = model.column(
        surface_vis_ft=20.0, bottom_ft=12.0, month=6, lat_deg=32.8,
        u10=0.0, v10=0.0, hs_m=0.5)
    assert bool(out["no_cliff"])
    assert float(out["below_ft"]) == 20.0


def test_column_vectorized_shapes():
    shape = (4, 5)
    out = model.column(
        surface_vis_ft=np.full(shape, 25.0),
        bottom_ft=np.full(shape, 80.0),
        month=6,
        lat_deg=np.full(shape, 33.0),
        u10=np.full(shape, -3.0), v10=np.full(shape, -3.0),
        hs_m=np.full(shape, 1.0))
    for key in ("cliff_ft", "below_ft", "swing_ft", "no_cliff"):
        assert out[key].shape == shape, key


def test_point_loma_acceptance_anchor():
    """PRD acceptance: at Point Loma in June, typical conditions
    (moderate NW wind, ~1 m long-period swell, kelp-line bottom
    ~60 ft, surface vis ~25 ft) must yield cliff ~22-28 ft and
    below-cliff vis ~5-12 ft."""
    theta = math.radians(C.ALONGSHORE_EQUATORWARD_DEG)
    u, v = 5.0 * math.sin(theta), 5.0 * math.cos(theta)  # 5 m/s from NW
    out = model.column(
        surface_vis_ft=25.0, bottom_ft=60.0, month=6, lat_deg=32.67,
        u10=u, v10=v, hs_m=1.0, period_s=14.0)
    assert not bool(out["no_cliff"])
    assert 22.0 <= float(out["cliff_ft"]) <= 28.0
    assert 5.0 <= float(out["below_ft"]) <= 12.0


# ---- encoder <-> LayerSpec contract -----------------------------------------

def test_encode_ranges_match_layer_spec():
    """fetch_viz_column's encoder ranges must equal the LayerSpec
    contract — drift here means the pipeline writes one range and the
    frontend decodes another (same bug class
    test_fetch_layer_spec_consistency guards for fetch.py layers)."""
    import fetch_viz_column as fvc
    from lib.layer_spec import LAYER_SPECS

    spec = LAYER_SPECS["viz_column"]
    assert spec.range is None  # uses range_ft + cliff_range_ft
    assert "range_ft" in spec.extra_required_keys
    assert "cliff_range_ft" in spec.extra_required_keys
    assert spec.scale == "linear" and spec.unit == "ft"
    # The values the fetcher encodes with:
    assert fvc.VIZ_COLUMN_RANGE_FT == (0.0, 80.0)
    assert fvc.VIZ_COLUMN_CLIFF_RANGE_FT == (0.0, 100.0)
