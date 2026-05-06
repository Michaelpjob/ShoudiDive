"""Local freshness gate for the data artifacts about to be deployed.

This differs from check_published.py:

  * check_published.py probes shouldidive.com after deploy.
  * this script checks public/data/manifest.json in the current checkout
    before deploy/commit.

It writes pipeline/validation/data/freshness_health.json and exits non-zero
when any selected layer misses its freshness or completeness budget.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import requests


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public" / "data"
MANIFEST = DATA / "manifest.json"
OUT_PATH = ROOT / "pipeline" / "validation" / "data" / "freshness_health.json"
MUR_SCI_POINT_URL = (
    "https://coastwatch.pfeg.noaa.gov/erddap/griddap/"
    "jplMURSST41.csv?analysed_sst[(last)][(32.93)][(-118.49)]"
)

TOP_LEVEL_MAX_HOURS = 36

LAYER_DATE_MAX_DAYS = {
    "sst": 4,
    "chl": 7,
    "kd490": 14,
    "wind": 1,
    "viz": 2,
    "wave": 2,
    "precip": 3,
}

SUMMARY_MAX_HOURS = {
    "sst7d": 96,
    "wind5d": 8,
    "swell5d": 8,
    "current5d": 8,
}

SUMMARY_MIN_DAYS = {
    "sst7d": 3,
    "wind5d": 5,
    "swell5d": 5,
    "current5d": 5,
}

SUMMARY_MIN_BUCKETS_PER_DAY = {
    "wind5d": 3,
    "swell5d": 3,
    "current5d": 3,
}

SST_SOURCE_LAG_MAX_DAYS = 1.25


@dataclass
class Finding:
    severity: str
    code: str
    title: str
    layer: str | None = None
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "title": self.title,
            "layer": self.layer,
            "detail": self.detail,
        }


def parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_data_path(path_or_url: str | None) -> Path | None:
    if not path_or_url or path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return None
    return DATA / path_or_url.lstrip("/").removeprefix("data/")


def latest_date_from_window(win: dict) -> datetime | None:
    dates = win.get("dates")
    if isinstance(dates, list) and dates:
        return parse_iso(str(dates[-1]))
    valid_at = win.get("valid_at")
    return parse_iso(valid_at) if valid_at else None


def check_top_level(manifest: dict, now: datetime) -> list[Finding]:
    raw = manifest.get("generated_at")
    when = parse_iso(raw)
    if when is None:
        return [Finding(
            "high",
            "manifest_generated_at_missing",
            "Manifest has no parseable generated_at",
            detail=f"raw={raw!r}",
        )]
    age_h = (now - when).total_seconds() / 3600
    if age_h > TOP_LEVEL_MAX_HOURS:
        return [Finding(
            "high",
            "manifest_generated_at_stale",
            f"Manifest full-refresh timestamp is {age_h:.1f}h old",
            detail=f"threshold={TOP_LEVEL_MAX_HOURS}h generated_at={raw}",
        )]
    return []


def primary_window(layer_id: str, windows: dict) -> tuple[str, dict] | tuple[None, None]:
    preferred = {
        "sst": ["1d", "2d", "3d"],
        "chl": ["1d", "2d", "3d"],
        "kd490": ["1d", "2d", "3d"],
        "viz": ["now"],
        "wave": ["now"],
        "precip": ["now"],
    }.get(layer_id, ["1d", "now", "2d", "3d"])
    for key in preferred:
        if isinstance(windows.get(key), dict):
            return key, windows[key]
    for key, value in windows.items():
        if isinstance(value, dict):
            return key, value
    return None, None


def check_window_layer(layer_id: str, info: dict, now: datetime) -> list[Finding]:
    findings: list[Finding] = []
    windows = info.get("windows") or {}
    key, win = primary_window(layer_id, windows)
    if not win:
        return [Finding(
            "high",
            "layer_missing_windows",
            f"Layer {layer_id} has no usable windows",
            layer=layer_id,
        )]

    url_fields = [k for k in ("url", "speed_url", "uv_url", "wave_url") if isinstance(win.get(k), str)]
    for field in url_fields:
        p = resolve_data_path(win[field])
        if p is not None and not p.exists():
            findings.append(Finding(
                "high",
                "layer_artifact_missing",
                f"Layer {layer_id} window {key} is missing {field}",
                layer=layer_id,
                detail=str(p.relative_to(ROOT)),
            ))

    max_days = LAYER_DATE_MAX_DAYS.get(layer_id)
    if max_days is not None:
        when = latest_date_from_window(win)
        if when is None:
            findings.append(Finding(
                "medium",
                "layer_latest_date_missing",
                f"Layer {layer_id} has no parseable latest date",
                layer=layer_id,
                detail=f"window={key}",
            ))
        else:
            age_d = (now - when).total_seconds() / 86400
            if age_d > max_days:
                findings.append(Finding(
                    "high",
                    "layer_date_stale",
                    f"Layer {layer_id} is {age_d:.1f}d old",
                    layer=layer_id,
                    detail=f"threshold={max_days}d window={key}",
                ))
    return findings


def load_summary(info: dict) -> tuple[dict | None, str | None]:
    p = resolve_data_path(info.get("summary_url"))
    if p is None:
        return None, "summary_url missing or remote"
    if not p.exists():
        return None, f"{p.relative_to(ROOT)} missing"
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"{p.relative_to(ROOT)} invalid JSON: {exc}"


def check_summary_layer(layer_id: str, info: dict, now: datetime) -> list[Finding]:
    summary, err = load_summary(info)
    if summary is None:
        return [Finding(
            "high",
            "summary_unreadable",
            f"Layer {layer_id} summary is unreadable",
            layer=layer_id,
            detail=err or "",
        )]

    findings: list[Finding] = []
    generated = parse_iso(summary.get("generated_at") or info.get("generated_at"))
    max_h = SUMMARY_MAX_HOURS.get(layer_id)
    if generated is None:
        findings.append(Finding(
            "high",
            "summary_generated_at_missing",
            f"Layer {layer_id} summary has no generated_at",
            layer=layer_id,
        ))
    elif max_h is not None:
        age_h = (now - generated).total_seconds() / 3600
        if age_h > max_h:
            findings.append(Finding(
                "high",
                "summary_generated_at_stale",
                f"Layer {layer_id} summary is {age_h:.1f}h old",
                layer=layer_id,
                detail=f"threshold={max_h}h",
            ))

    days = summary.get("days")
    min_days = SUMMARY_MIN_DAYS.get(layer_id, 1)
    if not isinstance(days, list) or len(days) < min_days:
        findings.append(Finding(
            "high",
            "summary_days_short",
            f"Layer {layer_id} has too few days",
            layer=layer_id,
            detail=f"got={len(days) if isinstance(days, list) else 0} expected>={min_days}",
        ))
        return findings

    min_buckets = SUMMARY_MIN_BUCKETS_PER_DAY.get(layer_id)
    if min_buckets:
        sparse = [
            d for d in days
            if len(d.get("buckets") or []) < min_buckets
        ]
        if sparse:
            findings.append(Finding(
                "high",
                "summary_day_sparse",
                f"Layer {layer_id} has sparse day summaries",
                layer=layer_id,
                detail=", ".join(f"d{d.get('day')} buckets={len(d.get('buckets') or [])}" for d in sparse[:5]),
            ))

    bucket_url_fields = {
        "wind5d": ("uv_url",),
        "swell5d": ("wave_url",),
        "current5d": ("uv_url",),
    }.get(layer_id, ())
    if bucket_url_fields:
        missing = []
        for d in days:
            for b in d.get("buckets") or []:
                for field in bucket_url_fields:
                    p = resolve_data_path(b.get(field))
                    if p is not None and not p.exists():
                        missing.append(str(p.relative_to(ROOT)))
        if missing:
            findings.append(Finding(
                "high",
                "summary_bucket_artifact_missing",
                f"Layer {layer_id} references missing bucket artifact(s)",
                layer=layer_id,
                detail=", ".join(missing[:8]),
            ))

    if layer_id == "sst7d":
        missing = []
        for d in days:
            p = resolve_data_path(d.get("url"))
            if p is not None and not p.exists():
                missing.append(str(p.relative_to(ROOT)))
        if missing:
            findings.append(Finding(
                "high",
                "sst_history_png_missing",
                "SST history references missing PNGs",
                layer=layer_id,
                detail=", ".join(missing[:5]),
            ))
    return findings


def check_sst_source_lag(manifest: dict) -> list[Finding]:
    """Compare published SST date against latest MUR availability.

    The San Clemente Island point is a cheap proxy for the MUR dataset's
    latest time and directly covers the area that triggered this fix.
    """
    sst = (manifest.get("layers") or {}).get("sst") or {}
    win = ((sst.get("windows") or {}).get("1d") or {})
    published = latest_date_from_window(win)
    if published is None:
        return [Finding(
            "medium",
            "sst_latest_date_missing",
            "SST 1d window has no parseable date for source-lag check",
            layer="sst",
        )]

    try:
        r = requests.get(MUR_SCI_POINT_URL, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        return [Finding(
            "medium",
            "sst_source_query_failed",
            "Could not query latest MUR SST source time",
            layer="sst",
            detail=f"{exc.__class__.__name__}: {exc}",
        )]

    lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return [Finding(
            "medium",
            "sst_source_query_unparseable",
            "MUR SST source query returned an unexpected CSV shape",
            layer="sst",
            detail=lines[:3].__repr__(),
        )]
    source_time = parse_iso(lines[2].split(",", 1)[0])
    if source_time is None:
        return [Finding(
            "medium",
            "sst_source_time_unparseable",
            "MUR SST source time was not parseable",
            layer="sst",
            detail=lines[2],
        )]

    lag_days = (source_time.date() - published.date()).days
    if lag_days > SST_SOURCE_LAG_MAX_DAYS:
        return [Finding(
            "high",
            "sst_source_lag",
            f"SST is {lag_days:.1f}d behind latest MUR availability",
            layer="sst",
            detail=f"published={published.date().isoformat()} source={source_time.date().isoformat()} point=San Clemente Island",
        )]
    return []


def selected_layers(raw: str, manifest: dict) -> list[str]:
    if raw == "all":
        return sorted((manifest.get("layers") or {}).keys())
    return [x.strip() for x in raw.split(",") if x.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--layers",
        default="all",
        help="Comma-separated layer ids to check, or all.",
    )
    parser.add_argument(
        "--skip-top-level",
        action="store_true",
        help="Do not check manifest.generated_at. Useful for partial forecast jobs.",
    )
    parser.add_argument(
        "--check-sst-source",
        action="store_true",
        help="Query NOAA MUR at San Clemente Island and fail if published SST lags source availability.",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    findings: list[Finding] = []
    if not MANIFEST.exists():
        findings.append(Finding(
            "high",
            "manifest_missing",
            "public/data/manifest.json is missing",
            detail=str(MANIFEST.relative_to(ROOT)),
        ))
        manifest = {"layers": {}}
    else:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if not args.skip_top_level:
        findings.extend(check_top_level(manifest, now))

    layers = manifest.get("layers") or {}
    for layer_id in selected_layers(args.layers, manifest):
        info = layers.get(layer_id)
        if not isinstance(info, dict):
            findings.append(Finding(
                "high",
                "layer_missing",
                f"Layer {layer_id} missing from manifest",
                layer=layer_id,
            ))
            continue
        if "summary_url" in info:
            findings.extend(check_summary_layer(layer_id, info, now))
        else:
            findings.extend(check_window_layer(layer_id, info, now))

    if args.check_sst_source:
        findings.extend(check_sst_source_lag(manifest))

    out = {
        "computed_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "layers_checked": selected_layers(args.layers, manifest),
        "summary": {
            "total_findings": len(findings),
            "healthy": not findings,
        },
        "findings": [f.as_dict() for f in findings],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    for f in findings:
        layer = f" [{f.layer}]" if f.layer else ""
        print(f"{f.severity.upper():>6} {f.code}{layer}: {f.title}")
        if f.detail:
            print(f"       {f.detail}")
    print(f"wrote {OUT_PATH.relative_to(ROOT)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
