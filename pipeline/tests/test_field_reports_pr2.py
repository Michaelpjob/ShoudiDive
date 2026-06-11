"""Field Reports PR-2 — coverage-driven confidence upgrades.

"Coverage is the currency": a layer earns a tier above its honest floor only
where enough recent, in-band ground truth exists. These lock the earned-vs-
withheld logic so the dot stays honest when the model isn't calibrated.
"""
from __future__ import annotations

from pipeline.validation.coverage import (
    compute_coverage,
    earned_tier_delta,
    viz_rollup,
    VIZ_UPGRADE_MAX_RMSE,
    VIZ_UPGRADE_MIN_CAL,
    VIZ_UPGRADE_MIN_OBS,
)


def test_no_upgrade_without_calibration():
    # Current prod reality: many obs but 0% in-band -> no upgrade, honest dot.
    assert earned_tier_delta(148, 0.0, 9.65) == 0


def test_no_upgrade_without_volume():
    assert earned_tier_delta(3, 0.9, 2.0) == 0  # great fit, too few obs


def test_no_upgrade_when_rmse_too_high():
    assert earned_tier_delta(50, 0.7, 12.0) == 0


def test_upgrade_when_earned():
    assert earned_tier_delta(VIZ_UPGRADE_MIN_OBS, VIZ_UPGRADE_MIN_CAL, VIZ_UPGRADE_MAX_RMSE) == 1
    assert earned_tier_delta(60, 0.8, 3.5) == 1


def test_upgrade_capped_at_one():
    # viz never reaches "Validated" — dive-report visibility is inherently noisy.
    assert earned_tier_delta(10_000, 1.0, 0.1) == 1


def test_viz_rollup_weights_by_n():
    zones = {
        "a": {"n": 1, "calibration_pct": 0.0, "rmse_ft": 10.0},
        "b": {"n": 3, "calibration_pct": 1.0, "rmse_ft": 2.0},
    }
    r = viz_rollup(zones)
    assert r["n"] == 4
    assert r["calibration_pct"] == 0.75  # (0*1 + 1*3)/4
    assert r["rmse_ft"] == 4.0           # (10*1 + 2*3)/4


def test_viz_rollup_empty():
    assert viz_rollup({})["n"] == 0
    assert viz_rollup(None)["n"] == 0


def test_compute_coverage_shape():
    cov = compute_coverage()
    assert "viz" in cov
    v = cov["viz"]
    for k in ("n_recent", "scored_n", "calibration_pct", "rmse_ft", "tier_delta", "as_of"):
        assert k in v
    assert v["tier_delta"] in (0, 1)
