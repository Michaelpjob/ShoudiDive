"""v1 water-column visibility model (PRD C1) — pure functions.

Computes a per-cell two-layer profile from inputs the pipeline already
fetches: wind (upwelling), swell + bathymetry (near-bottom
resuspension), tides (internal-tide cliff swing), and the existing
surface visibility field. No I/O here — `pipeline/fetch_viz_column.py`
owns loading/encoding; these functions own the physics, so the unit
layer can test them without network or files.

Vertical structure modeled:

    surface ──────────────────────── above-cliff vis = surface model
       │  clear layer
    ───┼──── cliff (thermocline proxy; swings with the internal tide)
       │  murk layer                 below-cliff vis = attenuated
    bottom ─────────────────────────

Where the bottom is shallower than the cliff there is no murk layer
(`no_cliff`), and the surface number applies to the whole column.

Everything is numpy-vectorized over the shared viz grid; scalars work
too (numpy broadcasting), which the per-spot sidecar path uses.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np

from viz_column import config as C


# ---- Upwelling index ------------------------------------------------------

def wind_stress(u10: np.ndarray, v10: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Surface wind stress (tau_x, tau_y) in N/m^2 from 10 m wind (m/s).

    Bulk formula with a constant drag coefficient — fine at v1's
    fidelity (Cd's wind-speed dependence shifts strong-wind stress by
    ~20%, well inside the heuristic's error bars).
    """
    speed = np.hypot(u10, v10)
    tau_x = C.RHO_AIR * C.DRAG_COEFFICIENT * speed * u10
    tau_y = C.RHO_AIR * C.DRAG_COEFFICIENT * speed * v10
    return tau_x, tau_y


def upwelling_index(u10: np.ndarray, v10: np.ndarray) -> np.ndarray:
    """Normalized upwelling index in [0, 1] from 10 m wind components.

    Offshore Ekman transport per meter of coast: M = tau_alongshore /
    (rho_sw * f), taking the alongshore-equatorward component of wind
    stress (single coastline angle for the whole bbox at v1). Positive
    M (equatorward stress) drives offshore surface transport ->
    upwelling; poleward stress (downwelling) clamps to 0.
    """
    tau_x, tau_y = wind_stress(np.asarray(u10, dtype=float),
                               np.asarray(v10, dtype=float))
    theta = math.radians(C.ALONGSHORE_EQUATORWARD_DEG)
    # Compass bearing -> unit vector in (east, north) components.
    along_e, along_n = math.sin(theta), math.cos(theta)
    tau_along = tau_x * along_e + tau_y * along_n
    transport = tau_along / (C.RHO_SEAWATER * C.CORIOLIS_F)  # m^2/s
    return np.clip(transport / C.UPWELLING_SATURATION_M2S, 0.0, 1.0)


# ---- Near-bottom resuspension ----------------------------------------------

def _wavenumber(period_s: float | np.ndarray, depth_m: np.ndarray) -> np.ndarray:
    """Linear-dispersion wavenumber k (rad/m) via Hunt's (1979) direct
    approximation — accurate to <0.1% across all depths, no iteration.
    """
    depth_m = np.maximum(np.asarray(depth_m, dtype=float), 0.1)
    omega = 2.0 * math.pi / np.asarray(period_s, dtype=float)
    y = omega**2 * depth_m / C.GRAVITY
    d = (0.666, 0.355, 0.1608465608, 0.0632098765, 0.0217540484, 0.0065407983)
    poly = np.zeros_like(y)
    for n, dn in enumerate(d, start=1):
        poly = poly + dn * y**n
    kd = np.sqrt(y**2 + y / (1.0 + poly))
    return kd / depth_m


def bottom_orbital_velocity(hs_m: np.ndarray, period_s: float | np.ndarray,
                            depth_m: np.ndarray) -> np.ndarray:
    """Near-bottom wave orbital velocity amplitude u_b (m/s).

    Linear theory: u_b = pi * H / (T * sinh(k d)). sinh is clamped to
    avoid overflow in deep water, where u_b -> 0 anyway.
    """
    hs_m = np.asarray(hs_m, dtype=float)
    depth_m = np.maximum(np.asarray(depth_m, dtype=float), 0.1)
    k = _wavenumber(period_s, depth_m)
    kd = np.minimum(k * depth_m, 50.0)
    return (math.pi * hs_m / np.asarray(period_s, dtype=float)) / np.sinh(kd)


def resuspension_index(hs_m: np.ndarray, period_s: float | np.ndarray,
                       depth_m: np.ndarray) -> np.ndarray:
    """Normalized near-bottom resuspension term in [0, 1].

    Ramps from 0 at the critical orbital velocity (below which fine
    sediment stays put) to 1 over ORBITAL_VEL_SCALE_MS.
    """
    u_b = bottom_orbital_velocity(hs_m, period_s, depth_m)
    return np.clip((u_b - C.ORBITAL_VEL_CRITICAL_MS) / C.ORBITAL_VEL_SCALE_MS,
                   0.0, 1.0)


# ---- Cliff depth + diurnal swing -------------------------------------------

def cliff_depth_ft(month: int, lat_deg: np.ndarray | float,
                   upwelling: np.ndarray | float) -> np.ndarray:
    """Cliff (thermocline proxy) depth in feet.

    Seasonal climatological base, shoaled by upwelling, deepened north
    of NORCAL_LAT_DEG where stratification is weaker. Replaced by
    C2's model-derived mixed-layer depth when that lands.
    """
    base = C.CLIFF_BASE_FT_BY_MONTH[int(month)]
    lat = np.asarray(lat_deg, dtype=float)
    up = np.asarray(upwelling, dtype=float)
    cliff = np.full(np.broadcast(lat, up).shape, base, dtype=float)
    cliff = cliff * (1.0 + C.NORCAL_CLIFF_DEEPEN_FRAC * (lat >= C.NORCAL_LAT_DEG))
    cliff = cliff * (1.0 - C.UPWELLING_CLIFF_SHOALING_FRAC * up)
    return np.clip(cliff, C.CLIFF_MIN_FT, C.CLIFF_MAX_FT)


def swing_amplitude_ft(month: int) -> float:
    """Peak-to-peak diurnal cliff swing (ft) — larger when seasonal
    stratification is strong (internal tides displace a sharp
    pycnocline farther)."""
    base = C.CLIFF_BASE_FT_BY_MONTH[int(month)]
    season = (C.SWING_SEASON_DEEP_FT - base) / (
        C.SWING_SEASON_DEEP_FT - C.SWING_SEASON_SHALLOW_FT)
    season = min(max(season, 0.0), 1.0)
    return C.SWING_BASE_FT + C.SWING_STRATIFICATION_EXTRA_FT * season


def cliff_series_ft(cliff_ft: float, month: int,
                    hours_since_high_water: Iterable[float]) -> list[float]:
    """Hourly cliff depth across a day as the internal tide swings it.

    M2-period sinusoid around the mean cliff depth, phase-locked to
    high water: deepest at high water per the documented v1 assumption
    (config.PHASE_DEEPEST_AT_HIGH_WATER) — C4's calibration confirms
    or flips this.
    """
    amp = swing_amplitude_ft(month) / 2.0
    sign = 1.0 if C.PHASE_DEEPEST_AT_HIGH_WATER else -1.0
    out = []
    for h in hours_since_high_water:
        phase = 2.0 * math.pi * (float(h) / C.M2_PERIOD_HOURS)
        out.append(round(float(cliff_ft) + sign * amp * math.cos(phase), 1))
    return out


# ---- Below-cliff visibility -------------------------------------------------

def below_cliff_vis_ft(surface_vis_ft: np.ndarray, upwelling: np.ndarray,
                       resuspension: np.ndarray) -> np.ndarray:
    """Below-cliff visibility (ft): the surface value attenuated by
    upwelled plankton water and resuspended sediment, floored at
    BELOW_VIS_FLOOR_FT and never exceeding the surface value."""
    surface = np.asarray(surface_vis_ft, dtype=float)
    atten = (C.BELOW_ATTENUATION_BASE
             - C.BELOW_ATTENUATION_UPWELLING * np.asarray(upwelling, dtype=float)
             - C.BELOW_ATTENUATION_RESUSPENSION * np.asarray(resuspension, dtype=float))
    atten = np.clip(atten, C.BELOW_ATTENUATION_MIN, C.BELOW_ATTENUATION_MAX)
    below = np.maximum(surface * atten, C.BELOW_VIS_FLOOR_FT)
    return np.minimum(below, surface)


# ---- Column assembly ---------------------------------------------------------

def column(surface_vis_ft, bottom_ft, month, lat_deg, u10, v10, hs_m,
           period_s=C.DEFAULT_SWELL_PERIOD_S):
    """Assemble the full two-layer column for cells or a single point.

    Returns a dict of numpy arrays (or scalars in/scalars out via
    0-d arrays): cliff_ft, below_ft, swing_ft (peak-to-peak), no_cliff
    (bool — bottom shallower than the cliff: clear to the bottom,
    surface number applies; below_ft mirrors surface there).
    """
    surface = np.asarray(surface_vis_ft, dtype=float)
    bottom = np.asarray(bottom_ft, dtype=float)
    up = upwelling_index(u10, v10)
    resus = resuspension_index(hs_m, period_s, bottom / C.FT_PER_M)
    cliff = cliff_depth_ft(month, lat_deg, up)
    below = below_cliff_vis_ft(surface, up, resus)
    no_cliff = bottom <= cliff
    below = np.where(no_cliff, surface, below)
    swing = np.full_like(cliff, swing_amplitude_ft(month))
    return {
        "cliff_ft": cliff,
        "below_ft": below,
        "swing_ft": swing,
        "no_cliff": no_cliff,
        "upwelling": up,
        "resuspension": resus,
    }
