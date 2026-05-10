"""PR-NC-1 — viz_predict zone classification tests.

Covers:
  * The new `norcal` lat band (36.00°N – 90°N) classifies correctly
    for known dive sites.
  * The narrowed `central` band (34.45°N – 36.00°N) still classifies
    correctly for sites south of Pt. Sur.
  * SoCal cells are unchanged — `transition` and `bight` stay where
    they were.
  * Boundary inclusivity at the lower edge of each band.
  * The new generic ``classify_zone`` walk over ``LAT_ZONE_BOUNDS``
    works without editing zones.py for future band additions.
  * Per-zone config dicts (PERSISTENCE_TAU_DAYS, DRIVER_COEFFS,
    SIGMA_LOG_CHL, SECCHI_COEFFS, TURBIDITY_CORRECTIONS) all carry
    norcal_* keys so model.py / visibility.py find them.

Run:
    python -m pytest pipeline/tests/test_zones.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# Same sys.path pattern the other tests use — pipeline/ on sys.path
# so `from viz_predict.zones import ...` resolves.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from viz_predict.zones import classify_zone  # noqa: E402
from viz_predict.config import (              # noqa: E402
    LAT_ZONE_BOUNDS,
    PERSISTENCE_TAU_DAYS,
    DRIVER_COEFFS,
    SIGMA_LOG_CHL,
    SECCHI_COEFFS,
    TURBIDITY_CORRECTIONS,
)


def _zone_at(lat: float, lng: float = -122.0, *,
             dist_to_shore_km: float = 0.5,
             dist_to_island_km: float = 200.0,
             depth_m: float = 20.0) -> str:
    """Helper: classify a single (lat, lng) cell with sane defaults
    for a nearshore California cell. Override the kwargs for islands
    or offshore cases."""
    lat_arr = np.array([[lat]])
    out = classify_zone(
        lat_arr,
        np.array([[dist_to_shore_km]]),
        np.array([[dist_to_island_km]]),
        np.array([[depth_m]]),
    )
    return str(out[0, 0])


# ---------------------------------------------------------------------------
# Boundary tests for the new norcal band
# ---------------------------------------------------------------------------


def test_norcal_lower_bound_is_inclusive_at_36():
    """A cell at exactly 36.00°N is `norcal`, not `central`."""
    assert _zone_at(36.00).startswith("norcal_"), (
        "36.00°N should be the inclusive lower edge of norcal"
    )


def test_just_below_36_is_central():
    """36.00 - epsilon falls into central, not norcal."""
    z = _zone_at(35.999)
    assert z.startswith("central_"), z


def test_central_lower_bound_unchanged_at_34_45():
    """34.45°N still anchors `central`'s lower edge."""
    assert _zone_at(34.45).startswith("central_")


def test_pt_conception_at_34_45_is_central():
    """Pt. Conception sits exactly on the central/transition boundary —
    cell-row at 34.45 should classify as central."""
    assert _zone_at(34.45, lng=-120.42).startswith("central_")


def test_just_below_central_is_transition():
    """34.45 - epsilon falls into transition."""
    z = _zone_at(34.449)
    assert z.startswith("transition_"), z


# ---------------------------------------------------------------------------
# Real dive-spot regression cases (from PR-NC-1 spec § "Manual validation")
# ---------------------------------------------------------------------------


def test_pt_sur_classifies_as_norcal_nearshore():
    """36.31°N at the coast — nearshore norcal."""
    z = _zone_at(36.31, lng=-121.90)
    assert z == "norcal_nearshore", z


def test_monterey_bay_classifies_as_norcal_nearshore():
    """36.80°N, ~80m depth, near shore — norcal nearshore."""
    z = _zone_at(36.80, lng=-121.95, depth_m=80.0)
    # depth_m > NEARSHORE_MAX_DEPTH_M (30) but dist_to_shore_km is
    # close, so still nearshore.
    assert z == "norcal_nearshore", z


def test_pioneer_seamount_classifies_as_norcal_offshore():
    """37.40°N, far from any island, deep — norcal offshore."""
    z = _zone_at(
        37.40, lng=-123.40,
        dist_to_shore_km=80.0,
        dist_to_island_km=200.0,
        depth_m=2000.0,
    )
    assert z == "norcal_offshore", z


def test_cambria_still_classifies_as_central_nearshore():
    """35.55°N stays in central even with the new norcal split.
    (Cambria is south of the 36.00 boundary by 0.45°.)"""
    z = _zone_at(35.55, lng=-121.10)
    assert z == "central_nearshore", z


def test_socal_la_jolla_unchanged():
    """32.85°N — bight, well south of any boundary that moved."""
    z = _zone_at(32.85, lng=-117.27)
    assert z == "bight_nearshore", z


def test_socal_catalina_unchanged():
    """33.39°N — bight, near Catalina island."""
    z = _zone_at(33.39, lng=-118.42, dist_to_island_km=2.0)
    assert z == "bight_islands", z


def test_socal_coronados_unchanged():
    """32.42°N — bight, Coronados zone."""
    z = _zone_at(32.42, lng=-117.27, dist_to_island_km=1.0)
    assert z == "bight_islands", z


# ---------------------------------------------------------------------------
# Vectorized-input shape preservation
# ---------------------------------------------------------------------------


def test_classify_zone_preserves_2d_array_shape():
    """The classifier should broadcast across HxW grids, not flatten."""
    lat = np.array([[36.5, 35.0], [34.0, 32.5]])
    dts = np.full(lat.shape, 0.5)
    dti = np.full(lat.shape, 200.0)
    dpt = np.full(lat.shape, 20.0)
    out = classify_zone(lat, dts, dti, dpt)
    assert out.shape == lat.shape
    # Top-left = norcal, top-right = central, bottom-left = transition,
    # bottom-right = bight.
    assert str(out[0, 0]).startswith("norcal_")
    assert str(out[0, 1]).startswith("central_")
    assert str(out[1, 0]).startswith("transition_")
    assert str(out[1, 1]).startswith("bight_")


# ---------------------------------------------------------------------------
# Per-zone config dicts must carry norcal_* keys for ALL the
# downstream lookups in model.py + visibility.py.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dist", ["nearshore", "islands", "offshore"])
def test_persistence_tau_has_norcal_keys(dist):
    assert f"norcal_{dist}" in PERSISTENCE_TAU_DAYS


@pytest.mark.parametrize("dist", ["nearshore", "islands", "offshore"])
def test_driver_coeffs_has_norcal_keys(dist):
    assert f"norcal_{dist}" in DRIVER_COEFFS


@pytest.mark.parametrize("dist", ["nearshore", "islands", "offshore"])
def test_sigma_log_chl_has_norcal_keys(dist):
    assert f"norcal_{dist}" in SIGMA_LOG_CHL


@pytest.mark.parametrize("dist", ["nearshore", "islands", "offshore"])
def test_secchi_coeffs_has_norcal_keys(dist):
    assert f"norcal_{dist}" in SECCHI_COEFFS


@pytest.mark.parametrize("dist", ["nearshore", "islands", "offshore"])
def test_turbidity_corrections_has_norcal_keys(dist):
    assert f"norcal_{dist}" in TURBIDITY_CORRECTIONS


# ---------------------------------------------------------------------------
# LAT_ZONE_BOUNDS structural sanity
# ---------------------------------------------------------------------------


def test_lat_zone_bounds_have_no_gaps_or_overlaps():
    """Each band's upper bound must equal the next band's lower bound
    (sorted by lower bound). Otherwise cells between bands fall into
    an unintended catch-all."""
    bands = sorted(LAT_ZONE_BOUNDS.items(), key=lambda kv: kv[1][0])
    for i in range(len(bands) - 1):
        upper = bands[i][1][1]
        next_lower = bands[i + 1][1][0]
        assert upper == next_lower, (
            f"gap/overlap between {bands[i][0]} (upper={upper}) and "
            f"{bands[i + 1][0]} (lower={next_lower})"
        )


def test_lat_zone_bounds_includes_norcal():
    """PR-NC-1 added the norcal band — make sure a future config edit
    doesn't accidentally remove it without intent."""
    assert "norcal" in LAT_ZONE_BOUNDS
    lo, hi = LAT_ZONE_BOUNDS["norcal"]
    assert lo == 36.00
    assert hi == 90.0
