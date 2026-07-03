"""Unit tests for pipeline/check_published.py.

Covers ``check_sst_health`` — the SST-specific manifest watchdog added
after the 2026-06 "buoy correction silently never ran" regression. These
are pure functions over synthetic manifest dicts: no network, no I/O.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from check_published import check_sst_health  # noqa: E402


def _manifest(sst_layer: dict | None) -> dict:
    layers = {}
    if sst_layer is not None:
        layers["sst"] = sst_layer
    return {"generated_at": "2026-07-02T09:00:00Z", "layers": layers}


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


def test_missing_buoy_correction_is_flagged_high():
    """The exact 2026-06 regression: no buoy_correction block at all."""
    m = _manifest({"grid": {"width": 586, "height": 511}})  # no buoy_correction
    findings = check_sst_health(m)
    assert "sst_buoy_correction_missing" in _codes(findings)
    hit = next(f for f in findings if f.code == "sst_buoy_correction_missing")
    assert hit.severity == "high"
    assert hit.layer == "sst"


def test_healthy_correction_no_fallback_is_clean():
    m = _manifest({
        "grid": {"width": 586, "height": 511},
        "buoy_correction": {"method": "kriging_gaussian", "n_anchors_active": 4},
    })
    assert check_sst_health(m) == []


def test_zero_active_anchors_is_flagged_medium():
    m = _manifest({
        "buoy_correction": {"method": "kriging_gaussian", "n_anchors_active": 0},
    })
    findings = check_sst_health(m)
    assert "sst_buoy_correction_no_anchors" in _codes(findings)
    hit = next(f for f in findings if f.code == "sst_buoy_correction_no_anchors")
    assert hit.severity == "medium"


def test_source_fallback_is_flagged_medium():
    """234x206 blended fallback with source_fallback set — coarse-grid alert."""
    m = _manifest({
        "grid": {"width": 234, "height": 206},
        "source": "NOAA Geo-polar blended SST",
        "source_fallback": True,
        "buoy_correction": {"method": "kriging_gaussian", "n_anchors_active": 5},
    })
    findings = check_sst_health(m)
    assert "sst_on_fallback_source" in _codes(findings)
    # The correction is present + active here, so ONLY the fallback fires.
    assert "sst_buoy_correction_missing" not in _codes(findings)


def test_current_prod_state_flags_both():
    """Reproduces the live 2026-07-02 manifest: on the fallback AND missing
    the buoy correction. Both degradations should surface together."""
    m = _manifest({
        "grid": {"width": 234, "height": 206},
        "source": "NOAA Geo-polar blended SST",
        "source_fallback": True,
        # no buoy_correction block
    })
    codes = _codes(check_sst_health(m))
    assert "sst_buoy_correction_missing" in codes
    assert "sst_on_fallback_source" in codes


def test_missing_sst_layer_defers_to_required_layers_check():
    """No sst layer at all → this function stays silent (the generic
    required_layers_missing finding owns that case)."""
    assert check_sst_health(_manifest(None)) == []
