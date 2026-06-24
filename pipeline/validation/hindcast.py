"""Hindcast harness — score retained ground truth against the predictions
we actually published, reconstructed from git history.

WHY THIS EXISTS
---------------
The per-cell prediction archive (archive.py) is gitignored and ephemeral:
each day's snapshot is read off the CI disk during the same run that
produced it, then discarded. So although ingest has retained ~290 clarity
observations over the last two months, only the most recent ~2 days were
ever scored — the predictions we made on every earlier day were thrown
away, leaving that ground truth with nothing to compare against.

But the *published* viz PNGs (viz_p10/p50/p90_ft.png) ARE committed to
main on every refresh. Every historical day's real prediction is therefore
sitting in git history. This harness:

  1. backfill() — walks that history, decodes each day's published
     p10/p50/p90 grids back to feet, and writes them into the (local,
     gitignored) archive in the exact shape the scorer reads. This is the
     "collect the historical predictions" step: it recovers predictions
     that were otherwise lost.
  2. score() — joins every retained clarity observation to its same-day
     reconstructed prediction (nearest grid cell within 25 km) and writes
     data/hindcast_residuals.jsonl: predicted vs observed, per spot.

These are the predictions we genuinely published (a mix of coefficient
versions over time), so the residuals measure REAL-TIME historical skill,
not a current-model refit. That is the honest "how accurate have we
actually been" record; coeff_hash is stamped 'published-historical' so the
provenance stays explicit and a later current-model hindcast can be told
apart from it.

This writes ONLY hindcast_* artifacts. It deliberately does NOT touch the
live loop's residuals.jsonl / per_zone_metrics.json, so the daily scoring
record stays clean and this stays a separate, re-runnable analysis.

Run:
    python -m validation.hindcast               # backfill + score
    python -m validation.hindcast --score-only  # reuse existing archive
    python -m validation.hindcast --since 2026-05-01
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
ARCHIVE_ROOT = DATA_DIR / "archive"
OBS_PATH = DATA_DIR / "observations.jsonl"
HINDCAST_PATH = DATA_DIR / "hindcast_residuals.jsonl"
REPO_ROOT = HERE.parents[1]  # .../<repo>

# Must match fetch_visibility.py's output grid + encoding exactly.
GRID_W, GRID_H = 140, 110
BBOX = {"lat_min": 31.8, "lat_max": 42.0, "lng_min": -128.5, "lng_max": -116.8}
VIZ_RANGE_FT = (0.0, 80.0)
P50 = "public/data/viz_p50_ft.png"
P10 = "public/data/viz_p10_ft.png"
P90 = "public/data/viz_p90_ft.png"
QUALITY_PNG = "public/data/viz_quality.png"
MAX_MATCH_KM = 25.0  # same neighbourhood tolerance as score.py

# Inverse of QUALITY_CODES in fetch_visibility.py.
QUALITY_BY_CODE = {
    1: "OBSERVED_1D", 2: "OBSERVED_3D", 3: "INTERPOLATED",
    4: "PREDICTED_HIGH_CONF", 5: "PREDICTED_MED_CONF",
    6: "PREDICTED_LOW_CONF", 7: "CLIMATOLOGY_ONLY",
}

# Cell-centre grid, identical convention to fetch_visibility.py:
#   lat_axis = linspace(lat_max, lat_min, H)  -> row 0 = north
#   lng_axis = linspace(lng_min, lng_max, W)  -> col 0 = west
_LAT_AXIS = np.linspace(BBOX["lat_max"], BBOX["lat_min"], GRID_H)
_LNG_AXIS = np.linspace(BBOX["lng_min"], BBOX["lng_max"], GRID_W)
_LNG_GRID, _LAT_GRID = np.meshgrid(_LNG_AXIS, _LAT_AXIS)  # both (H, W)


# ---- git + decode ----------------------------------------------------

def _git(*args) -> bytes:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True).stdout


def _decode_linear(data: bytes, lo: float, hi: float):
    """px 0 = NaN; 1..255 linear in [lo, hi]. Mirrors lib/decode.decode_linear_png."""
    if not data:
        return None
    try:
        arr = np.array(Image.open(io.BytesIO(data)))
    except Exception:
        return None
    if arr.ndim == 3:
        arr = arr[..., 0]
    if arr.shape != (GRID_H, GRID_W):
        return None
    out = np.full(arr.shape, np.nan, dtype=np.float32)
    v = arr > 0
    out[v] = lo + ((arr[v].astype(np.float32) - 1) / 254.0) * (hi - lo)
    return out


def _decode_quality(data: bytes):
    if not data:
        return None
    try:
        arr = np.array(Image.open(io.BytesIO(data)))
    except Exception:
        return None
    if arr.ndim == 3:
        arr = arr[..., 0]
    if arr.shape != (GRID_H, GRID_W):
        return None
    return arr  # raw codes 0..7


def _commits_by_date() -> dict[str, str]:
    """Latest commit hash per calendar date that touched the p50 PNG."""
    out = _git("log", "--format=%H %ad", "--date=short", "--", P50).decode("utf-8", "replace")
    by_date: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            # git log is newest-first; keep the first (latest) hash per date.
            by_date.setdefault(parts[1], parts[0])
    return by_date


# ---- 1. backfill the archive from published PNGs ---------------------

def backfill(since: str | None = None) -> int:
    by_date = _commits_by_date()
    dates = sorted(d for d in by_date if not since or d >= since)
    n_days = n_cells = 0
    for d in dates:
        commit = by_date[d]
        p50 = _decode_linear(_git("show", f"{commit}:{P50}"), *VIZ_RANGE_FT)
        if p50 is None:
            print(f"  {d}: no usable p50 PNG, skipping")
            continue
        nan_grid = np.full((GRID_H, GRID_W), np.nan, dtype=np.float32)
        p10 = _decode_linear(_git("show", f"{commit}:{P10}"), *VIZ_RANGE_FT)
        p90 = _decode_linear(_git("show", f"{commit}:{P90}"), *VIZ_RANGE_FT)
        ql = _decode_quality(_git("show", f"{commit}:{QUALITY_PNG}"))
        p10 = nan_grid if p10 is None else p10
        p90 = nan_grid if p90 is None else p90

        run_at = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        run_iso = run_at.isoformat(timespec="seconds").replace("+00:00", "Z")
        out_path = ARCHIVE_ROOT / run_at.strftime("%Y/%m/%d.jsonl.gz")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        p50f, p10f, p90f = p50.reshape(-1), p10.reshape(-1), p90.reshape(-1)
        latf, lngf = _LAT_GRID.reshape(-1), _LNG_GRID.reshape(-1)
        qlf = ql.reshape(-1) if ql is not None else None

        written = 0
        with gzip.open(out_path, "wt", encoding="utf-8") as f:
            for i in range(p50f.size):
                v50 = p50f[i]
                if not np.isfinite(v50):
                    continue
                q = QUALITY_BY_CODE.get(int(qlf[i]), "PUBLISHED") if qlf is not None else "PUBLISHED"
                row = {
                    "run_at": run_iso,
                    "lat": round(float(latf[i]), 4),
                    "lng": round(float(lngf[i]), 4),
                    "viz_p50_ft": round(float(v50), 2),
                    "viz_p10_ft": None if not np.isfinite(p10f[i]) else round(float(p10f[i]), 2),
                    "viz_p90_ft": None if not np.isfinite(p90f[i]) else round(float(p90f[i]), 2),
                    "quality": q,
                    "zone": None,  # bathymetry-derived; not reconstructable from the PNG alone
                    "drivers": {},
                    "coeff_hash": "published-historical",
                }
                f.write(json.dumps(row) + "\n")
                written += 1
        n_days += 1
        n_cells += written
        print(f"  {d}: {written:5d} cells -> {out_path.relative_to(DATA_DIR)}")
    print(f"backfill: {n_days} days, {n_cells} cells")
    return n_days


# ---- 2. score retained clarity obs against the backfilled archive ----

def _load_archive(d) -> dict | None:
    """Return numpy arrays for one date's snapshot, or None."""
    p = ARCHIVE_ROOT / f"{d.year:04d}/{d.month:02d}/{d.day:02d}.jsonl.gz"
    if not p.exists():
        return None
    lat, lng, p50, p10, p90, qual = [], [], [], [], [], []
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            lat.append(r["lat"]); lng.append(r["lng"])
            p50.append(r.get("viz_p50_ft"))
            p10.append(r.get("viz_p10_ft") if r.get("viz_p10_ft") is not None else np.nan)
            p90.append(r.get("viz_p90_ft") if r.get("viz_p90_ft") is not None else np.nan)
            qual.append(r.get("quality"))
    if not lat:
        return None
    return {
        "lat": np.array(lat), "lng": np.array(lng),
        "p50": np.array(p50, dtype=float), "p10": np.array(p10, dtype=float),
        "p90": np.array(p90, dtype=float), "qual": qual,
    }


def _nearest(arc: dict, lat: float, lng: float):
    """Vectorised equirectangular nearest cell; returns (idx, km) or (None, None)."""
    clat = np.radians(0.5 * (lat + arc["lat"]))
    dx = np.radians(arc["lng"] - lng) * np.cos(clat)
    dy = np.radians(arc["lat"] - lat)
    km = 6371.0 * np.sqrt(dx * dx + dy * dy)
    i = int(np.argmin(km))
    return (i, float(km[i])) if km[i] <= MAX_MATCH_KM else (None, None)


def _iter_clarity_obs():
    if not OBS_PATH.exists():
        return
    with OBS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("observed_secchi_ft") in (None, "", 0):
                continue
            if not o.get("timestamp_utc"):
                continue
            yield o


def score() -> int:
    cache: dict = {}
    rows = []
    n_obs = n_nodate = n_nomatch = 0
    for o in _iter_clarity_obs():
        n_obs += 1
        try:
            ts = datetime.fromisoformat(o["timestamp_utc"].replace("Z", "+00:00"))
        except ValueError:
            continue
        d = ts.date()
        if d not in cache:
            cache[d] = _load_archive(d)
        arc = cache[d]
        if arc is None:
            n_nodate += 1
            continue
        idx, km = _nearest(arc, float(o["lat"]), float(o["lng"]))
        if idx is None:
            n_nomatch += 1
            continue
        p50 = float(arc["p50"][idx])
        p10 = arc["p10"][idx]
        p90 = arc["p90"][idx]
        observed = float(o["observed_secchi_ft"])
        in_band = bool(np.isfinite(p10) and np.isfinite(p90) and p10 <= observed <= p90)
        rows.append({
            "obs_id": o.get("obs_id"),
            "date": d.isoformat(),
            "spot_name": o.get("spot_name") or "(unknown)",
            "lat": round(float(o["lat"]), 4),
            "lng": round(float(o["lng"]), 4),
            "observed_ft": round(observed, 2),
            "predicted_p50_ft": round(p50, 2),
            "predicted_p10_ft": None if not np.isfinite(p10) else round(float(p10), 2),
            "predicted_p90_ft": None if not np.isfinite(p90) else round(float(p90), 2),
            "residual_ft": round(p50 - observed, 2),
            "in_p10_p90": in_band,
            "match_km": round(km, 1),
            "quality": arc["qual"][idx],
            "source": o.get("source"),
            "source_confidence": float(o.get("source_confidence", 0.5)),
            "coeff_hash": "published-historical",
        })

    rows.sort(key=lambda r: (r["date"], r["spot_name"]))
    with HINDCAST_PATH.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"score: {len(rows)} scored / {n_obs} clarity obs "
          f"({n_nodate} no same-day prediction, {n_nomatch} >25km from any cell)")
    print(f"       -> {HINDCAST_PATH.relative_to(DATA_DIR.parent)}")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Backfill + score the visibility hindcast.")
    ap.add_argument("--since", help="only backfill/score dates >= YYYY-MM-DD")
    ap.add_argument("--score-only", action="store_true",
                    help="skip the PNG backfill; reuse the existing local archive")
    args = ap.parse_args()
    if not args.score_only:
        backfill(args.since)
    score()


if __name__ == "__main__":
    main()
