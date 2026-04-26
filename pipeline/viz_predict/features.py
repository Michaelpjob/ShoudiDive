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
    along = alongshore_wind_component(u_wind_5d, v_wind_5d, coast_normal_deg)
    along_mean = along.mean(axis=-1)
    return along_mean - along_climo_5d


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
        "upwell":    upwelling_anomaly_5d(u_wind_5d, v_wind_5d, along_climo_5d, coast_normal_deg_for_upwell),
        "swell":     bottom_stir,
        "precip":    runoff_index(precip_7d_mm, dist_to_river_mouth_km),
        "river":     river_discharge_index(river_discharge_cfs, river_climo_cfs, dist_to_river_mouth_km),
        "sst":       sst_anomaly(sst_today, sst_climo),
        "seasonal":  seasonal_residual(chl_climo_doy, chl_climo_annual_mean),
        "exposure":  exposure_index(u_wind_today, v_wind_today, swell_dir_today_deg, swell_height_today_m, coast_normal_deg_field),
        "tide":      tide_index(tide_range_m, depth_m, dist_to_shore_km),
        "substrate": substrate_modifier(is_sandy, bottom_stir),
        "cloud":     cloud_effect(cloud_fraction_7d),
    }
