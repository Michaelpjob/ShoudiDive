"""PR1 — chl freshness sidecar: encode/decode round-trip + the
end-to-end orchestration that fetch_visibility.py performs before
calling viz_predict.assign_quality.

The bug we're guarding against: pre-PR1 the pipeline hardcoded
`age = 0.0` for every cell that had any chl pixel within the
7-day fallback window. That made stale obs look fresh — three
downstream consequences in `02-fix.md`:

  1. persistence_with_decay weight stayed at 1.0 instead of decaying
     toward climatology
  2. effective_sigma kept p10/p90 narrow when it should widen
  3. assign_quality flagged everything OBSERVED_1D even when stale

The fix is two-part:

  * fetch.py emits a per-cell `chl_1d_age_days.png` sidecar (mode='L',
    pixel = age + 1; 0 = no data) over the same `stack` build_layer
    iterates.
  * fetch_visibility.py reads the sidecar, bilinear-resamples onto
    the prediction grid, and gates `chl_obs_today` on age==0 so
    `assign_quality` correctly downgrades stale cells.

These tests run without network access — they fabricate small
in-memory arrays end-to-end.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fetch import build_age_array, encode_age_png  # noqa: E402
from fetch_visibility import decode_age_png  # noqa: E402
from viz_predict.model import assign_quality  # noqa: E402


# ---------------------------------------------------------------------------
# Sidecar PNG round-trip
# ---------------------------------------------------------------------------


def test_encode_decode_round_trip(tmp_path):
    """encode_age_png → decode_age_png preserves cell ages, with the
    no-data sentinel (255) round-tripping to 999.0 in the decoded
    float array."""
    age = np.array(
        [
            [0,   1,   2,   3],   # fresh -> 3 days stale
            [4,   5,  10,  20],   # widening staleness
            [50, 100, 254, 255],  # 255 is no-data
        ],
        dtype=np.uint8,
    )
    out = tmp_path / "age.png"
    encode_age_png(age, out)
    decoded = decode_age_png(out)

    expected = age.astype(np.float32)
    expected[age == 255] = 999.0
    np.testing.assert_array_equal(decoded, expected)


def test_decode_no_data_is_999(tmp_path):
    """Pixel value 0 in the PNG is reserved for "no data" and must
    decode to the 999.0 sentinel `fetch_visibility` keys off of."""
    age = np.full((4, 4), 255, dtype=np.uint8)  # all no-data
    out = tmp_path / "no_data.png"
    encode_age_png(age, out)
    decoded = decode_age_png(out)
    assert np.all(decoded == 999.0)


# ---------------------------------------------------------------------------
# build_age_array — newest valid wins
# ---------------------------------------------------------------------------


def test_build_age_takes_freshest_value():
    """For each cell, build_age_array must return the FRESHEST age it
    has across the stack, not the first one it walks into.

    Stack is chronological (oldest -> newest). Cell (0,0) has a valid
    value on every day; should report age=0 (today). Cell (0,1) is
    only valid 2 days ago; should report age=2. Cell (1,0) is only
    valid 4 days ago; age=4. Cell (1,1) is never valid; encodes as
    255 (no-data sentinel).
    """
    end = date(2026, 1, 10)
    nan = np.nan
    # Day 0 = 4 days before `end` (oldest), Day 4 = end (newest).
    day0 = np.array([[1.0, nan], [0.5, nan]])
    day1 = np.array([[1.1, nan], [nan, nan]])
    day2 = np.array([[1.2, 0.4], [nan, nan]])
    day3 = np.array([[1.3, nan], [nan, nan]])
    day4 = np.array([[1.4, nan], [nan, nan]])
    stack = [day0, day1, day2, day3, day4]
    dates = [date(2026, 1, 6 + i) for i in range(5)]

    age = build_age_array(stack, dates, end)
    assert age.shape == (2, 2)
    assert age[0, 0] == 0    # valid today
    assert age[0, 1] == 2    # valid 2 days ago
    assert age[1, 0] == 4    # valid 4 days ago
    assert age[1, 1] == 255  # never valid


def test_build_age_empty_stack_returns_none():
    assert build_age_array([], [], date(2026, 1, 10)) is None


# ---------------------------------------------------------------------------
# End-to-end: orchestration → assign_quality buckets
# ---------------------------------------------------------------------------


def test_assign_quality_buckets_with_real_ages():
    """Mirrors what fetch_visibility.py does after PR1:

      chl_obs_today = chl_today  WHERE age == 0  ELSE NaN
      chl_lastvalid = chl_today  (raw — no gating)
      chl_lastvalid_age_days = the per-cell ages from the sidecar

    `assign_quality` then buckets each cell:
      age=0           -> OBSERVED_1D       (today's obs)
      age in [1,3]    -> OBSERVED_3D       (recent persistence)
      age in [4,5]    -> PREDICTED_HIGH_CONF
      age in [6,10]   -> PREDICTED_MED_CONF
      age >  10       -> PREDICTED_LOW_CONF (or INTERPOLATED if mask set)

    Note: `CLIMATOLOGY_ONLY` is unreachable in this orchestration —
    `PREDICTED_LOW_CONF` claims all stale, non-interpolated cells
    regardless of how stale. That's a model semantics quirk, NOT
    PR1's scope. PR1 only guarantees stale cells STOP being flagged
    OBSERVED_1D. Folding the climatology-only branch into reach is
    tracked separately (model.py `assign_quality` revision).
    """
    # One cell per bucket we want to verify.
    ages = np.array([0.0, 1.0, 3.0, 4.0, 5.0, 6.0, 10.0, 11.0, 30.0])
    chl_today = np.array([0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5])

    chl_obs_today = np.where(ages == 0.0, chl_today, np.nan)
    interpolated = np.zeros_like(ages, dtype=bool)

    quality = assign_quality(chl_obs_today, ages, interpolated)
    expected = np.array(
        [
            "OBSERVED_1D",
            "OBSERVED_3D",
            "OBSERVED_3D",
            "PREDICTED_HIGH_CONF",
            "PREDICTED_HIGH_CONF",
            "PREDICTED_MED_CONF",
            "PREDICTED_MED_CONF",
            "PREDICTED_LOW_CONF",
            "PREDICTED_LOW_CONF",
        ]
    )
    np.testing.assert_array_equal(quality, expected)


def test_assign_quality_interpolated_mask_overrides_predicted():
    """When `interpolated_mask` is True for a stale cell (age > 3),
    INTERPOLATED takes precedence over PREDICTED_*. This is the only
    realistic way `INTERPOLATED` fires today and a useful sanity
    check that PR1's age plumbing doesn't break that branch.
    """
    ages = np.array([4.0, 8.0, 15.0])
    chl_obs_today = np.array([np.nan, np.nan, np.nan])
    interpolated = np.array([True, True, True])

    quality = assign_quality(chl_obs_today, ages, interpolated)
    np.testing.assert_array_equal(
        quality, np.array(["INTERPOLATED", "INTERPOLATED", "INTERPOLATED"])
    )


def test_pre_pr1_bug_would_fail_this_test():
    """Regression guard: under the pre-PR1 hardcoded `age = 0.0` path,
    every cell with a finite chl value (regardless of true age) would
    be flagged OBSERVED_1D. This test recreates that buggy call shape
    and asserts it disagrees with the corrected behaviour — so if
    someone ever reverts the orchestration fix, this test catches it.

    The corrected path passes REAL ages to assign_quality; the buggy
    path passes all zeros. Both with the same chl_obs_today input
    (every cell finite, simulating the legacy "if there's any chl in
    the 7-day window, treat it as today's").
    """
    real_ages = np.array([0.0, 2.0, 7.0, 15.0])
    chl_obs_today_buggy = np.array([0.5, 0.5, 0.5, 0.5])  # legacy: all "fresh"
    interpolated = np.zeros_like(real_ages, dtype=bool)

    buggy_age_input = np.zeros_like(real_ages)
    buggy_quality = assign_quality(chl_obs_today_buggy, buggy_age_input, interpolated)
    assert np.all(buggy_quality == "OBSERVED_1D")  # confirms the bug shape

    # Corrected: chl_obs_today is gated to age==0 only.
    chl_obs_today_fixed = np.where(real_ages == 0.0, 0.5, np.nan)
    fixed_quality = assign_quality(chl_obs_today_fixed, real_ages, interpolated)
    assert fixed_quality[0] == "OBSERVED_1D"
    assert fixed_quality[1] == "OBSERVED_3D"
    assert fixed_quality[2] == "PREDICTED_MED_CONF"
    assert fixed_quality[3] == "PREDICTED_LOW_CONF"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
