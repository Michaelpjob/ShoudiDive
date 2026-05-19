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


def cdom_obs_trust(dist_to_river_km):
    return np.clip(dist_to_river_km / CDOM_CONTAMINATION_DIST_KM, 0.0, 1.0)


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
    cdom_trust = cdom_obs_trust(dist_to_river_km)
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
