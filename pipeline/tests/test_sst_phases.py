"""Smoke tests for the Phase A-E SST upgrades.

Each phase ships its own module; these tests cover the unit-layer
contracts (no network, no real I/O):

  Phase B  sst_buoy_correction — kriging math + sanity gates
  Phase C  validation/sst_score, validation/sst_watchdog — pure-aggregation
           functions over synthetic residuals
  Phase D  sst_predict/nearshore — graceful no-input behavior +
           gradient/distance helpers
  Phase E  fetch_sst_5day — persistence_decay_forecast math
           (no PNG / manifest IO)

Tests run under pipeline/tests/ so the existing dev-checks pipeline
job picks them up via ``pytest pipeline/tests/test_*.py``.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# =========================================================================
# Phase B — Buoy correction
# =========================================================================

def test_buoy_correction_imports():
    from sst_buoy_correction import (
        BUOYS, fetch_buoy_readings, kriging_correction_surface, correction_summary
    )
    # Sanity: registry not empty, every entry has the expected keys.
    assert len(BUOYS) > 0
    for b in BUOYS:
        assert {"stn", "name", "lat", "lng"} <= set(b.keys())


def test_buoy_correction_zero_anchors_returns_zero_grid():
    """No anchors == no correction, full bbox stays untouched."""
    from sst_buoy_correction import kriging_correction_surface
    H, W = 20, 30
    sst = np.full((H, W), 16.0, dtype=np.float32)
    lats = np.linspace(37.6, 31.8, H)
    lngs = np.linspace(-124.0, -116.8, W)
    correction, anchors = kriging_correction_surface(
        sst_grid_c=sst, lats=lats, lngs=lngs, buoys=[],
    )
    assert correction.shape == sst.shape
    assert np.all(correction == 0.0)
    assert anchors == []


def test_buoy_correction_single_anchor_centers_at_residual():
    """One anchor at the center → kernel-weighted correction peaks at
    the residual value at that location and decays outward."""
    from sst_buoy_correction import kriging_correction_surface, BuoyReading
    H, W = 30, 40
    lats = np.linspace(37.6, 31.8, H)
    lngs = np.linspace(-124.0, -116.8, W)
    sst = np.full((H, W), 16.0, dtype=np.float32)
    # Place a single buoy at the bbox centroid; observed = 17 °C.
    buoy = BuoyReading(
        stn="x", name="test",
        lat=float(lats[H // 2]), lng=float(lngs[W // 2]),
        wtmp_c=17.0, n_samples=24, age_hours=1.0,
    )
    correction, anchors = kriging_correction_surface(
        sst_grid_c=sst, lats=lats, lngs=lngs, buoys=[buoy],
    )
    assert anchors[0]["residual_c"] == pytest.approx(1.0)
    # Center of bbox should see ~+1.0 °C correction; far corner less.
    center = correction[H // 2, W // 2]
    corner = correction[0, 0]
    assert center > 0.7   # close to 1.0, kernel makes it slightly less
    assert corner >= 0.0  # always toward the residual sign here
    assert center > corner


def test_buoy_correction_clamps_extreme_residual():
    """A bogus 10°C residual gets dropped from the anchor set, not
    propagated through the surface."""
    from sst_buoy_correction import (
        kriging_correction_surface, BuoyReading, RESIDUAL_SANITY_BOUND_C,
    )
    H, W = 20, 30
    lats = np.linspace(37.6, 31.8, H)
    lngs = np.linspace(-124.0, -116.8, W)
    sst = np.full((H, W), 16.0, dtype=np.float32)
    bogus = BuoyReading(
        stn="bogus", name="bogus",
        lat=float(lats[H // 2]), lng=float(lngs[W // 2]),
        wtmp_c=16.0 + RESIDUAL_SANITY_BOUND_C + 5.0,
        n_samples=24, age_hours=1.0,
    )
    correction, anchors = kriging_correction_surface(
        sst_grid_c=sst, lats=lats, lngs=lngs, buoys=[bogus],
    )
    assert anchors[0].get("skipped"), "bogus anchor was kept"
    assert np.all(correction == 0.0), "bogus anchor leaked into correction"


def test_buoy_correction_import_resolves_under_cron_invocation():
    """Regression guard for the silent-no-op bug (prod, ~3 weeks).

    The production cron runs ``python pipeline/fetch.py`` — cwd is
    ``pipeline/`` and the repo root is NOT on sys.path, so ``pipeline``
    is not an importable package. ``_apply_sst_buoy_correction`` used to
    import *only* ``from pipeline.sst_buoy_correction import …`` with no
    bare fallback, so under the cron it raised ModuleNotFoundError →
    caught → returned None → the buoy correction never ran and no
    ``buoy_correction`` manifest block was emitted.

    The other Phase-B tests insert ``pipeline/`` onto sys.path and import
    bare, so they exercise the module but never fetch.py's integration
    import path — which is exactly how this slipped through. This test
    reproduces the cron invocation faithfully (subprocess, cwd=pipeline)
    and asserts the helper returns a summary block, not None. The network
    is stubbed so the test stays hermetic and fast.
    """
    prog = textwrap.dedent(
        """
        import numpy as np
        # Imported the way `python pipeline/fetch.py` imports it.
        import fetch
        import sst_buoy_correction as sbc

        # Stub the NDBC fetch so we exercise ONLY the import path inside
        # the helper, never the live network. One in-bbox anchor.
        def _fake_readings(**_kw):
            return [sbc.BuoyReading(
                stn="46086", name="San Clemente Basin",
                lat=32.499, lng=-118.034,
                wtmp_c=20.0, n_samples=24, age_hours=1.0,
            )]
        sbc.fetch_buoy_readings = _fake_readings

        stack = [np.full((12, 14), 19.0, dtype="float32")]
        summ = fetch._apply_sst_buoy_correction(stack=stack, grid_h=12, grid_w=14)
        assert summ is not None, (
            "buoy correction returned None under the cron invocation — "
            "the pipeline.-prefixed import fell through to ImportError"
        )
        assert summ.get("method") == "kriging_gaussian"
        print("CRON_IMPORT_OK anchors=%s" % summ.get("n_anchors_total"))
        """
    )
    r = subprocess.run(
        [sys.executable, "-c", prog],
        cwd=str(ROOT),  # ROOT == pipeline/ — mimics `python pipeline/fetch.py`
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, (
        f"cron-invocation buoy import regressed:\nSTDOUT:\n{r.stdout}\n"
        f"STDERR:\n{r.stderr}"
    )
    assert "CRON_IMPORT_OK" in r.stdout, r.stdout


# =========================================================================
# Phase C — sst_score + sst_watchdog
# =========================================================================

def test_sst_score_imports():
    from validation import sst_score
    # Surface check: all the expected helpers exist.
    assert callable(sst_score.score_all_observations)
    assert callable(sst_score.per_zone_metrics)
    assert callable(sst_score.per_spot_metrics)
    assert callable(sst_score._haversine_km)
    assert callable(sst_score._nearest_spot)


def test_sst_score_per_zone_metrics_shape():
    """Synthetic residuals across two zones — verify aggregation."""
    from validation import sst_score
    residuals = [
        {"residual_f": 2.0, "residual_c": 1.11, "zone": "bight_nearshore",
         "predicted_c": 16.5, "observed_c": 15.4, "source_confidence": 0.9},
        {"residual_f": 1.0, "residual_c": 0.55, "zone": "bight_nearshore",
         "predicted_c": 17.0, "observed_c": 16.4, "source_confidence": 0.9},
        {"residual_f": -0.5, "residual_c": -0.28, "zone": "central_offshore",
         "predicted_c": 14.0, "observed_c": 14.3, "source_confidence": 0.9},
    ]
    out = sst_score.per_zone_metrics(residuals)
    assert "bight_nearshore" in out
    assert out["bight_nearshore"]["n"] == 2
    assert out["bight_nearshore"]["bias_f"] == pytest.approx(1.5)


def test_sst_score_per_spot_metrics_includes_alpha_suggestion():
    from validation import sst_score
    residuals = [
        {"residual_f":  1.6, "residual_c":  0.89, "zone": "bight_nearshore",
         "spot_id": "lajolla", "predicted_c": 16.5, "observed_c": 15.6,
         "source_confidence": 0.9},
        {"residual_f":  1.4, "residual_c":  0.78, "zone": "bight_nearshore",
         "spot_id": "lajolla", "predicted_c": 17.0, "observed_c": 16.2,
         "source_confidence": 0.9},
    ]
    out = sst_score.per_spot_metrics(residuals)
    assert out["lajolla"]["n"] == 2
    assert out["lajolla"]["bias_f"] == pytest.approx(1.5)
    # α delta should flip sign — to correct +1.5 bias we subtract 1.5.
    assert out["lajolla"]["suggested_alpha_delta_f"] == pytest.approx(-1.5)


def test_sst_watchdog_rules_run_on_synthetic_metrics():
    from validation import sst_watchdog
    zone_metrics = {
        "bight_nearshore": {
            "n": 40, "bias_f": 2.1, "rmse_f": 2.4, "pearson_r": 0.85,
        },
    }
    spot_metrics = {
        "lajolla": {
            "name": "La Jolla", "n": 25, "bias_f": -1.8, "rmse_f": 2.0,
            "pearson_r": 0.8, "suggested_alpha_delta_f": 1.8,
        },
    }
    f1 = sst_watchdog.rule_zone_bias(zone_metrics)
    f5 = sst_watchdog.rule_spot_bias(spot_metrics)
    assert any(f["rule"] == "R1" for f in f1)
    assert any(f["rule"] == "R5" for f in f5)


# =========================================================================
# Phase D — Nearshore enhancement
# =========================================================================

def test_nearshore_imports():
    from sst_predict import nearshore
    assert callable(nearshore.compute_all_corrections)
    assert callable(nearshore.upwelling_correction)


def test_nearshore_no_inputs_returns_empty_layers(tmp_path, monkeypatch):
    """When neither bathy.png nor wind PNGs are published, the module
    returns no layers and a zero correction grid — fetch.py treats
    that as 'don't apply', layer ships normally."""
    from sst_predict import nearshore
    monkeypatch.setattr(nearshore, "PUBLIC_DATA", tmp_path)
    result = nearshore.compute_all_corrections(target_h=20, target_w=30)
    assert result["layers"] == []
    assert result["total_delta_c"].shape == (20, 30)
    assert np.all(result["total_delta_c"] == 0.0)


def test_nearshore_bathy_gradient_norm():
    from sst_predict.nearshore import bathy_gradient_norm
    # A simple ramp — gradient should be uniform (= one finite value
    # everywhere); after normalization that's a constant 1.0.
    depth = np.tile(np.linspace(0, 1000, 20).reshape(1, -1), (15, 1)).astype(np.float32)
    g = bathy_gradient_norm(depth)
    assert g.shape == depth.shape
    assert g.min() >= 0 and g.max() <= 1.0
    # The interior of the ramp has uniform gradient → normalised to 1.0.
    assert g[5, 10] == pytest.approx(1.0, abs=0.01)


def test_nearshore_coastal_distance_proxy():
    from sst_predict.nearshore import coastal_distance_proxy_km
    depth = np.array([[0, 100, 200, 600, 1500]], dtype=np.float32)
    d = coastal_distance_proxy_km(depth)
    # 0 m → 0 km, 100 m → 2.5 km, 200 m → 5 km, 600 m → 10 km, 1500 m → 30 km
    assert d[0, 0] == pytest.approx(0.0,  abs=0.1)
    assert d[0, 1] == pytest.approx(2.5,  abs=0.1)
    assert d[0, 2] == pytest.approx(5.0,  abs=0.1)
    assert d[0, 3] == pytest.approx(10.0, abs=0.5)
    assert d[0, 4] == pytest.approx(30.0, abs=0.1)


# =========================================================================
# Phase E — Persistence-decay forecast
# =========================================================================

def test_forecast_imports():
    # Importing fetch_sst_5day at module top-level pulls sst_predict.config
    # which is enough to verify the top-level constants are still wired.
    import fetch_sst_5day
    assert fetch_sst_5day.HORIZON_DAYS == 7
    assert callable(fetch_sst_5day.persistence_decay_forecast)


def test_forecast_day_0_equals_nowcast():
    """At lead 0, forecast must equal today's SST exactly. Sanity check
    that the math doesn't quietly drift the 'today' frame."""
    from fetch_sst_5day import persistence_decay_forecast
    H, W = 5, 7
    sst_now   = np.full((H, W), 17.0, dtype=np.float32)
    sst_climo = np.full((H, W), 16.0, dtype=np.float32)  # cold prior
    tau       = np.full((H, W), 10.0, dtype=np.float32)
    fc = persistence_decay_forecast(
        sst_now_c=sst_now, sst_climo_c=sst_climo,
        tau_days=tau, horizon_days=7,
    )
    assert fc.shape == (7, H, W)
    np.testing.assert_array_almost_equal(fc[0], sst_now, decimal=3)


def test_forecast_decays_toward_climatology():
    """At long lead, forecast must converge toward climatology."""
    from fetch_sst_5day import persistence_decay_forecast
    H, W = 4, 6
    sst_now   = np.full((H, W), 17.0, dtype=np.float32)
    sst_climo = np.full((H, W), 16.0, dtype=np.float32)
    tau       = np.full((H, W), 2.0, dtype=np.float32)   # short τ for fast decay
    fc = persistence_decay_forecast(
        sst_now_c=sst_now, sst_climo_c=sst_climo,
        tau_days=tau, horizon_days=7,
    )
    # By day 6 with τ=2, anomaly should be down by exp(-3)=5%, leaving
    # ~+0.05 °C above climatology (16.05 °C). Tolerance 0.1.
    assert fc[6].mean() == pytest.approx(16.05, abs=0.1)


def test_forecast_zero_anomaly_stays_at_climatology():
    """If today already equals climatology, forecast is flat at climatology
    for every lead — anomaly is 0, exp(-d/τ)*0 = 0."""
    from fetch_sst_5day import persistence_decay_forecast
    H, W = 3, 4
    val = 15.0
    sst_now   = np.full((H, W), val, dtype=np.float32)
    sst_climo = np.full((H, W), val, dtype=np.float32)
    tau       = np.full((H, W), 5.0, dtype=np.float32)
    fc = persistence_decay_forecast(
        sst_now_c=sst_now, sst_climo_c=sst_climo,
        tau_days=tau, horizon_days=7,
    )
    np.testing.assert_array_almost_equal(fc, np.full((7, H, W), val), decimal=3)
