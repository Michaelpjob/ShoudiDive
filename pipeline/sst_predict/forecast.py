"""+1 .. +7 day SST forecast via persistence + advection + heat flux.

Two-stage propagation of today's blended field, mirroring the
hierarchy of skill in operational ocean forecasting:

  Stage 1 — Persistence with anomaly decay
    SST_anom(t+τ) = SST_anom(today) · exp(-τ / PERSISTENCE_TAU_DAYS[zone])
    Anomaly = today − climatology. Decays toward 0 (= climatology).

  Stage 2 — Ocean-model advection (when WCOFS/RTOFS available)
    Advect today's anomaly by the model's surface currents at each lead
    time. Fills in the spatial pattern that pure persistence misses
    (upwelling cell migration, eddies).

  Stage 3 — Atmospheric heat-flux correction
    Q_net = SW + LW + SH + LH (NCEP COARE 3.0 from HRRR/GFS T2m + winds)
    ΔT_skin = HEAT_FLUX_GAIN[zone] · Q_net · dt / (ρ · c_p · MLD[zone])

The output is a (lead_days, 87, 71) array. Phase-3 also writes per-day
PNGs and a summary.json mirroring fetch_wind_5day's pattern, so the
React + RN clients pick up the SST forecast UI for free.

Status: framework. Implementation in phase 3.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np

from . import config


HORIZON_DAYS = 7


def forecast(
    *,
    sst_now_c:        np.ndarray,         # (87, 71) blender output
    sst_climo_c:      np.ndarray,         # (87, 71) MUR climatology for today's DOY
    zone_grid:        np.ndarray,         # (87, 71) zone-id ints
    rtofs_forecast:   Optional[np.ndarray] = None,   # (lead_h, 87, 71)
    wcofs_forecast:   Optional[np.ndarray] = None,   # (lead_h, 87, 71)
    hrrr_t2m:         Optional[np.ndarray] = None,
    hrrr_winds:       Optional[np.ndarray] = None,
    gfs_t2m:          Optional[np.ndarray] = None,
    gfs_winds:        Optional[np.ndarray] = None,
) -> dict:
    """Return per-day SST forecast.

    Returns a dict with keys::

      sst_c        (HORIZON_DAYS, 87, 71)  — daily mean SST
      anomaly_c    (HORIZON_DAYS, 87, 71)  — anomaly vs climatology
      confidence   list[str]               — per-day "high"/"medium"/"low"
      stages_used  list[set[str]]          — which stages contributed per day

    Per-day confidence schedule (matches fetch_wind_5day pattern):

      day 0     "high"     (= today's blend, no propagation)
      day 1-2   "high"     (HRRR direct, WCOFS regional)
      day 3-4   "medium"   (RTOFS lead 49-96 h, GFS-only forcing)
      day 5-7   "low"      (RTOFS only, anomaly decayed toward climo)

    The UI uses these confidence tags to dim the longer-lead days the
    same way the wind 5-day forecast already does.
    """
    raise NotImplementedError(
        "phase-3: implement persistence + advection + heat-flux stages")


def _persistence_step(
    sst_now_c:    np.ndarray,
    sst_climo_c:  np.ndarray,
    zone_grid:    np.ndarray,
    lead_days:    int,
) -> np.ndarray:
    """Decay today's anomaly toward climatology.

    SST(t+τ) = climo + (today - climo) · exp(-τ/τ_zone)
    """
    raise NotImplementedError("phase-3 stub")


def _advection_step(
    sst_field_c:        np.ndarray,
    surface_currents_u: np.ndarray,
    surface_currents_v: np.ndarray,
    dt_hours:           float,
) -> np.ndarray:
    """Simple semi-Lagrangian advection. Phase-3 spec:

    For each cell (i,j) at time t+dt, find the upstream cell at time t
    by tracing back along the (u, v) field at (i,j). Bilinear-sample
    the value. This is the standard 'characteristics' method —
    numerically stable + cheap. Real ocean models do this too, just
    on the model grid before regridding to ours.
    """
    raise NotImplementedError("phase-3 stub")


def _heat_flux_step(
    sst_field_c: np.ndarray,
    t2m_c:       np.ndarray,
    u10:         np.ndarray,
    v10:         np.ndarray,
    sw_dn:       Optional[np.ndarray],   # downward shortwave, W/m²
    zone_grid:   np.ndarray,
    dt_hours:    float,
) -> np.ndarray:
    """COARE 3.0 bulk-flux correction.

    Q_net = Q_sw - Q_lw - Q_sh - Q_lh

    Phase-3 implementation reuses the COARE coefficients NCEP uses for
    RTOFS forcing (well-validated in the literature). For v1, an
    air-sea ΔT proxy with a sensible-heat-only term is enough — full
    latent-heat needs humidity which HRRR doesn't expose at 10 m.

    ΔT_water = HEAT_FLUX_GAIN[zone] · Q_net · dt / (ρ_w · c_p · MLD[zone])
    """
    raise NotImplementedError("phase-3 stub")


def _per_day_confidence(stages_used: list[set]) -> str:
    """Map stages used → 'high'/'medium'/'low' tag.

    high   if WCOFS or HRRR direct contributed
    medium if RTOFS or GFS contributed but not WCOFS/HRRR
    low    if pure persistence was the only contributor
    """
    raise NotImplementedError("phase-3 stub")
