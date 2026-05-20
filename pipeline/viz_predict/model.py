"""Three-stage chl-a prediction: climatology -> persistence -> driver model."""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass

from .config import (
    PERSISTENCE_TAU_DAYS, DRIVER_COEFFS, SIGMA_LOG_CHL,
    CHL_MIN_MGPM3, CHL_MAX_MGPM3,
    PRED_AGE_HIGH_CONF_DAYS, PRED_AGE_LOW_CONF_DAYS,
    CDOM_CONTAMINATION_DIST_KM,
)


@dataclass
class ChlPrediction:
    chl_p50:  np.ndarray
    chl_p10:  np.ndarray
    chl_p90:  np.ndarray
    quality:  np.ndarray
    age_days: np.ndarray


def persistence_with_decay(chl_lastvalid, chl_lastvalid_age_days, chl_climo, zone):
    log_obs   = np.log(np.where(np.isnan(chl_lastvalid), chl_climo, chl_lastvalid))
    log_climo = np.log(np.maximum(chl_climo, CHL_MIN_MGPM3))
    tau = np.vectorize(lambda z: PERSISTENCE_TAU_DAYS.get(str(z), 5.0))(zone)
    age = np.where(np.isnan(chl_lastvalid_age_days), 999.0, chl_lastvalid_age_days)
    weight_obs = np.exp(-age / tau)
    log_pred = weight_obs * log_obs + (1 - weight_obs) * log_climo
    return np.exp(log_pred)


def driver_adjustment(zone, upwell, swell, precip, river, sst, seasonal,
                      exposure, tide, substrate, cloud, trend=None):
    log_adj = np.zeros_like(upwell, dtype=np.float64)
    # 2026-05-19: `trend` is a derivative companion to `sst` (3-day
    # cooling rate). Default None so existing callers / test fixtures
    # that don't pass it stay valid — zero contribution to log_adj.
    if trend is None:
        trend = np.zeros_like(upwell, dtype=np.float64)
    for z in np.unique(zone):
        z_str = str(z)
        if z_str not in DRIVER_COEFFS:
            continue
        c = DRIVER_COEFFS[z_str]
        m = (zone == z)
        log_adj[m] = (
              c.upwell    * upwell[m]
            + c.swell     * swell[m]
            + c.precip    * precip[m]
            + c.river     * river[m]
            + c.sst       * sst[m]
            + c.trend     * trend[m]
            + c.seasonal  * seasonal[m]
            + c.exposure  * exposure[m]
            + c.tide      * tide[m]
            + c.substrate * substrate[m]
            + c.cloud     * cloud[m]
        )
    return log_adj


def effective_sigma(zone, age_days):
    sigma_base = np.vectorize(lambda z: SIGMA_LOG_CHL.get(str(z), 0.45))(zone)
    tau = np.vectorize(lambda z: PERSISTENCE_TAU_DAYS.get(str(z), 5.0))(zone)
    age = np.where(np.isnan(age_days), 999.0, age_days)
    return sigma_base * np.sqrt(1.0 + age / tau)


def cdom_obs_trust(dist_to_river_km, runoff_idx=None, river_idx=None):
    """Per-cell weight on a fresh chl observation when blending with
    the predicted chl prior.

    Base: geometric distance to nearest river mouth (clip to [0, 1] at
    CDOM_CONTAMINATION_DIST_KM = 3 km). The motivating concern is that
    dissolved organic matter from river outflow can be misread as chl
    by ocean-color algorithms, so near-mouth cells get downweighted.

    2026-05-20 refinement — same bug-class as the Kd_490 fix in PR #67.
    The geometric filter is too aggressive when rivers are not actually
    flowing. In dry CA spring/summer with low river discharge AND no
    recent precipitation, the chl signal near the coast is much more
    likely to be real upwelling bloom than CDOM noise — same Pt
    Conception / Western Channel Islands failure mode the Kd_490 fix
    addressed, but on the chl path instead of the Kd path.

    We compute an "active CDOM" proxy from the runoff_idx and river_idx
    drivers (both pre-decayed by distance from source) and relax the
    geometric filter by up to 80% when both are low. The 80% cap leaves
    a 20% buffer for unmodelled CDOM sources (storm runoff that didn't
    register, urban outfalls, harbor plumes).

    Worked examples (CDOM_CONTAMINATION_DIST_KM = 3 km):

      Pt Conception (1 km from Cojo Creek), dry day, no rain:
        base_trust  = 1/3 = 0.33
        cdom_active = max(runoff_idx≈0, river_idx≈0) = 0
        relief      = 0.8 × (1 − 0) = 0.8
        effective   = 0.33 + 0.67 × 0.8 = 0.87   (87% obs, 13% prior)

      Pt Conception, Pineapple Express in progress:
        cdom_active = ~1.0
        relief      = 0
        effective   = base_trust = 0.33  (full suppression preserved)

      Channel-Islands offshore cell, 30 km from any river:
        base_trust  = 1.0
        effective   = 1.0  (no change either way)

    Backward compatibility: calling cdom_obs_trust with only
    dist_to_river_km returns the original geometric trust. Callers
    that don't pass the new kwargs get the pre-refinement behaviour.
    """
    base_trust = np.clip(dist_to_river_km / CDOM_CONTAMINATION_DIST_KM, 0.0, 1.0)
    if runoff_idx is None and river_idx is None:
        return base_trust
    cdom_active = np.zeros_like(base_trust, dtype=np.float64)
    if runoff_idx is not None:
        # runoff_idx already in [0, 1] (tanh × distance decay).
        cdom_active = np.maximum(cdom_active, np.asarray(runoff_idx, dtype=np.float64))
    if river_idx is not None:
        # river_idx can go NEGATIVE when discharge is below climatology
        # (a DRY river — extra confidence there's no CDOM). Clip the
        # negative side to 0 so dry-river cells don't inflate cdom_active.
        cdom_active = np.maximum(
            cdom_active,
            np.maximum(np.asarray(river_idx, dtype=np.float64), 0.0),
        )
    cdom_active = np.clip(cdom_active, 0.0, 1.0)
    relief = 0.8 * (1.0 - cdom_active)
    return base_trust + (1.0 - base_trust) * relief


def assign_quality(chl_obs_today, chl_lastvalid_age_days, interpolated_mask):
    out = np.full(chl_lastvalid_age_days.shape, "CLIMATOLOGY_ONLY", dtype="<U24")
    has_today = ~np.isnan(chl_obs_today)
    out[has_today] = "OBSERVED_1D"
    age = np.where(np.isnan(chl_lastvalid_age_days), 999.0, chl_lastvalid_age_days)
    fresh = (~has_today) & (age <= 3)
    out[fresh] = "OBSERVED_3D"
    interp = (~has_today) & (age > 3) & interpolated_mask
    out[interp] = "INTERPOLATED"
    pred_high = (~has_today) & (age > 3) & (~interpolated_mask) & (age <= PRED_AGE_HIGH_CONF_DAYS)
    out[pred_high] = "PREDICTED_HIGH_CONF"
    pred_med = (~has_today) & (age > PRED_AGE_HIGH_CONF_DAYS) & (~interpolated_mask) & (age <= PRED_AGE_LOW_CONF_DAYS)
    out[pred_med] = "PREDICTED_MED_CONF"
    pred_low = (~has_today) & (age > PRED_AGE_LOW_CONF_DAYS) & (~interpolated_mask)
    out[pred_low] = "PREDICTED_LOW_CONF"
    return out


def predict_chl(*, chl_obs_today, chl_lastvalid, chl_lastvalid_age_days,
                chl_climo, zone, drivers, dist_to_river_km,
                interpolated_mask=None):
    if interpolated_mask is None:
        interpolated_mask = np.zeros_like(chl_lastvalid_age_days, dtype=bool)

    chl_pers = persistence_with_decay(chl_lastvalid, chl_lastvalid_age_days, chl_climo, zone)

    log_adj = driver_adjustment(
        zone,
        upwell=drivers["upwell"], swell=drivers["swell"],
        precip=drivers["precip"], river=drivers["river"],
        sst=drivers["sst"], seasonal=drivers["seasonal"],
        exposure=drivers["exposure"], tide=drivers["tide"],
        substrate=drivers["substrate"], cloud=drivers["cloud"],
        # `trend` may be absent on pre-2026-05-19 dict shapes; .get()
        # defaults to None which driver_adjustment treats as zero
        # contribution. Keeps test fixtures that build drivers
        # manually working without modification.
        trend=drivers.get("trend"),
    )
    log_chl_pred = np.log(chl_pers) + log_adj

    has_today = ~np.isnan(chl_obs_today)
    # 2026-05-20: pass runoff + river indices so the CDOM filter relaxes
    # when no active runoff/discharge — same bug class as the Kd_490
    # stale-override (PR #67). Without this, near-coast bloom cells in
    # dry spring/summer get the chl observation downweighted to 17-67%
    # because of geometric proximity to a river mouth that's not flowing.
    cdom_trust = cdom_obs_trust(
        dist_to_river_km,
        runoff_idx=drivers.get("precip"),
        river_idx=drivers.get("river"),
    )
    obs_log = np.log(np.maximum(chl_obs_today, CHL_MIN_MGPM3))
    log_chl_pred = np.where(
        has_today,
        cdom_trust * obs_log + (1 - cdom_trust) * log_chl_pred,
        log_chl_pred,
    )

    sigma = effective_sigma(zone, chl_lastvalid_age_days)
    sigma = np.where(has_today & (cdom_trust > 0.9), 0.05, sigma)

    p50 = np.exp(log_chl_pred)
    p10 = np.exp(log_chl_pred - 1.28 * sigma)
    p90 = np.exp(log_chl_pred + 1.28 * sigma)

    p50 = np.clip(p50, CHL_MIN_MGPM3, CHL_MAX_MGPM3)
    p10 = np.clip(p10, CHL_MIN_MGPM3, CHL_MAX_MGPM3)
    p90 = np.clip(p90, CHL_MIN_MGPM3, CHL_MAX_MGPM3)

    quality = assign_quality(chl_obs_today, chl_lastvalid_age_days, interpolated_mask)

    return ChlPrediction(
        chl_p50=p50, chl_p10=p10, chl_p90=p90,
        quality=quality,
        age_days=np.where(np.isnan(chl_lastvalid_age_days), 999.0, chl_lastvalid_age_days),
    )
