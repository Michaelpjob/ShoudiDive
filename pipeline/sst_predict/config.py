"""All tunable parameters for sst_predict.

Mirror of ``viz_predict.config`` — same zone scheme, same coefficient-
hash discipline (so ``validation/archive.py`` can attribute residuals
to a specific config version when scoring SST).

Status: framework. Numeric values here are PLACEHOLDERS based on
literature defaults + analogues from the visibility model. They get
replaced with empirically-tuned values once the validation pipeline
has accumulated enough residuals (see Phase 4 in ``README.md``).
"""
from dataclasses import dataclass
from typing import Dict


# ---- Zones --------------------------------------------------------------
#
# We use the SAME 9-zone (3 lat × 3 dist-to-shore) classification as
# viz_predict. Re-using zones means scoring can cross-reference SST
# residuals against visibility residuals in the same zones — the two
# axes (water clarity, water temperature) often co-vary, and the
# watchdog can spot zones where BOTH are off (= structural pipeline
# problem) vs only one off (= source-specific problem).
#
# Imported at runtime to avoid circular imports during static analysis.

LAT_LABELS    = ["central", "transition", "bight"]
DIST_LABELS   = ["nearshore", "islands", "offshore"]


def zone_key(lat_label: str, dist_label: str) -> str:
    return f"{lat_label}_{dist_label}"


ALL_ZONES = [zone_key(la, di) for la in LAT_LABELS for di in DIST_LABELS]


# ---- Per-zone bias correction ------------------------------------------
#
# MUR L4 has a known +0.3-0.5°F warm bias along the CA coast in the
# nearshore band (skin-vs-bulk + cool-skin not modeled in the L4
# product). Empirically derived per-zone offsets get applied to the
# blended "now" field BEFORE forecasting, so downstream forecasts
# inherit a calibrated baseline.
#
# v1 placeholder: 0.0 everywhere (= no correction). Filled in by
# ``sst_watchdog.py`` once R1 (zone bias) has accumulated ≥30 obs/zone
# of consistent-direction signal. The watchdog suggests a delta; a
# human commits the change.

BIAS_CORRECTION_F: Dict[str, float] = {z: 0.0 for z in ALL_ZONES}


# ---- Forecast persistence timescales -----------------------------------
#
# When advancing today's blended field forward in time, the anomaly
# (today − climatology) decays toward 0 with timescale τ. Coastal CA
# nearshore SST has shorter autocorrelation (upwelling events flip the
# anomaly in days); offshore has longer (~weeks).
#
# These are LITERATURE DEFAULTS (Roughgarden et al. 1991; CalCOFI
# autocorrelation analyses). Re-tune from in-house residuals in Phase 4.

PERSISTENCE_TAU_DAYS: Dict[str, float] = {
    "central_nearshore":    7.0,    # upwelling-dominated, short τ
    "central_islands":     10.0,
    "central_offshore":    14.0,
    "transition_nearshore": 9.0,
    "transition_islands":  12.0,
    "transition_offshore": 18.0,
    "bight_nearshore":     12.0,    # warmer, more sluggish
    "bight_islands":       15.0,
    "bight_offshore":      21.0,
}


# ---- Per-zone, per-lead-time σ -----------------------------------------
#
# Used by ``ensemble.py`` to derive p10/p50/p90 intervals. Calibrated
# such that p10-p90 covers ~80% of obs (matches the visibility model's
# CAL_LOW=0.60 / CAL_HIGH=0.95 acceptable band).
#
# v1 placeholder: a hand-crafted ramp from 0.5°F at +0d to 2.5°F at +7d.
# Phase 4 promotes empirical σ from the residual archive — the
# ``check_regression.py`` baseline-promote workflow already used by
# viz_predict is the template.
#
# Schema: SIGMA_SST_BY_LEAD[zone][lead_days_int]

_LEAD_DEFAULT = [0.5, 0.7, 0.9, 1.1, 1.4, 1.7, 2.1, 2.5]  # days 0..7

SIGMA_SST_BY_LEAD: Dict[str, list[float]] = {
    z: list(_LEAD_DEFAULT) for z in ALL_ZONES
}


# ---- Heat-flux gain -----------------------------------------------------
#
# ``forecast.py`` applies a simple bulk heat-flux correction on top of
# persistence: ΔT = HEAT_FLUX_GAIN[zone] * Q_net / (ρ·c_p·MLD), where
# MLD is mixed-layer depth. The gain is a multiplicative scale that
# accounts for sub-grid mixing not captured by the bulk formula.
#
# Default 1.0 = pure bulk-flux physics. Watchdog R3 (correlation)
# suggests changes here when the model under- or over-warms a zone.

HEAT_FLUX_GAIN: Dict[str, float] = {z: 1.0 for z in ALL_ZONES}


# ---- Mixed-layer depth (MLD) climatology -------------------------------
#
# Used by the heat-flux step in ``forecast.py``. Real MLD has a strong
# seasonal cycle (deep winter, shallow stratified summer). v1 uses
# fixed per-zone values from CalCOFI summer climatology; v3 swaps in
# a monthly climatology array.

MLD_M: Dict[str, float] = {
    "central_nearshore":    15.0,
    "central_islands":      20.0,
    "central_offshore":     30.0,
    "transition_nearshore": 12.0,
    "transition_islands":   18.0,
    "transition_offshore":  25.0,
    "bight_nearshore":      10.0,
    "bight_islands":        15.0,
    "bight_offshore":       22.0,
}


# ---- Encoding -----------------------------------------------------------
#
# Match the existing SST PNG encoding so manifest readers don't need
# updating. fetch.py uses range (9.0, 25.0)°C with linear scale — we
# inherit that exactly.

SST_RANGE_C  = (9.0, 25.0)
SST_SCALE    = "linear"
SST_UNIT_C   = "degC"
SST_UNIT_F   = "degF"  # display unit; the PNG values are always °C


# ---- Quality flags -----------------------------------------------------
#
# Mirror of ``viz_predict.QUALITY_FLAGS`` — the React + RN clients
# expect this set as a string enum. SST flags use the same vocabulary
# so the existing UI badges work without UI changes.

QUALITY_FLAGS = {
    "OBSERVED_FRESH":      "Multiple satellite sources <12h old",
    "OBSERVED_1D":         "Satellite source within last day",
    "OBSERVED_3D":         "Satellite source within 3 days",
    "INTERPOLATED":        "Spatially interpolated from neighbors",
    "FORECAST_HIGH_CONF":  "Model output, lead ≤2d, narrow interval",
    "FORECAST_MED_CONF":   "Model output, lead 3-5d",
    "FORECAST_LOW_CONF":   "Model output, lead >5d or active event",
    "CLIMATOLOGY_ONLY":    "No recent obs; seasonal climatology only",
}


# ---- Watchdog thresholds -----------------------------------------------
#
# Mirror of ``validation/watchdog.py`` thresholds, in SST-relevant units.

# R1 — zone bias. Diving-relevant accuracy is ~1°F; flag at 1.5°F.
BIAS_THRESHOLD_F        = 1.5

# R2 — interval calibration. Same band as the viz watchdog.
CAL_LOW                 = 0.60
CAL_HIGH                = 0.95

# R3 — correlation. NDBC vs MUR over multi-day windows is typically
# r > 0.85 in coastal CA; r < 0.5 means the model is missing a major
# driver for this zone.
CORR_LOW                = 0.50

# Min-n thresholds match the viz watchdog. SST obs are denser than
# visibility (every NDBC buoy reports hourly) so 30 obs/zone arrives
# faster — typically within 2-3 days vs visibility's 2 weeks.
MIN_N_BIAS              = 30
MIN_N_CORR              = 50
