"""Hindcast scoring for SST predictions.

Joins observations.jsonl rows with ``observed_sst_f`` set against
each day's SST archive snapshot, computes per-cell residuals, and
aggregates by zone AND by saved-spot. Same loop pattern as ``score.py``
(visibility); the two writers run in the same workflow but output to
distinct file pairs so each watchdog reads its own signal cleanly.

Dual aggregation
----------------
Per-zone bias is the existing pattern: 9 zones (3 lat × 3 dist), each
zone gets one bias / RMSE / calibration / r row. That's the right
granularity for "the sat-vs-buoy bias regime in the SoCal Bight is
+0.4 °F" but too coarse for "La Jolla Cove specifically tends to be
1.5 °F cooler than its bbox cell".

So we ALSO aggregate per saved-spot. The watchdog R5 rule reads from
that aggregation and suggests a per-spot α delta when residuals
accumulate.

Inputs (all already produced by existing pipeline pieces):
  observations.jsonl              — buoy + dive log obs (existing)
  archive/{YYYY}/{MM}/{DD}.jsonl.gz — per-cell predictions (existing)

Outputs:
  sst_residuals.jsonl              — one row per scored obs
  sst_per_zone_metrics.json        — 9 zones × {n, rmse, bias, mae, cal, r}
  sst_per_spot_metrics.json        — one row per saved-spot ID
"""
from __future__ import annotations

import gzip
import json
import math
import pathlib
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import numpy as np


# Make ``viz_predict`` zone helpers + sst_predict spot list importable.
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


HERE = pathlib.Path(__file__).resolve().parent
DATA_DIR        = HERE / "data"
ARCHIVE_ROOT    = DATA_DIR / "archive"
OBS_PATH        = DATA_DIR / "observations.jsonl"
RESIDUALS_PATH  = DATA_DIR / "sst_residuals.jsonl"
ZONE_METRICS    = DATA_DIR / "sst_per_zone_metrics.json"
SPOT_METRICS    = DATA_DIR / "sst_per_spot_metrics.json"

LOOKBACK_DAYS = 2   # mirror score.py — same archive lifetime constraint


# ---- Saved-spot list ---------------------------------------------------
# Lifted from src/lib/mapData.js. Hand-keep in sync; a unit test in
# pipeline/tests/ enforces the two arrays match exactly. Per-spot
# scoring rolls every observation within MAX_DIST_KM of the spot
# centroid into that spot's bin (same threshold as the cell-nearest
# match used by score.py — observations farther than 25 km from the
# nearest spot are aggregated into "other" only).
SAVED_SPOTS: list[dict] = [
    {"id": "monterey",  "name": "Monterey",       "lat": 36.62, "lng": -121.92},
    {"id": "morro",     "name": "Morro Bay",      "lat": 35.36, "lng": -120.88},
    {"id": "pt-concep", "name": "Pt. Conception", "lat": 34.45, "lng": -120.47},
    {"id": "santabarb", "name": "Santa Barbara",  "lat": 34.40, "lng": -119.70},
    {"id": "santacruz", "name": "Santa Cruz I.",  "lat": 34.05, "lng": -119.75},
    {"id": "malibu",    "name": "Malibu",         "lat": 34.02, "lng": -118.78},
    {"id": "catalina",  "name": "Catalina",       "lat": 33.39, "lng": -118.45},
    {"id": "lajolla",   "name": "La Jolla",       "lat": 32.85, "lng": -117.28},
    {"id": "sandiego",  "name": "San Diego",      "lat": 32.70, "lng": -117.18},
    {"id": "coronados", "name": "Coronados",      "lat": 32.40, "lng": -117.27},
]

SPOT_MATCH_KM = 12.0   # tighter than zone-cell match (25 km) — per-spot
                       # bias only meaningful when the obs is plausibly
                       # AT that spot, not "in the same zone".


# ---- Load helpers (mirror score.py) ------------------------------------

def _load_archive_for_date(d: date) -> list[dict] | None:
    p = ARCHIVE_ROOT / f"{d.year:04d}/{d.month:02d}/{d.day:02d}.jsonl.gz"
    if not p.exists():
        return None
    rows: list[dict] = []
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
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _nearest_cell(rows: list[dict], lat: float, lng: float, max_km: float = 25.0) -> dict | None:
    best, best_d = None, float("inf")
    for r in rows:
        d = _haversine_km(lat, lng, r["lat"], r["lng"])
        if d < best_d:
            best, best_d = r, d
    if best is None or best_d > max_km:
        return None
    return best


def _nearest_spot(lat: float, lng: float, max_km: float = SPOT_MATCH_KM) -> str | None:
    best, best_d = None, float("inf")
    for s in SAVED_SPOTS:
        d = _haversine_km(lat, lng, s["lat"], s["lng"])
        if d < best_d:
            best, best_d = s, d
    if best is None or best_d > max_km:
        return None
    return best["id"]


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


# ---- SST residual scoring ----------------------------------------------

def _to_celsius(value: float, unit: str | None) -> float:
    """observed_sst_f is the canonical column but ingest scrapers
    sometimes set observed_sst_c (CDIP / NDBC are SI). Tolerate both."""
    if unit == "C" or unit == "c":
        return float(value)
    return (float(value) - 32.0) * 5.0 / 9.0


def score_all_observations() -> list[dict]:
    archive_by_date: dict[date, list[dict] | None] = {}
    residuals: list[dict] = []

    for o in _iter_recent_observations(LOOKBACK_DAYS):
        # Pull whichever SST field is present; observation schemas
        # differ across ingest sources.
        sst_f = o.get("observed_sst_f")
        sst_c = o.get("observed_sst_c")
        if sst_f is None and sst_c is None:
            continue
        if sst_c is not None:
            observed_c = float(sst_c)
        else:
            observed_c = _to_celsius(sst_f, "F")

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

        # The visibility archive carries `sst_today_c` as a driver.
        # That's what we score against — it's the model's sample of
        # today's SST at this cell. When sst_predict is wired (Phase E),
        # it'll add `sst_p50_c` etc. We score whichever is available,
        # preferring the prediction over the raw input.
        predicted_c = (
            cell.get("sst_p50_c")
            if cell.get("sst_p50_c") is not None
            else cell.get("sst_today_c")
        )
        if predicted_c is None:
            continue
        residual_c = float(predicted_c) - float(observed_c)

        residuals.append({
            "obs_id":            o["obs_id"],
            "scored_at":         datetime.now(timezone.utc)
                                     .isoformat(timespec="seconds")
                                     .replace("+00:00", "Z"),
            "predicted_c":       round(float(predicted_c), 2),
            "observed_c":        round(float(observed_c), 2),
            "residual_c":        round(residual_c, 2),
            "residual_f":        round(residual_c * 9 / 5, 2),
            "zone":              cell.get("zone"),
            "spot_id":           _nearest_spot(float(o["lat"]), float(o["lng"])),
            "lat":               float(o["lat"]),
            "lng":               float(o["lng"]),
            "source":            o.get("source"),
            "source_confidence": float(o.get("source_confidence", 0.5)),
            "coeff_hash":        cell.get("coeff_hash"),
        })

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with RESIDUALS_PATH.open("w", encoding="utf-8") as f:
        for r in residuals:
            f.write(json.dumps(r) + "\n")
    print(f"sst_score: wrote {len(residuals)} residuals to {RESIDUALS_PATH}")
    return residuals


# ---- Aggregation -------------------------------------------------------

def _agg_metrics(rows: list[dict]) -> dict:
    """Same metric set as score.py:per_zone_metrics, in °F units."""
    if not rows:
        return {"n": 0}
    weights = np.array([float(r.get("source_confidence", 0.5)) for r in rows])
    residual_arr_f = np.array([float(r["residual_f"]) for r in rows])

    wmean_sq = float(np.average(residual_arr_f ** 2, weights=weights))
    rmse = float(math.sqrt(max(0.0, wmean_sq)))
    bias = float(np.average(residual_arr_f, weights=weights))
    mae  = float(np.average(np.abs(residual_arr_f), weights=weights))

    if len(rows) >= 2:
        preds = np.array([float(r["predicted_c"]) for r in rows])
        obs   = np.array([float(r["observed_c"]) for r in rows])
        r_p = (
            float(np.corrcoef(preds, obs)[0, 1])
            if preds.std() > 0 and obs.std() > 0 else None
        )
    else:
        r_p = None

    return {
        "n":           len(rows),
        "rmse_f":      round(rmse, 2),
        "bias_f":      round(bias, 2),
        "mae_f":       round(mae, 2),
        "pearson_r":   None if r_p is None else round(r_p, 3),
    }


def per_zone_metrics(residuals: list[dict]) -> dict:
    by_zone: dict[str, list[dict]] = defaultdict(list)
    for r in residuals:
        by_zone[r.get("zone") or "unknown"].append(r)
    return {z: _agg_metrics(rows) for z, rows in sorted(by_zone.items()) if rows}


def per_spot_metrics(residuals: list[dict]) -> dict:
    by_spot: dict[str, list[dict]] = defaultdict(list)
    for r in residuals:
        sid = r.get("spot_id")
        if sid:
            by_spot[sid].append(r)
    out: dict = {}
    for spot in SAVED_SPOTS:
        sid = spot["id"]
        rows = by_spot.get(sid, [])
        if not rows:
            out[sid] = {"name": spot["name"], "n": 0}
            continue
        m = _agg_metrics(rows)
        m["name"] = spot["name"]
        m["lat"] = spot["lat"]
        m["lng"] = spot["lng"]
        # Suggested per-spot α delta (in °F). The watchdog R5 picks this
        # up; humans review + commit. Bias is "predicted − observed", so
        # to correct we SUBTRACT bias from predictions ⇒ α_delta = -bias.
        m["suggested_alpha_delta_f"] = round(-m["bias_f"], 2)
        out[sid] = m
    return out


def write_metrics(zone_metrics: dict, spot_metrics: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    ZONE_METRICS.write_text(json.dumps({
        "computed_at": now,
        "lookback_days": LOOKBACK_DAYS,
        "zones": zone_metrics,
    }, indent=2))
    SPOT_METRICS.write_text(json.dumps({
        "computed_at": now,
        "lookback_days": LOOKBACK_DAYS,
        "spots": spot_metrics,
    }, indent=2))
    print(f"sst_score: wrote per-zone → {ZONE_METRICS}")
    print(f"sst_score: wrote per-spot → {SPOT_METRICS}")
    for zone, m in zone_metrics.items():
        if m.get("n"):
            print(f"  zone {zone:24s}  n={m['n']:3d}  rmse={m['rmse_f']:5.2f} °F  "
                  f"bias={m['bias_f']:+5.2f} °F  r={m['pearson_r']!s}")
    for sid, m in spot_metrics.items():
        if m.get("n"):
            print(f"  spot {sid:12s}  n={m['n']:3d}  rmse={m['rmse_f']:5.2f} °F  "
                  f"bias={m['bias_f']:+5.2f} °F  Δα={m['suggested_alpha_delta_f']:+.2f}")


def main() -> int:
    residuals = score_all_observations()
    zone = per_zone_metrics(residuals)
    spot = per_spot_metrics(residuals)
    write_metrics(zone, spot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
