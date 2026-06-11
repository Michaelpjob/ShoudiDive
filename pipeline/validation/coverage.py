"""Ground-truth COVERAGE — how much real observation backs the model, per region.

"Coverage is the currency": the confidence dots start at an honest floor
(viz/swell = Modeled, in src/lib/confidence.js) and can only be UPGRADED
where enough recent, in-band ground truth actually exists. This module
computes that signal from the persisted observations + the latest per-zone
scoring metrics and writes a compact `coverage` block into the active
region's manifest.json, which confidence.js reads.

Nothing is hand-set: with sparse or poorly-calibrated data the tier_delta is
0, so the dot stays at its honest floor. The bump is *earned*, never assumed.

Run: python -m pipeline.validation.coverage   (after validation.score)
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone

try:
    from pipeline.regions import active_region
except ModuleNotFoundError:  # when run with pipeline/ as the working dir
    from regions import active_region


HERE = pathlib.Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
OBS_PATH = DATA_DIR / "observations.jsonl"
METRICS_PATH = DATA_DIR / "per_zone_metrics.json"
ROOT = HERE.parent.parent  # .../ShoudiDive

# What it takes to EARN a tier above the honest floor. Deliberately strict:
# visibility ground truth is sparse + subjective, so the bar to call the viz
# model "Observed" instead of "Modeled" is real coverage AND a real fit.
COVERAGE_WINDOW_DAYS = 30      # recent window for counting observations
VIZ_UPGRADE_MIN_OBS = 20       # >= this many recent scoreable obs in-region
VIZ_UPGRADE_MIN_CAL = 0.50     # >= 50% of them inside the model's p10-p90
VIZ_UPGRADE_MAX_RMSE = 6.0     # RMSE under 6 ft
VIZ_MAX_TIER_DELTA = 1         # viz can reach "Observed", never "Validated"
                               # (dive-report visibility is inherently noisy)


# ---- pure helpers (unit-tested) --------------------------------------

def viz_rollup(zones: dict | None) -> dict:
    """Collapse a per_zone_metrics ``zones`` dict to one region-level viz
    signal, weighting calibration + RMSE by each zone's observation count."""
    total_n = 0
    cal_w = 0.0
    rmse_w = 0.0
    for z in (zones or {}).values():
        n = int(z.get("n") or 0)
        if n <= 0:
            continue
        total_n += n
        if z.get("calibration_pct") is not None:
            cal_w += float(z["calibration_pct"]) * n
        if z.get("rmse_ft") is not None:
            rmse_w += float(z["rmse_ft"]) * n
    if total_n == 0:
        return {"n": 0, "calibration_pct": None, "rmse_ft": None}
    return {
        "n": total_n,
        "calibration_pct": round(cal_w / total_n, 3),
        "rmse_ft": round(rmse_w / total_n, 2),
    }


def earned_tier_delta(n_recent: int, calibration_pct, rmse_ft) -> int:
    """A viz tier bump is earned only with enough recent obs AND a real fit."""
    if (
        n_recent >= VIZ_UPGRADE_MIN_OBS
        and calibration_pct is not None and calibration_pct >= VIZ_UPGRADE_MIN_CAL
        and rmse_ft is not None and rmse_ft <= VIZ_UPGRADE_MAX_RMSE
    ):
        return VIZ_MAX_TIER_DELTA
    return 0


# ---- IO + assembly ---------------------------------------------------

def _recent_scoreable_obs_in_region(window_days: int) -> int:
    if not OBS_PATH.exists():
        return 0
    b = active_region().bbox
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    n = 0
    with OBS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("observed_secchi_ft") is None:
                continue
            ts = o.get("timestamp_utc")
            lat, lng = o.get("lat"), o.get("lng")
            if not ts or lat is None or lng is None:
                continue
            try:
                when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if when < cutoff:
                continue
            if (b["lat_min"] <= lat <= b["lat_max"]
                    and b["lng_min"] <= lng <= b["lng_max"]):
                n += 1
    return n


def _load_zones() -> dict:
    if not METRICS_PATH.exists():
        return {}
    try:
        return (json.loads(METRICS_PATH.read_text(encoding="utf-8")) or {}).get("zones") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def compute_coverage() -> dict:
    n_recent = _recent_scoreable_obs_in_region(COVERAGE_WINDOW_DAYS)
    roll = viz_rollup(_load_zones())
    cal, rmse = roll["calibration_pct"], roll["rmse_ft"]
    return {
        "viz": {
            "n_recent": n_recent,
            "window_days": COVERAGE_WINDOW_DAYS,
            "scored_n": roll["n"],
            "calibration_pct": cal,
            "rmse_ft": rmse,
            "tier_delta": earned_tier_delta(n_recent, cal, rmse),
            "as_of": datetime.now(timezone.utc)
                .isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
    }


def _manifest_path() -> pathlib.Path:
    return active_region().data_output_dir(ROOT) / "manifest.json"


def main() -> None:
    coverage = compute_coverage()
    v = coverage["viz"]
    print(
        f"coverage: viz n_recent={v['n_recent']} scored_n={v['scored_n']} "
        f"cal={v['calibration_pct']} rmse={v['rmse_ft']} -> tier_delta={v['tier_delta']}"
    )
    mp = _manifest_path()
    if not mp.exists():
        print(f"coverage: manifest not found at {mp}, skipping injection")
        return
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["coverage"] = coverage
    mp.write_text(json.dumps(manifest, indent=2))
    print(f"coverage: wrote manifest.coverage to {mp}")


if __name__ == "__main__":
    main()
