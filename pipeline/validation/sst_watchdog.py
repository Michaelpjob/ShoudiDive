"""Automated review of SST prediction accuracy after every refresh.

Mirror of validation/watchdog.py for the SST axis. Reads
sst_per_zone_metrics.json + sst_residuals.jsonl, runs four rule checks,
writes a markdown summary, exits 1 when any rule fired (so the
surrounding workflow can open / update a rolling Issue tagged
``sst-watchdog``).

The watchdog is the SELF-ADJUSTMENT mechanism the user asked for:
every finding suggests a concrete coefficient delta in
``pipeline/sst_predict/config.py`` that a human reviews and commits.
The watchdog never auto-edits coefficients itself — same humans-in-
the-loop discipline as the visibility watchdog. v2 adds a
``--auto-apply`` mode for stable-bias zones (R1 firing same-direction
≥7 consecutive days).

Rules:
  R1  Zone systematic bias (over- or under-prediction)
        threshold: |bias_F| > BIAS_THRESHOLD_F (1.5°F)
        min n:     MIN_N_BIAS (30)
        suggests:  config.BIAS_CORRECTION_F[zone] += -bias_F
                   (sign flipped — we want to CORRECT the bias)

  R2  Zone interval calibration (p10–p90 too narrow / too wide)
        threshold: calibration_pct outside [CAL_LOW, CAL_HIGH]
        min n:     MIN_N_BIAS (30)
        suggests:  config.SIGMA_SST_BY_LEAD[zone][0] *= (target/actual)

  R3  Zone correlation (model captures relative differences)
        threshold: pearson_r < CORR_LOW (0.5)
        min n:     MIN_N_CORR (50)
        suggests:  structural — investigate whether forecast.py is
                   missing a driver for this zone (e.g. nearshore
                   upwelling not captured by 9km RTOFS, kelp shading
                   in shallow zones, river-mouth thermal plumes)

  R4  Data-flow health (sources running)
        threshold: <12 SST obs in last 24h OR a satellite source
                   stays red across 3+ consecutive check_published
                   runs
        suggests:  investigate refresh-data.yml or check_feeds.yml

Status: framework. Implementation in phase 4 (after enough residual
signal has accumulated to make rule outputs meaningful).
"""
from __future__ import annotations

import pathlib

# ---- Paths --------------------------------------------------------------

HERE = pathlib.Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

METRICS_PATH      = DATA_DIR / "sst_per_zone_metrics.json"
RESIDUALS_PATH    = DATA_DIR / "sst_residuals.jsonl"
OBSERVATIONS_PATH = DATA_DIR / "observations.jsonl"          # shared with viz watchdog
SUMMARY_PATH      = DATA_DIR / "sst_watchdog_summary.md"


def rule_zone_bias(zones: dict) -> list[dict]:
    """R1: |bias_F| > 1.5°F, n >= 30.

    For each zone where the metric exceeds threshold, suggests
    a delta to BIAS_CORRECTION_F[zone] sized to close the bias
    1:1 (the simplest correction). When the residuals later show
    the correction overshot, R1 fires the OTHER direction at the
    next refresh and the human walks it back. Convergence is
    typically 2-3 iterations.
    """
    raise NotImplementedError(
        "phase-4: literally watchdog.rule_zone_bias with sst units")


def rule_zone_calibration(zones: dict) -> list[dict]:
    """R2: calibration_pct outside [0.60, 0.95], n >= 30.

    For too-narrow intervals: SIGMA_SST_BY_LEAD[zone] *= 1.5
    For too-wide:             SIGMA_SST_BY_LEAD[zone] *= 0.7
    The asymmetry is intentional — too-narrow is more dangerous
    because users see the predicted band as a confidence claim, so
    we widen aggressively and tighten cautiously.
    """
    raise NotImplementedError("phase-4 stub")


def rule_zone_correlation(zones: dict) -> list[dict]:
    """R3: pearson_r < 0.5, n >= 50.

    No automated coefficient suggestion — low correlation is a
    structural signal that needs human investigation. The summary
    points to candidate causes (the zone-specific physics that the
    forecast model might be under-representing).
    """
    raise NotImplementedError("phase-4 stub")


def rule_data_flow(observations: list[dict], feed_health: dict) -> list[dict]:
    """R4: scrapers running + satellite feeds healthy.

    Counts SST obs in observations.jsonl from the last 24h. Cross-
    references with feed_health.json to flag the (rare) case where
    obs are flowing but the satellite feeds for the predictor are
    red — that's a "validation works, prediction broken" state worth
    surfacing prominently.
    """
    raise NotImplementedError("phase-4 stub")


def render_summary_markdown(findings: list[dict]) -> str:
    """Format rule findings into a GitHub Issue body.

    Same template as watchdog.render_summary_markdown. Each finding
    section has:
      - Title (zone + violation)
      - What (user-readable explanation)
      - Action (concrete coefficient change to copy-paste)
      - Confidence (n + how recent + how stable across runs)
    """
    raise NotImplementedError("phase-4 stub")


def main() -> int:
    """Run all four rules; write summary; exit 1 on any finding.

    Workflow integration (phase-4) mirrors the viz watchdog block in
    refresh-data.yml — open / update a rolling Issue tagged
    ``sst-watchdog`` on findings, close it when clear.
    """
    raise NotImplementedError(
        "phase-4: orchestrate rules + render + exit code")


if __name__ == "__main__":
    raise SystemExit(main())
