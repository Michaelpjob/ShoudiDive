"""Multi-source per-cell SST blender → "now" field.

Mirror of ``pipeline/chl_blend.py``: walk every satellite source in
priority order, walk back N days per source until a non-NaN cell is
found, then per-cell pick the value with the lowest age × distrust.

The blender produces three sidecar PNGs the manifest exposes:

  sst_now.png            blended grayscale °C linear (matches existing range)
  sst_now_age_days.png   per-cell age in days (0 = today)
  sst_now_source.png     per-cell source-id index (legend in manifest)

Status: framework. Implementation in phase 2.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np

from . import config, sources


def blend_now(today: date) -> dict:
    """Build today's blended SST nowcast from the satellite registry.

    Returns a dict with keys::

      sst_c            (87, 71) np.float32 — blended SST, NaN for no-data
      age_days         (87, 71) np.float32 — age of each cell's source in days
      source_id        (87, 71) np.int8    — source priority index
      coverage_frac    float                — fraction of cells with valid data
      mean_age_days    float                — area-weighted mean age
      sources_used     dict[str, dict]      — per-source coverage stats

    Algorithm (literally chl_blend.py with priority weights):

    1. For each source in ``sources.SAT_SOURCES`` (priority order):
        for back in 0..source.max_back:
            arr = source.fetcher(today - back days)
            if arr is not None: break
        cache (source.id, arr, back)

    2. Per cell:
        score(s) = α·age_days(s) + β·priority_rank(s)
        winner = argmin(score) over sources with non-NaN value here

    3. Apply BIAS_CORRECTION_F (per-zone) to the blended field.

    4. Return + write PNGs via ``encode.encode_now()``.
    """
    raise NotImplementedError(
        "phase-2: copy chl_blend.py:_blend_cells, swap CHL sources for SST")


def _per_cell_priority_score(
    *,
    age_days:      np.ndarray,
    source_priority: int,
    cloud_penalty: float = 0.0,
) -> np.ndarray:
    """Score lower-is-better (mirror chl_blend's tiebreak).

    α=1.0 day⁻¹, β=1.0 priority-rank — same weights as chl_blend.
    Phase 2 may calibrate β separately if a satellite source's
    actual error vs MUR turns out to merit different weighting.
    """
    raise NotImplementedError("phase-2 stub")


def _apply_zone_bias_correction(
    sst_c:           np.ndarray,
    zone_grid:       np.ndarray,
) -> np.ndarray:
    """Apply per-zone BIAS_CORRECTION_F from config (in °F → converted).

    The bias correction is the FIRST self-tuning loop output: the
    watchdog's R1 finds zones where the model is consistently off by
    >1.5°F and writes a suggested delta into the GitHub Issue. A human
    commits the delta into config.BIAS_CORRECTION_F. Next run picks up
    the corrected baseline.
    """
    raise NotImplementedError("phase-2 stub")
