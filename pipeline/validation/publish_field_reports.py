"""Publish a trimmed, frontend-facing slice of the ground-truth observations.

The Field Reports map layer (src/components/FieldReportsLayer.jsx) renders
recent real observations as pins. This writes a small, region-filtered JSON
array (last RECENT_DAYS, only the fields the UI needs) so the browser never
downloads the full git-versioned observations.jsonl.

Run: python -m pipeline.validation.publish_field_reports   (after ingest)
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
OBS_PATH = HERE / "data" / "observations.jsonl"
ROOT = HERE.parent.parent

RECENT_DAYS = 14
MAX_POINTS = 600  # cap the published payload size


def _kind(o: dict) -> str:
    src = (o.get("source") or "").lower()
    if "buoy" in src:
        return "buoy"
    if "cencoos" in src or "rcca" in src:
        return "turbidity"
    return "dive_report"


def _what_value(o: dict):
    """The observation's headline measurement for the pin + popup."""
    if o.get("observed_secchi_ft") is not None:
        return "Visibility", round(float(o["observed_secchi_ft"]), 1), "ft"
    if o.get("observed_swell_ft") is not None:
        return "Swell", round(float(o["observed_swell_ft"]), 1), "ft"
    if o.get("observed_sst_f") is not None:
        return "Water temp", round(float(o["observed_sst_f"]), 1), "°F"
    return "Observation", None, None


def collect(region_bbox: dict, recent_days: int) -> list[dict]:
    if not OBS_PATH.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
    out: list[dict] = []
    with OBS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            lat, lng, ts = o.get("lat"), o.get("lng"), o.get("timestamp_utc")
            if lat is None or lng is None or not ts:
                continue
            try:
                when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if when < cutoff:
                continue
            b = region_bbox
            if not (b["lat_min"] <= lat <= b["lat_max"] and b["lng_min"] <= lng <= b["lng_max"]):
                continue
            what, value, unit = _what_value(o)
            if value is None:
                continue  # only publish points carrying a real measurement
            out.append({
                "id": o.get("obs_id"),
                "lat": round(float(lat), 4),
                "lng": round(float(lng), 4),
                "kind": _kind(o),
                "spot": o.get("spot_name"),
                "what": what,
                "value": value,
                "unit": unit,
                "when": ts,
                "source": o.get("source"),
            })
    out.sort(key=lambda r: r["when"], reverse=True)
    return out[:MAX_POINTS]


def main() -> None:
    region = active_region()
    points = collect(region.bbox, RECENT_DAYS)
    out_path = region.data_output_dir(ROOT) / "observations_recent.json"
    out_path.write_text(json.dumps(points), encoding="utf-8")
    print(f"field-reports: published {len(points)} recent observations to {out_path}")


if __name__ == "__main__":
    main()
