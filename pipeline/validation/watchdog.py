"""Automated review of model accuracy after every refresh-data run.

Reads ``per_zone_metrics.json`` + ``residuals.jsonl`` + ``observations.jsonl``,
runs four rule checks, writes a human-readable markdown summary, and
exits 1 if any rule triggered (so the surrounding workflow can open
or update a rolling GitHub Issue).

Rules — each one is gated on a minimum sample size to avoid false-
positive findings on noisy small-n zones:

  R1  Zone systematic bias (over- or under-prediction)
        threshold: |bias_ft| > 5.0
        min n: 30
        action: bump SECCHI_COEFFS[zone].a or DRIVER_COEFFS[zone]

  R2  Zone interval calibration (p10–p90 too narrow or too wide)
        threshold: calibration_pct outside [0.60, 0.95]
        min n: 30
        action: tweak SIGMA_LOG_CHL[zone]

  R3  Zone correlation (model captures relative differences)
        threshold: pearson_r < 0.30
        min n: 50
        action: structural — model is missing something for this zone

  R4  Data-flow health (scrapers running)
        threshold: <8 obs in the last 24 hours OR a configured source
        is silent for >24h
        action: investigate ingest cron logs

The watchdog never modifies coefficients itself — every finding is a
suggestion for a human to review. The GitHub Issue is the audit trail.
"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta


HERE = pathlib.Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
METRICS_PATH      = DATA_DIR / "per_zone_metrics.json"
RESIDUALS_PATH    = DATA_DIR / "residuals.jsonl"
OBSERVATIONS_PATH = DATA_DIR / "observations.jsonl"
SUMMARY_PATH      = DATA_DIR / "watchdog_summary.md"


# Thresholds — kept here as constants so they're easy to tune as the
# system matures. The current values are conservative; tighten when
# more obs accumulate and the false-positive rate is known.
BIAS_THRESHOLD_FT       = 5.0   # |bias| > this → R1 fires
CAL_LOW                 = 0.60  # < this → interval too narrow
CAL_HIGH                = 0.95  # > this → interval too wide
CORR_LOW                = 0.30  # pearson r < this → R3 fires
MIN_N_BIAS              = 30    # min obs/zone for R1 + R2
MIN_N_CORR              = 50    # min obs/zone for R3 (correlation needs more)

# Data-flow expectations. With CDIP × 6 + NDBC × 6 + Just Get Wet × ~3
# + DiveViz × ~1 = ~16 obs/cron × 24 crons = ~384/day, but dedup and
# weather knock that down. Floor at 50/day = anything below means
# multiple scrapers are silently broken.
EXPECTED_DAILY_OBS_FLOOR = 50

# Sources we expect to ALWAYS be contributing. CDIP buoys are the most
# reliable so a 24h gap from any of them is real signal that something
# upstream broke.
REQUIRED_SOURCES = ["cdip-buoy", "ndbc-buoy"]
REQUIRED_SOURCE_MAX_AGE_HOURS = 24


# ---- Loading ---------------------------------------------------------

def load_metrics() -> dict | None:
    if not METRICS_PATH.exists():
        return None
    try:
        return json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ---- Rules -----------------------------------------------------------

def rule_zone_bias(zones: dict) -> list[dict]:
    """R1: |bias_ft| > 5.0, n >= 30."""
    out = []
    for zone, m in sorted(zones.items()):
        n = int(m.get("n") or 0)
        bias = float(m.get("bias_ft") or 0.0)
        if n < MIN_N_BIAS:
            continue
        if abs(bias) <= BIAS_THRESHOLD_FT:
            continue
        # Suggested coefficient adjustment per the handoff:
        # closing 1 ft of bias ≈ +0.3 of SECCHI_a (rough rule-of-thumb).
        suggested_delta_a = round(-bias * 0.3, 2)
        out.append({
            "rule":     "R1",
            "severity": "high",
            "zone":     zone,
            "n":        n,
            "bias_ft":  bias,
            "rmse_ft":  float(m.get("rmse_ft") or 0.0),
            "title":    f"Zone `{zone}` is biased {bias:+.1f} ft (n={n})",
            "what":     (
                f"Model is systematically "
                f"{'over' if bias > 0 else 'under'}-predicting visibility "
                f"in this zone."
            ),
            "action":   (
                f"Adjust `SECCHI_COEFFS[{zone!r}].a` by `{suggested_delta_a:+.2f}` "
                f"(closes ~{abs(bias) * 0.7:.1f} ft of the {abs(bias):.1f} ft bias). "
                f"File: `pipeline/viz_predict/config.py`."
            ),
        })
    return out


def rule_zone_calibration(zones: dict) -> list[dict]:
    """R2: calibration_pct outside [0.60, 0.95], n >= 30."""
    out = []
    for zone, m in sorted(zones.items()):
        n = int(m.get("n") or 0)
        cal = float(m.get("calibration_pct") or 0.0)
        if n < MIN_N_BIAS:
            continue
        if CAL_LOW <= cal <= CAL_HIGH:
            continue
        too_narrow = cal < CAL_LOW
        out.append({
            "rule":     "R2",
            "severity": "medium",
            "zone":     zone,
            "n":        n,
            "calibration_pct": cal,
            "title":    (
                f"Zone `{zone}` interval is "
                f"{'too narrow' if too_narrow else 'too wide'} "
                f"({cal * 100:.0f}% calibration, target 80%, n={n})"
            ),
            "what":     (
                "Honest p10–p90 should bracket about 80% of observations. "
                + ("Too narrow means the model is over-confident."
                   if too_narrow else
                   "Too wide means the model is under-confident.")
            ),
            "action":   (
                f"Adjust `SIGMA_LOG_CHL[{zone!r}]` "
                f"({'+0.05' if too_narrow else '-0.05'} as a starting nudge). "
                f"File: `pipeline/viz_predict/config.py`."
            ),
        })
    return out


def rule_zone_correlation(zones: dict) -> list[dict]:
    """R3: pearson_r < 0.30, n >= 50."""
    out = []
    for zone, m in sorted(zones.items()):
        n = int(m.get("n") or 0)
        r = m.get("pearson_r")
        if r is None or n < MIN_N_CORR:
            continue
        try:
            r = float(r)
        except (TypeError, ValueError):
            continue
        if r >= CORR_LOW:
            continue
        out.append({
            "rule":     "R3",
            "severity": "structural",
            "zone":     zone,
            "n":        n,
            "pearson_r": r,
            "title":    f"Zone `{zone}` has weak correlation r={r:.2f} (n={n})",
            "what":     (
                "Even after subtracting the bias, the model isn't tracking "
                "relative differences in this zone. That's a structural "
                "miss — possibly a missing driver or an incorrectly-applied "
                "coefficient. Numeric tweaks won't fix this; the model "
                "needs a new term or a different formulation for this zone."
            ),
            "action":   (
                f"Investigate `pipeline/viz_predict/visibility.py` and "
                f"`zones.py` for `{zone}` handling. Consider what additional "
                f"input might separate the good days from the bad days here."
            ),
        })
    return out


def rule_data_flow(observations: list[dict]) -> list[dict]:
    """R4: total obs/24h < floor, OR required-source silent >24h."""
    out = []
    if not observations:
        out.append({
            "rule":     "R4",
            "severity": "high",
            "title":    "Observations file is empty",
            "what":     "No ground truth at all has been ingested. Every scraper is failing.",
            "action":   "Check the most recent `Ingest ground-truth observations` workflow run.",
        })
        return out

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(hours=24)

    recent = []
    for o in observations:
        ts_str = o.get("timestamp_utc")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= recent_cutoff:
            recent.append((ts, o))

    if len(recent) < EXPECTED_DAILY_OBS_FLOOR:
        out.append({
            "rule":     "R4",
            "severity": "high",
            "title":    (
                f"Only {len(recent)} observations in the last 24h "
                f"(floor: {EXPECTED_DAILY_OBS_FLOOR})"
            ),
            "what":     "Multiple scrapers may be silently broken.",
            "action":   "Open the latest hourly ingest workflow run; look for `FAILED` lines per scraper.",
        })

    # Per-required-source freshness check.
    last_seen: dict[str, datetime] = {}
    for ts, o in recent:
        src = o.get("source")
        if not src:
            continue
        if src not in last_seen or ts > last_seen[src]:
            last_seen[src] = ts

    for src in REQUIRED_SOURCES:
        if src not in last_seen:
            out.append({
                "rule":     "R4",
                "severity": "high",
                "title":    f"Required source `{src}` has been silent for >{REQUIRED_SOURCE_MAX_AGE_HOURS}h",
                "what":     f"Expected `{src}` to contribute at least one observation per cron. None seen in the recent window.",
                "action":   f"Inspect `pipeline/validation/ingest/{src.split('-')[0]}.py` and the latest ingest cron's log.",
            })

    return out


# ---- Per-source bias (informational, computed from residuals) -------

def per_source_summary(residuals: list[dict]) -> list[dict]:
    """Compute mean residual by source for the audit log. Not gated —
    informational only, surfaces in the markdown but doesn't fail."""
    by_source: dict[str, list[float]] = defaultdict(list)
    for r in residuals:
        src = r.get("source")
        delta = r.get("residual_ft")
        if src and isinstance(delta, (int, float)):
            by_source[src].append(float(delta))
    out = []
    for src, vals in sorted(by_source.items()):
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        out.append({"source": src, "n": len(vals), "bias_ft": round(avg, 2)})
    return out


# ---- Markdown rendering ---------------------------------------------

def render(findings: list[dict], zones: dict, source_summary: list[dict]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z")
    lines: list[str] = []
    lines.append(f"# Validation watchdog — {now}")
    lines.append("")
    if not findings:
        lines.append("✅ **No findings.** All gated rules are within thresholds; "
                     "this issue auto-closes when this report is published.")
        lines.append("")
    else:
        lines.append(
            f"**{len(findings)} finding(s)** flagged across the gated rules. "
            "Each finding includes a suggested action; the watchdog never "
            "modifies coefficients itself."
        )
        lines.append("")
        lines.append("## Findings")
        lines.append("")
        for i, f in enumerate(findings, 1):
            badge = {
                "high":       "🔴",
                "medium":     "⚠️",
                "structural": "🧩",
            }.get(f.get("severity", ""), "•")
            lines.append(f"### {badge} {i}. {f['title']}")
            lines.append("")
            lines.append(f["what"])
            lines.append("")
            lines.append(f"**Suggested action:** {f['action']}")
            lines.append("")

    lines.append("## Per-zone metrics")
    lines.append("")
    if zones:
        lines.append("| Zone | n | RMSE (ft) | Bias (ft) | Calibration | Pearson r |")
        lines.append("|---|---|---|---|---|---|")
        for zone, m in sorted(zones.items()):
            n = m.get("n") or 0
            rmse = m.get("rmse_ft")
            bias = m.get("bias_ft")
            cal  = m.get("calibration_pct")
            r    = m.get("pearson_r")
            lines.append(
                f"| `{zone}` | {n} | "
                f"{'—' if rmse is None else f'{rmse:.2f}'} | "
                f"{'—' if bias is None else f'{bias:+.2f}'} | "
                f"{'—' if cal  is None else f'{cal * 100:.0f}%'} | "
                f"{'—' if r    is None else f'{r:.2f}'} |"
            )
    else:
        lines.append("_(no zones with observations yet — first signals expected within the first week of ingest)_")
    lines.append("")

    lines.append("## Per-source bias (informational)")
    lines.append("")
    if source_summary:
        lines.append("| Source | n | Mean residual (predicted − observed) |")
        lines.append("|---|---|---|")
        for s in source_summary:
            lines.append(f"| `{s['source']}` | {s['n']} | {s['bias_ft']:+.2f} ft |")
    else:
        lines.append("_(no scored residuals yet — needs at least one source with `observed_secchi_ft` populated)_")
    lines.append("")

    lines.append("## How to act on this issue")
    lines.append("")
    lines.append(
        "1. Review each finding's suggested action. Edits go in "
        "`pipeline/viz_predict/config.py`."
    )
    lines.append(
        "2. After making a coefficient change, the next refresh-data "
        "run will rebuild `per_zone_metrics.json` and the watchdog "
        "re-runs. If the finding clears, this issue auto-closes."
    )
    lines.append(
        "3. To dismiss a specific finding intentionally (you've decided "
        "the bias is correct behavior), close this issue manually with a "
        "comment. It will reopen if the finding persists in the next run."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*Auto-generated by `pipeline/validation/watchdog.py`. "
        "Threshold definitions live in that file as module constants — "
        "edit there to tighten or relax over time.*"
    )
    return "\n".join(lines)


# ---- Entry point -----------------------------------------------------

def main() -> int:
    metrics = load_metrics()
    if metrics is None:
        # No metrics yet — score.py hasn't run or wrote nothing. Don't
        # emit a finding; this is normal during the first day.
        SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH.write_text(
            "# Validation watchdog\n\n"
            "_(per_zone_metrics.json missing — score.py hasn't produced output yet. "
            "No findings to report.)_\n",
            encoding="utf-8",
        )
        print("watchdog: no metrics file; nothing to report")
        return 0

    zones = (metrics or {}).get("zones", {}) or {}
    residuals = load_jsonl(RESIDUALS_PATH)
    observations = load_jsonl(OBSERVATIONS_PATH)

    findings: list[dict] = []
    findings.extend(rule_zone_bias(zones))
    findings.extend(rule_zone_calibration(zones))
    findings.extend(rule_zone_correlation(zones))
    findings.extend(rule_data_flow(observations))

    source_summary = per_source_summary(residuals)

    body = render(findings, zones, source_summary)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(body, encoding="utf-8")

    if findings:
        print(f"watchdog: {len(findings)} finding(s) -> {SUMMARY_PATH.name}")
        for f in findings:
            print(f"  [{f['severity']:10s}] {f['title']}")
        return 1
    print("watchdog: no findings; all gated rules within thresholds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
