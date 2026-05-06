"""Public entry point for sst_predict.

Mirror of viz_predict.predict.predict_all — one function call that
runs the full nowcast + forecast + ensemble pipeline and returns
everything callers need.

Status: framework. Implementation lands across phases 2-4 as the
sub-modules become real.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np

from . import blend, forecast, ensemble


def predict_all(
    *,
    today:       Optional[date] = None,
    horizon_days: int = 7,
) -> dict:
    """Run the full SST nowcast + forecast pipeline.

    Returns a dict with keys::

      now: {
          sst_c, p10_c, p90_c,                   (87, 71) °C
          age_days, source_id,                   (87, 71)
          sources_used, coverage_frac,
          mean_age_days, quality_flag_grid,
      }
      forecast: {
          sst_c, p10_c, p90_c,                   (HORIZON_DAYS, 87, 71)
          confidence,                            list[str]  per-day
          stages_used,                           list[set]  per-day
      }
      meta: {
          generated_at, coeff_hash, sources_attempted,
          sources_succeeded, errors,
      }

    Each sub-step is wrapped in try/except so a single source going
    red doesn't take the whole pipeline down — same fault-tolerance
    pattern as ``fetch.py`` and ``chl_blend.py``. Errors are
    accumulated in ``meta.errors`` for the watchdog to surface.
    """
    raise NotImplementedError(
        "phase-2: orchestrate blend.blend_now() + encode.encode_now()")


def coefficient_hash() -> str:
    """SHA-256 prefix of the active config — same pattern as
    validation/archive.py:coefficient_hash for viz_predict.

    Hashes BIAS_CORRECTION_F + PERSISTENCE_TAU_DAYS + SIGMA_SST_BY_LEAD +
    HEAT_FLUX_GAIN + MLD_M. Excludes registry/threshold metadata that
    doesn't influence the prediction so trivial doc edits don't churn
    the hash.

    Returns the first 12 hex chars (collision space ~10^14 — fine for
    this scale).
    """
    raise NotImplementedError("phase-2 stub — copy archive.coefficient_hash")
