"""Closed-loop validation for the visibility model.

Three pieces ship in v1:

* ``archive`` — writes per-cell prediction snapshots from each
  ``fetch_visibility`` run into gzipped JSONL, tagged with a SHA of
  the active config so every later residual can be attributed to a
  specific coefficient version.

* ``ingest`` — pulls ground-truth observations from public CA water
  sources (CDIP buoys, Eagle 4 dive logs in v1; more sources later).
  Output is a normalized observation table at
  ``pipeline/validation/data/observations.jsonl``.

* ``score`` — KDTree-matches observations against the matching day's
  archive snapshot, computes residual + per-zone RMSE / bias /
  calibration, dumps ``pipeline/validation/data/per_zone_metrics.json``.

The dashboard, LLM extractor, full source catalog, and coefficient
suggestor are deferred until v1 produces a meaningful number of
observations (~30 days at expected scrape rate).
"""
