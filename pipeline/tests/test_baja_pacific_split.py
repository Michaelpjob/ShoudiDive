"""Baja Pacific-vs-Cortez zone split (2026-06-10).

Locks the fix for the prod QA report: mid_baja / south_baja over-
predicted visibility on the Pacific upwelling shelf because the
lat-only zone walk gave the green Pacific side the same clear-water
coefficients as the Sea of Cortez at the same latitude.

These tests run the real classifier on the actual problem coordinates
(decoded from prod) and assert that Pacific-side cells get relabeled to
north_baja while Cortez-side cells keep their clear-water band.

Isolation: classify_zone reads LAT_ZONE_BOUNDS captured at import (CA
by default in the test env). We monkeypatch zones.LAT_ZONE_BOUNDS to the
Baja bands for the duration of each test so the lat walk produces
north/mid/south_baja regardless of SHOULDIDIVE_REGION, and pass the
split explicitly so we don't depend on PACIFIC_SPLIT's region gating.
"""
import numpy as np
import pytest

from pipeline.viz_predict import zones
from pipeline.viz_predict.config import _BAJA_PACIFIC_SPLIT

# Baja lat bands — mirror of pipeline/regions/baja.py lat_zone_bounds.
_BAJA_BANDS = {
    "north_baja": (28.00, 90.00),
    "mid_baja":   (24.50, 28.00),
    "south_baja": (-90.00, 24.50),
}


@pytest.fixture(autouse=True)
def _baja_bands(monkeypatch):
    monkeypatch.setattr(zones, "LAT_ZONE_BOUNDS", _BAJA_BANDS)


def _classify(lng, lat, *, split=_BAJA_PACIFIC_SPLIT, lng_arg=True,
              dist_shore=50.0, dist_isl=999.0, depth=500.0):
    out = zones.classify_zone(
        np.array([lat]), np.array([dist_shore]), np.array([dist_isl]),
        np.array([depth]),
        lng=(np.array([lng]) if lng_arg else None),
        pacific_split=split,
    )
    return str(out[0])


def test_pacific_mid_baja_relabeled_to_north():
    # San Juanico, Magdalena, Punta Abreojos — Pacific mid/south Baja
    # bloom shelf. Must shed the clear-water mid/south band.
    for name, lng, lat in [
        ("San Juanico",     -112.5, 26.0),
        ("Magdalena N",     -112.4, 24.9),
        ("Magdalena mouth", -112.1, 24.5),
        ("Punta Abreojos",  -113.7, 26.7),
    ]:
        z = _classify(lng, lat)
        assert z.startswith("north_baja"), f"{name}: expected north_baja*, got {z}"


def test_cortez_side_keeps_clear_water_band():
    # Cabo Pulmo + La Paz + Loreto — Sea of Cortez clear water. Must
    # KEEP their mid/south_baja clear-water coefficients (not relabeled).
    assert _classify(-109.43, 23.44).startswith("south_baja"), "Cabo Pulmo stays south_baja"
    assert _classify(-110.32, 24.16).startswith("south_baja"), "La Paz stays south_baja"
    assert _classify(-111.30, 26.01).startswith("mid_baja"),   "Loreto stays mid_baja"


def test_north_baja_unchanged_both_sides():
    # north_baja is intentionally NOT in the relabel map — its Cortez
    # side is the cold Midriff, so both sides use the same band.
    assert _classify(-116.10, 30.40).startswith("north_baja"), "San Quintín (Pacific N)"
    assert _classify(-113.00, 29.00).startswith("north_baja"), "Midriff (Cortez N)"


def test_no_split_without_lng_or_config():
    # Omitting lng, or passing pacific_split=None, is a pure no-op.
    assert _classify(-112.5, 26.0, lng_arg=False).startswith("mid_baja"), "no lng → no relabel"
    assert _classify(-112.5, 26.0, split=None).startswith("mid_baja"), "no split → no relabel"
