"""Chl -> Secchi visibility + 0-100 clarity score (piecewise per vis.md bands)."""
from __future__ import annotations
import numpy as np

from .config import (
    SECCHI_COEFFS, TURBIDITY_CORRECTIONS,
    SECCHI_MIN_M, SECCHI_MAX_M,
    CLARITY_CATEGORIES,
)


def secchi_from_chl(chl_mgpm3, zone):
    out = np.zeros_like(chl_mgpm3, dtype=np.float64)
    for z in np.unique(zone):
        z_str = str(z)
        if z_str not in SECCHI_COEFFS:
            continue
        c = SECCHI_COEFFS[z_str]
        m = (zone == z)
        out[m] = c.a * (chl_mgpm3[m] ** -c.b)
    return out


def apply_turbidity_corrections(secchi_m, zone, bottom_stir, runoff_idx,
                                 river_idx, tide_idx, substrate_term, is_kelp=None):
    out = secchi_m.copy()
    if is_kelp is None:
        is_kelp = np.zeros_like(secchi_m, dtype=bool)
    for z in np.unique(zone):
        z_str = str(z)
        if z_str not in TURBIDITY_CORRECTIONS:
            continue
        c = TURBIDITY_CORRECTIONS[z_str]
        m = (zone == z)
        out[m] = (
            out[m]
            - c.swell     * bottom_stir[m]
            - c.runoff    * runoff_idx[m]
            - c.river     * river_idx[m]
            # Kelp penalty is now CONDITIONAL on bottom-stir: kelp forests
            # filter particulates and shelter the water column on calm days
            # (Pt. Loma at flat seas reads CLEARER than open water just
            # offshore). The penalty only applies when waves are actually
            # dislodging canopy debris, scaled by the same bottom_stir
            # signal that drives the swell turbidity term.
            - c.kelp      * is_kelp[m].astype(np.float64) * bottom_stir[m]
            - c.substrate * substrate_term[m]
            - c.tide      * tide_idx[m]
        )
    return out


# Piecewise-linear Secchi-to-score, calibrated to the published band edges:
#     0   ft / 0.0  m  -> score 0     (low edge of Poor)
#    10   ft / 3.0  m  -> score 20    (Poor → Fair)
#    20   ft / 6.1  m  -> score 40    (Fair → Good)
#    30   ft / 9.1  m  -> score 60    (Good → Very Good)
#    50   ft / 15.2 m  -> score 80    (Very Good → Excellent)
#   ~80   ft / 24.4 m  -> score 100   (top of the encoding range)
_BAND_KNOTS_M    = np.array([0.0, 3.0, 6.1, 9.1, 15.2, 24.4])
_BAND_KNOT_SCORE = np.array([0.0, 20.0, 40.0, 60.0, 80.0, 100.0])


def secchi_to_score(secchi_m):
    """Piecewise-linear score so band edges line up with framework bands."""
    score = np.interp(secchi_m, _BAND_KNOTS_M, _BAND_KNOT_SCORE)
    return np.clip(score, 0.0, 100.0)


def score_to_category(score):
    labels = np.empty(score.shape, dtype="<U10")
    colors = np.empty(score.shape, dtype="<U7")
    for lo, hi, label, color in CLARITY_CATEGORIES:
        m = (score >= lo) & (score < hi)
        labels[m] = label
        colors[m] = color
    return labels, colors


def predict_visibility(*, chl_p10, chl_p50, chl_p90, zone,
                        bottom_stir, runoff_idx, river_idx, tide_idx,
                        substrate_term, is_kelp=None):
    secchi_clear  = secchi_from_chl(chl_p10, zone)
    secchi_median = secchi_from_chl(chl_p50, zone)
    secchi_turbid = secchi_from_chl(chl_p90, zone)

    common = dict(zone=zone, bottom_stir=bottom_stir, runoff_idx=runoff_idx,
                  river_idx=river_idx, tide_idx=tide_idx,
                  substrate_term=substrate_term, is_kelp=is_kelp)
    secchi_clear  = apply_turbidity_corrections(secchi_clear,  **common)
    secchi_median = apply_turbidity_corrections(secchi_median, **common)
    secchi_turbid = apply_turbidity_corrections(secchi_turbid, **common)

    secchi_clear  = np.clip(secchi_clear,  SECCHI_MIN_M, SECCHI_MAX_M)
    secchi_median = np.clip(secchi_median, SECCHI_MIN_M, SECCHI_MAX_M)
    secchi_turbid = np.clip(secchi_turbid, SECCHI_MIN_M, SECCHI_MAX_M)

    secchi_turbid = np.minimum(secchi_turbid, secchi_median)
    secchi_clear  = np.maximum(secchi_clear,  secchi_median)

    score = secchi_to_score(secchi_median)
    score_p10 = secchi_to_score(secchi_turbid)
    score_p90 = secchi_to_score(secchi_clear)
    label, color = score_to_category(score)

    return {
        "viz_p10_m":  secchi_turbid, "viz_p50_m":  secchi_median, "viz_p90_m":  secchi_clear,
        "viz_p10_ft": secchi_turbid * 3.281, "viz_p50_ft": secchi_median * 3.281, "viz_p90_ft": secchi_clear * 3.281,
        "score": score, "score_p10": score_p10, "score_p90": score_p90,
        "category": label, "color": color,
    }
