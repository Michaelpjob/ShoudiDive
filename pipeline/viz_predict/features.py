"""Feature engineering: drivers from raw inputs."""
from __future__ import annotations
import numpy as np


def doy_features(doy):
    theta = 2 * np.pi * doy / 365.25
    return np.sin(theta), np.cos(theta)


def alongshore_wind_component(u, v, coast_normal_deg=295.0):
    cn = np.deg2rad(coast_normal_deg)
    along_x = -np.sin(cn)
    along_y =  np.cos(cn)
    return u * along_x + v * along_y


def upwelling_anomaly_5d(u_wind_5d, v_wind_5d, along_climo_5d, coast_normal_deg=295.0):
    """Pure-wind upwelling proxy: 5-day mean alongshore wind minus climo.

    Kept as a separate function (no longer the `upwell` driver itself —
    see `upwelling_activity` below) because it's still useful as a
    diagnostic and lets us A/B against the older coupled version if
    the new feature ever produces a regression.
    """
    along = alongshore_wind_component(u_wind_5d, v_wind_5d, coast_normal_deg)
    along_mean = along.mean(axis=-1)
    return along_mean - along_climo_5d


def upwelling_activity(
    u_wind_5d, v_wind_5d, along_climo_5d,
    sst_today, sst_climo,
    coast_normal_deg=295.0,
    wind_scale_mps: float = 5.0,
    sst_scale_degc: float = 2.0,
    out_scale_mps: float = 5.0,
):
    """Coupled wind+SST upwelling-activity signal.

    Pure wind-anomaly upwelling can mis-fire — favorable wind can blow
    onto a coast where offshore advection brings warm surface water in
    and the visible cold/nutrient signature never materialises. Pure
    cold-SST can mis-fire too — an advected cold tongue arrives without
    any local mixing. The signature we actually want (cold deep water
    at the surface, nutrient-loaded, primed to bloom in a day or two)
    requires BOTH:

      * a 5-day mean alongshore wind that is more equatorward than
        climatology (Ekman transport offshore over a sustained window),
      * a today-vs-climo SST cold anomaly (the cold deep water has
        actually reached the surface).

    Returns the geometric mean of two normalised signals
    (wind_anom / wind_scale and -sst_anom / sst_scale, each clipped to
    [0, 1.5]) scaled by `out_scale_mps` to preserve numerical
    comparability with the old `upwelling_anomaly_5d` output. That
    way the per-zone DRIVER_COEFFS.upwell coefficients calibrated
    against the old feature still produce sensible log-chl
    adjustments without a full re-tune — the only behavioural change
    is that the signal now fires only when BOTH inputs are positive,
    not on wind alone or cold-alone.

    Worked examples:
      wind_anom = 5 m/s, sst_anom = -2°C  →  norm 1.0 × 1.0 = 1.0  →  5.0
      wind_anom = 5 m/s, sst_anom =  +1°C →  norm 1.0 × 0.0 = 0.0  →  0.0
      wind_anom = 10, sst_anom = -4°C     →  norm 1.5 × 1.5 = 2.25 →  sqrt → 1.5 → 7.5
      wind_anom = 0,  sst_anom = -4°C     →  norm 0 × 1.5 = 0      →  0.0
      (cold-only and wind-only both score zero — that's intentional.)

    Lag note: this feature only flags that upwelling is ACTIVE. It
    does not predict the chl response timing directly — that's
    handled at the model level by the chl observation blend in
    `predict_chl`. When the chl observation today is fresh and still
    low (day 0 of upwelling, before phyto has multiplied), the obs
    blend pegs the prediction to that fresh observation regardless
    of how high the upwelling_activity signal is. As soon as the next
    daily satellite pass shows the bloom, the obs blend snaps to the
    new (high) chl reading. This matches the physical reality the
    user described: "doesn't green up the first day."
    """
    wind_anom = upwelling_anomaly_5d(
        u_wind_5d, v_wind_5d, along_climo_5d, coast_normal_deg
    )
    wind_norm = np.clip(wind_anom / float(wind_scale_mps), 0.0, 1.5)
    sst_anom = sst_today - sst_climo
    sst_norm = np.clip(-sst_anom / float(sst_scale_degc), 0.0, 1.5)
    combined = np.sqrt(np.maximum(wind_norm * sst_norm, 0.0))
    return combined * float(out_scale_mps)


def exposure_index(u_wind, v_wind, swell_dir_deg, swell_height_m, coast_normal_deg):
    """Combined wind+swell exposure. 0=sheltered/leeward, 1=fully exposed/windward."""
    cn = np.deg2rad(coast_normal_deg)
    nx = np.cos(cn)
    ny = np.sin(cn)
    wind_toward_shore = -(u_wind * nx + v_wind * ny)
    wind_speed = np.hypot(u_wind, v_wind)
    wind_exposure = np.clip(wind_toward_shore / np.maximum(wind_speed, 0.1), -1.0, 1.0)
    wind_exposure = np.maximum(wind_exposure, 0.0)
    swell_x = np.sin(np.deg2rad(swell_dir_deg))
    swell_y = np.cos(np.deg2rad(swell_dir_deg))
    swell_toward_shore = -(swell_x * nx + swell_y * ny)
    swell_exposure = np.clip(swell_toward_shore, 0.0, 1.0)
    swell_term = swell_exposure * np.tanh(swell_height_m / 2.0)
    wind_term = wind_exposure * np.tanh(wind_speed / 7.5)
    return 0.5 * wind_term + 0.5 * swell_term


def bottom_stir_index(sig_wave_height_m, peak_period_s, depth_m):
    g = 9.81
    L_deep = (g * peak_period_s ** 2) / (2 * np.pi)
    k = 2 * np.pi / np.maximum(L_deep, 1.0)
    u_orbital = (np.pi * sig_wave_height_m / np.maximum(peak_period_s, 1.0)) * np.exp(-k * np.maximum(depth_m, 1.0))
    return np.clip(u_orbital, 0.0, 1.0)


def runoff_index(precip_7d_mm, dist_to_river_mouth_km, e_folding_km=5.0):
    decay = np.exp(-dist_to_river_mouth_km / e_folding_km)
    rain_term = np.tanh(precip_7d_mm / 25.0)
    return rain_term * decay


def river_discharge_index(river_discharge_cfs, river_climo_cfs, dist_to_river_mouth_km, e_folding_km=8.0):
    log_anom = np.log(np.maximum(river_discharge_cfs, 1.0)) - np.log(np.maximum(river_climo_cfs, 1.0))
    anom_term = np.tanh(log_anom / 1.5)
    decay = np.exp(-dist_to_river_mouth_km / e_folding_km)
    return anom_term * decay


def tide_index(tide_range_m, depth_m, dist_to_shore_km):
    shallow_term = np.exp(-depth_m / 20.0)
    nearshore_term = np.exp(-dist_to_shore_km / 5.0)
    return np.tanh(tide_range_m / 2.0) * shallow_term * nearshore_term


def sst_anomaly(sst_today, sst_climo):
    return sst_today - sst_climo


def seasonal_residual(chl_climo_doy, chl_climo_annual_mean):
    return np.log(np.maximum(chl_climo_doy, 1e-3)) - np.log(np.maximum(chl_climo_annual_mean, 1e-3))


def substrate_modifier(is_sandy, bottom_stir):
    return is_sandy.astype(np.float64) * bottom_stir


def cloud_effect(cloud_fraction_7d):
    return cloud_fraction_7d - 0.55


def assemble_features(
    *,
    u_wind_5d, v_wind_5d, along_climo_5d,
    u_wind_today, v_wind_today,
    sig_wave_height_3d_max, peak_period_3d_max, depth_m,
    swell_dir_today_deg, swell_height_today_m,
    coast_normal_deg_field, dist_to_shore_km,
    precip_7d_mm, dist_to_river_mouth_km,
    river_discharge_cfs, river_climo_cfs,
    sst_today, sst_climo,
    chl_climo_doy, chl_climo_annual_mean,
    tide_range_m,
    is_sandy,
    cloud_fraction_7d,
    coast_normal_deg_for_upwell=295.0,
):
    bottom_stir = bottom_stir_index(sig_wave_height_3d_max, peak_period_3d_max, depth_m)
    return {
        # 2026-05-19: `upwell` switched from `upwelling_anomaly_5d` (wind
        # only) to `upwelling_activity` (wind ∧ cold-SST coupled). The
        # output magnitude is rescaled to preserve calibration of the
        # existing DRIVER_COEFFS.upwell values, so no per-zone retune
        # was needed — only the SEMANTICS changed (signal fires only
        # when both wind anomaly and SST cold anomaly are positive).
        # See upwelling_activity docstring for the worked examples.
        "upwell":    upwelling_activity(
            u_wind_5d, v_wind_5d, along_climo_5d,
            sst_today, sst_climo,
            coast_normal_deg_for_upwell,
        ),
        "swell":     bottom_stir,
        "precip":    runoff_index(precip_7d_mm, dist_to_river_mouth_km),
        "river":     river_discharge_index(river_discharge_cfs, river_climo_cfs, dist_to_river_mouth_km),
        # `sst` driver stays in place as a separate signal: it catches
        # cold-water situations from causes OTHER than active upwelling
        # (e.g. cold advection from offshore, deep mixed-layer days
        # after a cold-front passage). The `upwell` driver now captures
        # the SPECIFIC compound event of wind-driven upwelling. The
        # two add in log(chl) — confirmed upwelling produces a bigger
        # bump than either signal alone, which is the right physics.
        "sst":       sst_anomaly(sst_today, sst_climo),
        "seasonal":  seasonal_residual(chl_climo_doy, chl_climo_annual_mean),
        "exposure":  exposure_index(u_wind_today, v_wind_today, swell_dir_today_deg, swell_height_today_m, coast_normal_deg_field),
        "tide":      tide_index(tide_range_m, depth_m, dist_to_shore_km),
        "substrate": substrate_modifier(is_sandy, bottom_stir),
        "cloud":     cloud_effect(cloud_fraction_7d),
    }
