"""End-to-end orchestrator for the viz_predict module.

Wraps zone classification + feature engineering + chl model + Secchi
visibility translation + 0-100 score into a single call.
"""
from __future__ import annotations
import numpy as np
from typing import Optional

from . import zones
from . import features
from . import model
from . import visibility


def predict_all(
    *,
    lat:                np.ndarray,
    lng:                np.ndarray,
    depth_m:            np.ndarray,
    dist_to_shore_km:   np.ndarray,
    dist_to_island_km:  np.ndarray,
    dist_to_river_km:   np.ndarray,
    coast_normal_deg:   np.ndarray,
    is_kelp:            Optional[np.ndarray] = None,
    is_sandy:           Optional[np.ndarray] = None,

    chl_obs_today:      np.ndarray = None,
    chl_lastvalid:      np.ndarray = None,
    chl_lastvalid_age_days: np.ndarray = None,

    chl_climo_doy:      np.ndarray = None,
    chl_climo_annual:   np.ndarray = None,

    sst_today:          np.ndarray = None,
    sst_climo:          np.ndarray = None,

    u_wind_5d:          np.ndarray = None,
    v_wind_5d:          np.ndarray = None,
    along_climo_5d:     np.ndarray = None,
    u_wind_today:       np.ndarray = None,
    v_wind_today:       np.ndarray = None,

    sig_wave_height_3d_max: np.ndarray = None,
    peak_period_3d_max:     np.ndarray = None,
    swell_dir_today_deg:    np.ndarray = None,
    swell_height_today_m:   np.ndarray = None,

    precip_7d_mm:           np.ndarray = None,
    river_discharge_cfs:    np.ndarray = None,
    river_climo_cfs:        np.ndarray = None,

    tide_range_today_m:     np.ndarray = None,

    cloud_fraction_7d:      Optional[np.ndarray] = None,

    interpolated_mask:      Optional[np.ndarray] = None,

    coast_normal_deg_for_upwell: float = 295.0,
) -> dict:
    """Compute predicted chl + visibility + 0-100 clarity score per pixel."""

    if is_kelp is None:
        is_kelp = np.zeros_like(depth_m, dtype=bool)
    if is_sandy is None:
        is_sandy = np.zeros_like(depth_m, dtype=bool)
    if cloud_fraction_7d is None:
        cloud_fraction_7d = np.full_like(depth_m, 0.55, dtype=np.float64)

    zone = zones.classify_zone(lat, dist_to_shore_km, dist_to_island_km, depth_m)
    isl_name, isl_dist, isl_side = zones.nearest_channel_island(lat, lng)

    drivers = features.assemble_features(
        u_wind_5d=u_wind_5d, v_wind_5d=v_wind_5d, along_climo_5d=along_climo_5d,
        u_wind_today=u_wind_today, v_wind_today=v_wind_today,
        sig_wave_height_3d_max=sig_wave_height_3d_max,
        peak_period_3d_max=peak_period_3d_max, depth_m=depth_m,
        swell_dir_today_deg=swell_dir_today_deg,
        swell_height_today_m=swell_height_today_m,
        coast_normal_deg_field=coast_normal_deg,
        dist_to_shore_km=dist_to_shore_km,
        precip_7d_mm=precip_7d_mm,
        dist_to_river_mouth_km=dist_to_river_km,
        river_discharge_cfs=river_discharge_cfs,
        river_climo_cfs=river_climo_cfs,
        sst_today=sst_today, sst_climo=sst_climo,
        chl_climo_doy=chl_climo_doy,
        chl_climo_annual_mean=chl_climo_annual,
        tide_range_m=tide_range_today_m,
        is_sandy=is_sandy,
        cloud_fraction_7d=cloud_fraction_7d,
        coast_normal_deg_for_upwell=coast_normal_deg_for_upwell,
    )

    chl = model.predict_chl(
        chl_obs_today=chl_obs_today,
        chl_lastvalid=chl_lastvalid,
        chl_lastvalid_age_days=chl_lastvalid_age_days,
        chl_climo=chl_climo_doy,
        zone=zone,
        drivers=drivers,
        dist_to_river_km=dist_to_river_km,
        interpolated_mask=interpolated_mask,
    )

    viz = visibility.predict_visibility(
        chl_p10=chl.chl_p10,
        chl_p50=chl.chl_p50,
        chl_p90=chl.chl_p90,
        zone=zone,
        bottom_stir=drivers["swell"],
        runoff_idx=drivers["precip"],
        river_idx=drivers["river"],
        tide_idx=drivers["tide"],
        substrate_term=drivers["substrate"],
        is_kelp=is_kelp,
    )

    return {
        "zone":         zone,
        "island_side":  isl_side,
        "chl_p10":      chl.chl_p10,
        "chl_p50":      chl.chl_p50,
        "chl_p90":      chl.chl_p90,
        "quality":      chl.quality,
        "age_days":     chl.age_days,
        **viz,
        "drivers":      drivers,
    }
