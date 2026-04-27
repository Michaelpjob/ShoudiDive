"""Hindcast scoring: join observations to predictions, dump metrics.

For every observation in ``data/observations.jsonl`` from the past
``LOOKBACK_DAYS`` days, find the nearest grid cell in the matching
day's archive snapshot, compute the residual against whichever
fields the source actually measured (``observed_secchi_ft`` /
``observed_sst_f`` / ``observed_swell_ft``), and aggregate per zone.

v1 outputs:

* ``data/residuals.jsonl`` — one row per obs (obs_id, predicted,
  observed, residual_ft, in_p10_p90, zone, quality, drivers, source,
  source_confidence, coeff_hash). Versioned in git.
* ``data/per_zone_metrics.json`` — one row per zone (n, rmse, bias,
  mae, calibration_pct, pearson_r). Versioned in git. THIS is the
  signal we read every morning.

Scope: v1 only joins ``observed_secchi_ft`` against the model's
``viz_p50_ft`` (visibility is the model's primary output). SST and
swell residuals from the buoys are recorded but not aggregated yet
— they validate auxiliary inputs (sst_today / swell5d) which use
different prediction paths. Adding their per-zone aggregation is
straightforward when the output of those pipelines is also archived.
"""
from __future__ import annotations

import gzip
import json
import math
import pathlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import numpy as np


HERE = pathlib.Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
ARCHIVE_ROOT = DATA_DIR / "archive"
OBS_PATH = DATA_DIR / "observations.jsonl"
RESIDUALS_PATH = DATA_DIR / "residuals.jsonl"
METRICS_PATH = DATA_DIR / "per_zone_metrics.json"

# How far back we look for observations to score. The archive only
# survives as long as it's on the local CI disk (gitignored), so
# in v1 we effectively only score same-day obs vs same-day predictions.
# Setting LOOKBACK_DAYS=2 covers the case where the cron straddles
# midnight UTC.
LOOKBACK_DAYS = 2


# ---- Archive loading -------------------------------------------------

def _load_archive_for_date(d: date) -> list[dict] | None:
    """Read every cell from the archive snapshot for one date."""
    p = ARCHIVE_ROOT / f"{d.year:04d}/{d.month:02d}/{d.day:02d}.jsonl.gz"
    if not p.exists():
        return None
    rows = []
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows or None


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance for KDTree-fallback nearest-cell lookup.

    Our grid is ~5 km cells, so nearest-cell within 25 km is
    effectively "same neighbourhood" — anything farther is too noisy
    to score against and we skip the observation.
    """
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _nearest_cell(rows: list[dict], lat: float, lng: float, *, max_km: float = 25.0) -> dict | None:
    """O(N) sweep — N is ~15k cells which is fine at scoring cadence.

    Avoids a scipy KDTree dependency for v1. If we ever want to score
    thousands of obs per run, swap this for KDTree built once per day.
    """
    best, best_d = None, float("inf")
    for r in rows:
        d = _haversine_km(lat, lng, r["lat"], r["lng"])
        if d < best_d:
            best, best_d = r, d
    if best is None or best_d > max_km:
        return None
    return best


# ---- Residual computation -------------------------------------------

def _iter_recent_observations(lookback_days: int) -> Iterable[dict]:
    if not OBS_PATH.exists():
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    with OBS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = o.get("timestamp_utc")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < cutoff:
                continue
            yield o


def score_all_observations() -> list[dict]:
    archive_by_date: dict[date, list[dict] | None] = {}
    residuals: list[dict] = []

    for o in _iter_recent_observations(LOOKBACK_DAYS):
        # v1 scope: only score viz_secchi for now. SST + swell from
        # the buoys are recorded for later but not in the loop yet.
        observed_ft = o.get("observed_secchi_ft")
        if observed_ft is None:
            continue
        ts = datetime.fromisoformat(o["timestamp_utc"].replace("Z", "+00:00"))
        d = ts.date()
        if d not in archive_by_date:
            archive_by_date[d] = _load_archive_for_date(d)
        rows = archive_by_date[d]
        if not rows:
            continue
        cell = _nearest_cell(rows, float(o["lat"]), float(o["lng"]))
        if cell is None:
            continue

        p50 = cell.get("viz_p50_ft")
        p10 = cell.get("viz_p10_ft")
        p90 = cell.get("viz_p90_ft")
        if p50 is None:
            continue
        residual = float(p50) - float(observed_ft)
        in_interval = (
            p10 is not None and p90 is not None and
            float(p10) <= float(observed_ft) <= float(p90)
        )
        residuals.append({
            "obs_id":            o["obs_id"],
            "scored_at":         datetime.now(timezone.utc)
                                     .isoformat(timespec="seconds")
                                     .replace("+00:00", "Z"),
            "predicted_p50_ft":  float(p50),
            "predicted_p10_ft":  None if p10 is None else float(p10),
            "predicted_p90_ft":  None if p90 is None else float(p90),
            "observed_ft":       float(observed_ft),
            "residual_ft":       residual,
            "in_p10_p90":        bool(in_interval),
            "zone":              cell.get("zone"),
            "quality":           cell.get("quality"),
            "drivers":           cell.get("drivers"),
            "source":            o.get("source"),
            "source_confidence": float(o.get("source_confidence", 0.5)),
            "coeff_hash":        cell.get("coeff_hash"),
        })

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with RESIDUALS_PATH.open("w", encoding="utf-8") as f:
        for r in residuals:
            f.write(json.dumps(r) + "\n")
    print(f"score: wrote {len(residuals)} residuals to {RESIDUALS_PATH}")
    return residuals


# ---- Aggregation -----------------------------------------------------

def per_zone_metrics(residuals: list[dict]) -> dict:
    by_zone: dict[str, list[dict]] = defaultdict(list)
    for r in residuals:
        z = r.get("zone") or "unknown"
        by_zone[z].append(r)

    out: dict[str, dict] = {}
    for zone, rows in sorted(by_zone.items()):
        if not rows:
            continue
        weights = np.array([float(r.get("source_confidence", 0.5)) for r in rows])
        residual_arr = np.array([float(r["residual_ft"]) for r in rows])
        in_int = np.array([1.0 if r.get("in_p10_p90") else 0.0 for r in rows])

        # weighted RMSE: sqrt(weighted mean of squared residuals)
        wmean_sq = float(np.average(residual_arr ** 2, weights=weights))
        rmse = float(math.sqrt(max(0.0, wmean_sq)))
        bias = float(np.average(residual_arr, weights=weights))
        mae  = float(np.average(np.abs(residual_arr), weights=weights))
        cal  = float(np.average(in_int, weights=weights))

        # Pearson r needs ≥2 obs and non-zero variance.
        if len(rows) >= 2:
            preds = np.array([float(r["predicted_p50_ft"]) for r in rows])
            obs   = np.array([float(r["observed_ft"]) for r in rows])
            if preds.std() > 0 and obs.std() > 0:
                r_p = float(np.corrcoef(preds, obs)[0, 1])
            else:
                r_p = None
        else:
            r_p = None

        out[zone] = {
            "n":               len(rows),
            "rmse_ft":         round(rmse, 2),
            "bias_ft":         round(bias, 2),
            "mae_ft":          round(mae, 2),
            "calibration_pct": round(cal, 3),
            "pearson_r":       None if r_p is None else round(r_p, 3),
        }

    return out


def write_metrics(metrics: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "computed_at":     datetime.now(timezone.utc)
                              .isoformat(timespec="seconds")
                              .replace("+00:00", "Z"),
        "lookback_days":   LOOKBACK_DAYS,
        "zones":           metrics,
    }
    METRICS_PATH.write_text(json.dumps(payload, indent=2))
    print(f"score: wrote per-zone metrics to {METRICS_PATH}")
    for zone, m in metrics.items():
        print(
            f"  {zone:24s}  n={m['n']:3d}  rmse={m['rmse_ft']:5.2f} ft  "
            f"bias={m['bias_ft']:+5.2f} ft  cal={m['calibration_pct']*100:5.1f}%  "
            f"r={m['pearson_r']!s}"
        )


def main():
    residuals = score_all_observations()
    metrics = per_zone_metrics(residuals)
    write_metrics(metrics)


if __name__ == "__main__":
    main()
