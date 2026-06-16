"""Storm-strip kelp risk indicator — PR-K5-3 of the kelp roadmap.

Reads the 5-day swell summary that fetch_swell_5day.py emits and
writes a small `kelp-storm-risk.json` flag file. When the bbox-wide
peak Hs (max_hs_ft over any bucket in the next 5 days) exceeds
8.0 ft, the file marks a storm-strip risk window with the peak day +
bucket + Hs.

8 ft (~2.44 m) is a conservative canopy-detachment threshold derived
from CDFW reports on storm impacts to giant kelp; sustained Hs above
this number routinely strips canopy that takes 5–7 days to regrow.
The frontend (`KelpPopup`) reads this file and shows a yellow banner
during the active window.

v1 simplification: this is a *region-wide* risk indicator, not
per-bed. Per-bed precision requires sampling the swell hourly PNG at
each bed centroid — deferred to a v2 enhancement. Most CA kelp beds
sit within 50 km of each other and experience similar wave
conditions, so the global flag is reasonable for first pass.

Output: `<region>/kelp-storm-risk.json` (or `kelp-storm-risk.json` at
the top level for CA — matches fetch_kelp.py's output convention).

Run on demand or as part of the daily refresh (after
fetch_swell_5day.py):
  python pipeline/check_kelp_storm_risk.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

# Canopy-detachment threshold. Reviewed against CDFW + Reed et al.
# (2008) field observations on giant kelp storm response. 8 ft is
# the lower bound of "regularly strips canopy"; sustained 10 ft+
# events cause whole-bed detachment.
STORM_HS_FT_THRESHOLD = 8.0

# How many days the warning stays active AFTER the peak event. CDFW
# canopy-stripping studies cite 5–7 days for noticeable regrowth.
WARNING_TAIL_DAYS = 7

ROOT = Path(__file__).resolve().parents[1]
REGION_DIR = active_region().data_output_dir(ROOT)
SWELL_SUMMARY = REGION_DIR / "swell" / "summary.json"
OUT_PATH = REGION_DIR / "kelp-storm-risk.json"


def find_peak_event(summary: dict) -> dict | None:
    """Scan all (day, bucket) pairs in the swell summary, return the
    bucket with the single highest max_hs_ft. None if no bucket
    exceeds the threshold.

    Returns shape: { day, date, bucket, max_hs_ft, mean_hs_ft,
                     mean_tp_s, mean_dp_deg }
    """
    best = None
    for day in summary.get("days", []) or []:
        date_str = day.get("date")
        day_n = day.get("day")
        for bucket in day.get("buckets", []) or []:
            max_hs = bucket.get("max_hs_ft")
            if not isinstance(max_hs, (int, float)):
                continue
            if max_hs < STORM_HS_FT_THRESHOLD:
                continue
            if best is None or max_hs > best["max_hs_ft"]:
                best = {
                    "day": day_n,
                    "date": date_str,
                    "bucket": bucket.get("bucket"),
                    "max_hs_ft": max_hs,
                    "mean_hs_ft": bucket.get("mean_hs_ft"),
                    "mean_tp_s": bucket.get("mean_tp_s"),
                    "mean_dp_deg": bucket.get("mean_dp_deg"),
                }
    return best


def main() -> None:
    if not SWELL_SUMMARY.exists():
        # Swell fetcher hasn't run yet or this region doesn't have
        # swell data. Write an empty/null risk record so the frontend
        # can distinguish "no risk" from "data unavailable."
        out = {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "available": False,
            "reason": f"swell summary not found at {SWELL_SUMMARY.relative_to(ROOT)}",
            "active": False,
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
        print(f"wrote {OUT_PATH.name} (swell summary unavailable)")
        return

    summary = json.loads(SWELL_SUMMARY.read_text())
    peak = find_peak_event(summary)

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "available": True,
        "threshold_hs_ft": STORM_HS_FT_THRESHOLD,
        "warning_tail_days": WARNING_TAIL_DAYS,
        "swell_cycle": summary.get("gfswave_cycle"),
        "anchor_date": summary.get("anchor_date"),
        "active": peak is not None,
        "peak": peak,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    if peak:
        print(
            f"wrote {OUT_PATH.name}: STORM-STRIP RISK active, "
            f"peak {peak['max_hs_ft']} ft on {peak['date']} "
            f"({peak['bucket']})"
        )
    else:
        print(f"wrote {OUT_PATH.name}: no storm-strip risk in 5-day forecast")


if __name__ == "__main__":
    main()
