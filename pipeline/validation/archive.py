"""Per-cell prediction snapshots for hindcast scoring.

Every nightly ``fetch_visibility.py`` run calls
``write_snapshot(grid_lat, grid_lng, predict_result)`` after
``viz_predict.predict_all()`` returns, dumping one record per grid
cell into a date-partitioned gzip JSONL file.

Each record includes the predicted p10/p50/p90 visibility, the
quality flag, the zone label, all 10 driver values, and a SHA of the
active config — so when ``score.py`` later joins observations against
this file, every residual is attributable to a specific coefficient
version. That's the mechanism that makes coefficient changes
data-driven instead of vibes-driven.

Storage: ``pipeline/validation/data/archive/{YYYY}/{MM}/{DD}.jsonl.gz``.
~140×110 cells × ~250 bytes/row gzipped -> ~700 KB/day -> ~250 MB/year.
The archive directory is git-ignored; v1 scoring runs in the same
workflow as ``fetch_visibility``, so today's archive is read off the
ephemeral CI disk before it disappears. Persistent archives require
an R2/LFS sync that's out of scope for v1.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


# Lives next to ``viz_predict``'s data, not under ``public/`` —
# scoring artefacts are infra, not user-facing assets.
ARCHIVE_ROOT = Path(__file__).resolve().parent / "data" / "archive"


def coefficient_hash() -> str:
    """SHA-256 (first 12 hex chars) of the active visibility config.

    Hashes ``DRIVER_COEFFS``, ``SECCHI_COEFFS``, ``TURBIDITY_CORRECTIONS``,
    ``SIGMA_LOG_CHL``, and ``PERSISTENCE_TAU_DAYS`` together. Anything
    else in ``config.py`` is metadata (zone bounds, BBOX) that doesn't
    influence the prediction; we deliberately exclude it so trivial
    config-comment edits don't churn the hash.
    """
    from viz_predict import config

    payload = json.dumps(
        {
            "drivers":      {k: asdict(v) for k, v in config.DRIVER_COEFFS.items()},
            "secchi":       {k: asdict(v) for k, v in config.SECCHI_COEFFS.items()},
            "turbidity":    {k: asdict(v) for k, v in config.TURBIDITY_CORRECTIONS.items()},
            "sigma_log_chl": dict(config.SIGMA_LOG_CHL),
            "persistence":  dict(config.PERSISTENCE_TAU_DAYS),
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def _safe_float(v):
    """Convert to JSON-safe float; NaN/inf -> None (serialised as null).

    Without this every ``np.nan`` cell turns into the string ``"NaN"``
    in the gzip and the readers downstream silently drop or crash on
    those rows. Round-tripping through ``None`` is honest and makes
    NaN cells easy to count when scoring.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def write_snapshot(grid_lat, grid_lng, predict_result, run_at: datetime | None = None) -> Path:
    """Append per-cell records to today's gzip JSONL.

    ``predict_result`` is the dict returned by ``viz_predict.predict_all()``.
    Must contain ``viz_p10_ft``, ``viz_p50_ft``, ``viz_p90_ft``,
    ``quality``, ``zone``, and ``drivers``. ``grid_lat`` / ``grid_lng``
    are the 1-D arrays that were passed into ``predict_all`` — the
    record indices line up with them.

    Returns the output path so callers can log it.
    """
    if run_at is None:
        run_at = datetime.now(timezone.utc)

    out_path = ARCHIVE_ROOT / run_at.strftime("%Y/%m/%d.jsonl.gz")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    coeff_h = coefficient_hash()
    drivers = predict_result.get("drivers", {}) or {}
    run_iso = run_at.isoformat(timespec="seconds").replace("+00:00", "Z")

    grid_lat = np.asarray(grid_lat).reshape(-1)
    grid_lng = np.asarray(grid_lng).reshape(-1)
    n = grid_lat.size

    p50 = np.asarray(predict_result["viz_p50_ft"]).reshape(-1)
    p10 = np.asarray(predict_result["viz_p10_ft"]).reshape(-1)
    p90 = np.asarray(predict_result["viz_p90_ft"]).reshape(-1)
    quality = np.asarray(predict_result["quality"]).reshape(-1)
    zone = np.asarray(predict_result["zone"]).reshape(-1)

    # Pre-flatten driver arrays once instead of indexing per cell.
    flat_drivers = {k: np.asarray(v).reshape(-1) for k, v in drivers.items()}

    n_written = 0
    n_skipped_nan = 0
    with gzip.open(out_path, "at", encoding="utf-8") as f:
        for i in range(n):
            v50 = _safe_float(p50[i])
            # Skip cells where the model didn't produce a prediction at
            # all — they're not useful for scoring and bloat the file.
            if v50 is None:
                n_skipped_nan += 1
                continue
            row = {
                "run_at":     run_iso,
                "lat":        float(grid_lat[i]),
                "lng":        float(grid_lng[i]),
                "viz_p50_ft": v50,
                "viz_p10_ft": _safe_float(p10[i]),
                "viz_p90_ft": _safe_float(p90[i]),
                "quality":    str(quality[i]),
                "zone":       str(zone[i]),
                "drivers":    {k: _safe_float(arr[i]) for k, arr in flat_drivers.items()},
                "coeff_hash": coeff_h,
            }
            f.write(json.dumps(row) + "\n")
            n_written += 1

    print(
        f"  archive: {n_written} cells -> {out_path.relative_to(ARCHIVE_ROOT.parent.parent)} "
        f"(coeff_hash={coeff_h}, skipped {n_skipped_nan} NaN cells)"
    )
    return out_path
