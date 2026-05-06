"""Hindcast scoring for sst_predict — same pattern as score.py (viz).

For every observation in observations.jsonl with
``observed_sst_f`` set, find the nearest grid cell in the matching
day's archive snapshot, compute the SST residual, aggregate per zone.

Inputs (all already produced by existing pipeline pieces):
  pipeline/validation/data/observations.jsonl    — buoy + dive log obs
  pipeline/validation/data/archive/*.jsonl.gz    — per-cell predictions

Outputs (mirror the viz versions exactly):
  pipeline/validation/data/sst_residuals.jsonl       — per-obs rows
  pipeline/validation/data/sst_per_zone_metrics.json — aggregated stats

The SST residual rows feed sst_watchdog.py — separate from the
visibility watchdog so the two axes triage independently. Both
watchdogs read from the same observations file (a single dive log
post often reports both `observed_secchi_ft` and `observed_sst_f`,
and we want both to count toward their respective metrics without
double-handling).

Status: framework. Implementation in phase 2.

The implementation can almost literally copy ``score.py``:
  - Same ``_load_archive_for_date`` helper
  - Same ``_haversine_km`` / ``_nearest_cell``
  - Replace ``observed_secchi_ft`` filter with ``observed_sst_f``
  - Replace ``viz_p50_ft`` predicted-field key with ``sst_p50_c`` (then
    convert to °F for the residual since obs are reported in °F)
  - Same per-zone aggregation (n, rmse, bias, mae, calibration_pct,
    pearson_r) — the metric definitions are universal
"""
from __future__ import annotations

import pathlib

# ---- Paths --------------------------------------------------------------

HERE = pathlib.Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
RESIDUALS_PATH = DATA_DIR / "sst_residuals.jsonl"
METRICS_PATH   = DATA_DIR / "sst_per_zone_metrics.json"
BASELINE_PATH  = DATA_DIR / "sst_per_zone_baseline.json"   # promoted by hand


LOOKBACK_DAYS = 2  # same as score.py — bounded by ephemeral archive lifetime


def main() -> int:
    """Score today's archived predictions vs ground-truth obs.

    Phase-2 implementation:
      1. Walk observations.jsonl, filter rows with observed_sst_f
      2. For each obs, find the nearest cell in the matching day's
         archive snapshot via _haversine_km
      3. Compute residual_f = observed_sst_f - predicted_sst_f
      4. Tag each row with the obs's zone (already in the archive cell)
      5. Aggregate per zone: n, rmse, bias, mae, calibration_pct, pearson_r
      6. Write sst_residuals.jsonl (one row per obs) and
         sst_per_zone_metrics.json (one row per zone)

    Exit 0 on success. Errors propagate up so the surrounding workflow
    step's failure is visible (this should NOT be continue-on-error
    when wired in phase 4 — we WANT to know if scoring breaks).
    """
    raise NotImplementedError(
        "phase-2: copy score.py:main, swap observed_secchi_ft for observed_sst_f")


if __name__ == "__main__":
    raise SystemExit(main())
