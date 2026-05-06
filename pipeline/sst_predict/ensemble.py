"""p10 / p50 / p90 uncertainty intervals for SST predictions.

Mirror of viz_predict's p10/p50/p90 pattern. SST has three independent
sources of uncertainty:

  σ_blend(cell)   — disagreement among satellite sources contributing
                    to today's blended cell. 0 when only one source
                    contributed; up to ~1°F when 3 sources all
                    contributed but with different values.

  σ_lead(zone, t) — calibrated forecast σ as a function of zone and
                    lead time, from the empirical residual archive.
                    Stored in config.SIGMA_SST_BY_LEAD; updated by
                    sst_score.py / sst_watchdog.py.

  σ_model(t)      — RTOFS vs HYCOM disagreement at the same lead.
                    Captures structural ocean-model uncertainty
                    (boundary conditions, parameterizations).

Total: σ² = σ_blend² + σ_lead² + σ_model².
Assuming Gaussian errors, p10/p90 = p50 ∓ 1.28·σ.

Real SST forecast errors have heavier tails than Gaussian (especially
during upwelling onset/cessation). The viz model accepts the same
simplification because the "narrow vs wide interval" UX value
dominates the precision-of-tails value. v3 may swap in a quantile
regression if the heavy-tail behavior turns out to matter for
flagging dive-go/no-go decisions.

Status: framework. Implementation in phase 4.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from . import config


def compute_intervals(
    *,
    sst_now_c:        np.ndarray,         # (87, 71)
    sst_forecast_c:   np.ndarray,         # (HORIZON_DAYS, 87, 71)
    sources_used:     dict,               # blend.py output
    rtofs_forecast:   Optional[np.ndarray] = None,
    hycom_forecast:   Optional[np.ndarray] = None,
    zone_grid:        np.ndarray = None,
) -> dict:
    """Returns p10/p50/p90 grids for now + each lead day.

    Output dict::

      now_p10, now_p50, now_p90               (87, 71)
      forecast_p10, forecast_p50, forecast_p90 (HORIZON_DAYS, 87, 71)
      sigma_blend                              (87, 71)
      sigma_lead                               (HORIZON_DAYS, 87, 71)
      sigma_model                              (HORIZON_DAYS, 87, 71)
      sigma_total                              (HORIZON_DAYS, 87, 71)

    p10 / p90 are p50 ∓ 1.28·sigma_total.
    """
    raise NotImplementedError("phase-4: assemble σ_blend + σ_lead + σ_model")


def sigma_from_source_spread(sources_used: dict, sst_now_c: np.ndarray) -> np.ndarray:
    """Per-cell stddev across satellite sources that contributed.

    blend.py exposes per-source per-cell values. Where multiple
    sources voted, σ = numpy.nanstd of the contributors. Where only
    one source contributed, σ = its calibrated single-source error
    (literature: MUR ~0.4°F, VIIRS ~0.5°F, GOES ~0.7°F).
    """
    raise NotImplementedError("phase-4 stub")


def sigma_from_lead_climatology(
    zone_grid:  np.ndarray,
    lead_days:  int,
) -> np.ndarray:
    """Look up SIGMA_SST_BY_LEAD[zone][lead_days] per cell."""
    raise NotImplementedError("phase-4 stub")


def sigma_from_model_spread(
    rtofs_lead_t: np.ndarray,
    hycom_lead_t: np.ndarray,
) -> np.ndarray:
    """|RTOFS − HYCOM| / 2 at each lead time. Capped at a sane maximum
    so a model that goes off the rails doesn't blow up the interval."""
    raise NotImplementedError("phase-4 stub")
