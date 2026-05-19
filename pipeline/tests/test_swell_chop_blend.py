"""Unit tests for the WW3+SMB blend in fetch_swell_5day.fill_with_wind_chop.

Catches the regression where a hard cliff appears in the rendered swell
PNG at the WW3 (gfswave) model boundary. User report (2026-05-18 Baja
screenshot): "still have a hard cut over in the wave data. there should
be no line where it goes from 9ft to 1ft seas."

Root cause we test against: inside `fill_with_wind_chop`, the exponential
swell-decay weight is multiplied by h_swell, but h_swell is set to 0.0
at cells where WW3 is invalid. So at every cell just outside the WW3
boundary, `0.0 * weight = 0.0` — the decay never expresses itself. The
fix: backfill h_swell from the nearest VALID WW3 cell (via the indices
that distance_transform_edt already computes), then multiply by weight.

These tests run in the `pipeline-tests` CI job. They don't need any
network access or cached GRIB files — they construct synthetic grids
and call the blend function directly.
"""
from __future__ import annotations

import numpy as np
import pytest

from pipeline.fetch_swell_5day import _blend_swell_chop


def _make_split_grid(h=20, w=80, swell_hs_m=3.0):
    """Synthetic grid: WEST half has WW3 swell, EAST half NaN.

    Wind UV is a uniform ~5 m/s easterly everywhere — enough to produce
    a small wind-sea (~0.4 m via SMB at 50 km fetch) but well below the
    swell magnitude offshore.
    """
    h_grid = np.full((h, w), np.nan, dtype=np.float32)
    h_grid[:, : w // 2] = swell_hs_m
    p_grid = np.full((h, w), np.nan, dtype=np.float32)
    p_grid[:, : w // 2] = 12.0
    d_grid = np.full((h, w), np.nan, dtype=np.float32)
    d_grid[:, : w // 2] = 285.0
    u = np.full((h, w), 5.0, dtype=np.float32)
    v = np.full((h, w), 0.0, dtype=np.float32)
    return h_grid, p_grid, d_grid, u, v


def test_blend_produces_smooth_decay_across_ww3_boundary():
    """The single largest cell-to-cell jump in the output Hs must be
    much smaller than the raw swell drop at the WW3 boundary."""
    h_grid, p_grid, d_grid, u, v = _make_split_grid(swell_hs_m=3.0)
    h_out, _, _ = _blend_swell_chop(h_grid, p_grid, d_grid, u, v)

    # Walk one row across the boundary and check max delta.
    row = h_out[h_out.shape[0] // 2]
    valid = np.isfinite(row)
    assert valid.all(), "every cell should be finite (swell-decayed or wind-chop)"
    deltas = np.abs(np.diff(row))
    max_delta = float(deltas.max())

    # Raw swell→chop without decay would be ~3.0 m - 0.4 m ≈ 2.6 m.
    # With 20-cell exponential decay, the first step beyond the boundary
    # should drop by ~5% of 3.0 m ≈ 0.15 m at most. Allow 0.5 m headroom
    # for the period/direction blend and floating-point arithmetic.
    assert max_delta < 0.5, (
        f"max single-cell jump {max_delta:.3f} m exceeds 0.5 m; "
        f"the WW3 boundary cliff regressed (row values: {row.tolist()})"
    )


def test_blend_decay_actually_reaches_far_cells():
    """At cells far beyond the WW3 boundary the decayed swell drops
    below the wind-sea, so Hs ≈ wind-sea Hs alone. This guards against
    the opposite regression (decay weight stuck at 1 forever)."""
    h_grid, p_grid, d_grid, u, v = _make_split_grid(swell_hs_m=3.0)
    h_out, _, _ = _blend_swell_chop(h_grid, p_grid, d_grid, u, v)

    # SMB wind-sea Hs at U=5 m/s, F=50 km ≈ 0.42 m. Far-field cell on
    # the right edge should be dominated by wind-sea.
    far_cell = float(h_out[h_out.shape[0] // 2, -1])
    assert far_cell < 1.0, (
        f"far-field cell Hs={far_cell:.3f} m is too high — decay isn't "
        "attenuating swell over ~20 cells"
    )


def test_blend_preserves_ww3_value_at_origin():
    """Inside the WW3-valid region the output Hs must combine the raw
    swell with the wind-sea via root-sum-square (not be cut by decay)."""
    h_grid, p_grid, d_grid, u, v = _make_split_grid(swell_hs_m=3.0)
    h_out, _, _ = _blend_swell_chop(h_grid, p_grid, d_grid, u, v)

    interior = float(h_out[h_out.shape[0] // 2, 0])
    # sqrt(3.0^2 + 0.4^2) ≈ 3.026 m, allow generous tolerance.
    assert 2.9 < interior < 3.2, (
        f"interior WW3 cell Hs={interior:.3f} m — should be ~3.02 m "
        "(swell dominates, weight=1)"
    )


def test_blend_handles_no_ww3_data_anywhere():
    """If gfswave returned all-NaN (model failure), the blend should
    fall back to pure wind-chop without crashing."""
    h, w = 10, 20
    h_grid = np.full((h, w), np.nan, dtype=np.float32)
    p_grid = np.full((h, w), np.nan, dtype=np.float32)
    d_grid = np.full((h, w), np.nan, dtype=np.float32)
    u = np.full((h, w), 6.0, dtype=np.float32)
    v = np.full((h, w), 0.0, dtype=np.float32)

    h_out, p_out, d_out = _blend_swell_chop(h_grid, p_grid, d_grid, u, v)
    assert np.isfinite(h_out).all(), "wind chop should fill every cell"
    assert (h_out > 0).all() and (h_out < 1.0).all(), (
        f"every cell should be chop-only (~0.4 m); got range "
        f"{h_out.min():.3f}..{h_out.max():.3f}"
    )


def test_blend_blocks_swell_across_land_barrier():
    """A peninsula running north-south down the grid middle should
    prevent Pacific swell on the west from bleeding through to wind-
    chop-only Cortez on the east. With is_land set, the east side
    should be wind-sea magnitude only."""
    h, w = 30, 40
    h_grid = np.full((h, w), np.nan, dtype=np.float32)
    # WW3 valid on west third only (cols 0..12).
    h_grid[:, : 13] = 4.0
    p_grid = np.full((h, w), np.nan, dtype=np.float32)
    p_grid[:, : 13] = 12.0
    d_grid = np.full((h, w), np.nan, dtype=np.float32)
    d_grid[:, : 13] = 270.0
    u = np.full((h, w), 5.0, dtype=np.float32)
    v = np.full((h, w), 0.0, dtype=np.float32)
    # Land barrier: cols 14..23 (10-cell-wide peninsula) running full
    # height. Cortez side: cols 24..39.
    is_land = np.zeros((h, w), dtype=bool)
    is_land[:, 14:24] = True

    # Probe at col 26 (just past land barrier) — distance from west
    # WW3 boundary = 13 cells, weight without blocking = exp(-13/20)
    # = 0.52, so 4.0 * 0.52 = 2.08 m swell + 0.42 m chop ≈ 2.12 m.
    # With blocking (path crosses 10 land cells), weight = 0 → chop only.
    PROBE_COL = 26
    h_out, _, _ = _blend_swell_chop(h_grid, p_grid, d_grid, u, v, is_land=is_land)
    cortez_blocked = float(h_out[h // 2, PROBE_COL])
    assert cortez_blocked < 1.0, (
        f"Cortez cell Hs={cortez_blocked:.3f} m — should be wind-chop only "
        "(~0.4 m); Pacific swell bled across the land barrier"
    )

    # Pacific side should still see the source.
    pacific = float(h_out[h // 2, 2])
    assert 3.9 < pacific < 4.2

    # Same setup but WITHOUT is_land: Cortez side gets the over-bleed
    # (this is the v3-only regression we're guarding against).
    h_out_no_block, _, _ = _blend_swell_chop(h_grid, p_grid, d_grid, u, v)
    cortez_unblocked = float(h_out_no_block[h // 2, PROBE_COL])
    assert cortez_unblocked > 1.5, (
        f"sanity check: without land mask, cortez Hs={cortez_unblocked:.3f} m "
        "should be > 1.5 m (proving the v3 over-bleed)"
    )


def test_blend_handles_no_wind_chop_anywhere():
    """If the wind UV is all-NaN, the blend should preserve WW3 valid
    cells and return NaN beyond them (no false data)."""
    h, w = 10, 20
    h_grid = np.full((h, w), np.nan, dtype=np.float32)
    h_grid[:, : w // 2] = 2.0
    p_grid = np.full((h, w), np.nan, dtype=np.float32)
    p_grid[:, : w // 2] = 10.0
    d_grid = np.full((h, w), np.nan, dtype=np.float32)
    d_grid[:, : w // 2] = 270.0
    u = np.full((h, w), np.nan, dtype=np.float32)
    v = np.full((h, w), np.nan, dtype=np.float32)

    h_out, _, _ = _blend_swell_chop(h_grid, p_grid, d_grid, u, v)
    # West half valid (swell only, no chop), east half NaN.
    assert np.isfinite(h_out[:, : w // 2]).all()
    assert np.isnan(h_out[:, w // 2 :]).any(), (
        "without wind chop, cells beyond WW3 + decay should remain NaN "
        "in the far field"
    )
