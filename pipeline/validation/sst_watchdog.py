"""SST watchdog — same R1 / R3 pattern as watchdog.py, plus a per-spot rule.

Reads ``sst_per_zone_metrics.json`` + ``sst_per_spot_metrics.json``,
runs the rules, writes ``sst_watchdog_summary.md``, exits 1 when any
rule fires so the surrounding workflow can open / update a rolling
GitHub Issue tagged ``sst-watchdog``.

Rules
-----
R1  Zone systematic bias.
      |bias_F| > 1.5 °F and n >= 30 → suggest a delta to
      ``sst_predict.config.BIAS_CORRECTION_F[zone]``. The watchdog
      never edits config; the suggestion is in the issue body and a
      human commits it.

R3  Zone correlation.
      pearson_r < 0.5 and n >= 50 → structural finding. No automated
      coefficient suggestion — low correlation means the model is
      missing a driver for this zone (kelp shading, river plume,
      seamount upwelling, etc).

R5  Per-spot bias.
      Same logic as R1 but evaluated at saved-spot granularity.
      n >= 20 (lower than the zone threshold; per-spot residuals
      accumulate slower because each obs has to be within
      SPOT_MATCH_KM of the spot centroid). Suggests a delta to
      ``SPOT_CORRECTIONS[spot_id].alpha_f``.

Calibration (R2) is deferred until ``sst_predict`` writes p10/p90
into the archive (Phase E). Data-flow (R4) is covered by the existing
``watchdog.py`` — SST obs ride along on the same observations.jsonl
volume gate.

Output
------
sst_watchdog_summary.md — markdown body the workflow uploads as the
GitHub Issue body. Same template as watchdog.render so reviewers see
a consistent format across the two axes.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone


HERE = pathlib.Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ZONE_METRICS_PATH = DATA_DIR / "sst_per_zone_metrics.json"
SPOT_METRICS_PATH = DATA_DIR / "sst_per_spot_metrics.json"
SUMMARY_PATH      = DATA_DIR / "sst_watchdog_summary.md"


# ----- Thresholds (mirror config in pipeline/sst_predict/config.py) ----
# Kept here as plain constants so this module can run without dragging
# in the sst_predict package — keeps the watchdog as a pure scoring
# consumer that doesn't need the predictor's runtime deps.

BIAS_THRESHOLD_F = 1.5    # R1, R5
CORR_LOW         = 0.5    # R3
MIN_N_BIAS_ZONE  = 30
MIN_N_BIAS_SPOT  = 20
MIN_N_CORR       = 50


# ----- Loaders ----------------------------------------------------------

def _load_json(path: pathlib.Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ----- Rules ------------------------------------------------------------

def rule_zone_bias(zones: dict) -> list[dict]:
    """R1: |bias_F| > 1.5, n >= 30 — zone-level systematic bias."""
    out: list[dict] = []
    for zone, m in sorted(zones.items()):
        n = int(m.get("n") or 0)
        bias = m.get("bias_f")
        if n < MIN_N_BIAS_ZONE or bias is None:
            continue
        bias = float(bias)
        if abs(bias) <= BIAS_THRESHOLD_F:
            continue
        # Closing the bias 1:1 — same heuristic the viz watchdog uses.
        # Sign flips because BIAS_CORRECTION_F is added to predictions.
        delta = round(-bias, 2)
        out.append({
            "rule":     "R1",
            "severity": "high",
            "zone":     zone,
            "n":        n,
            "bias_f":   round(bias, 2),
            "rmse_f":   m.get("rmse_f"),
            "title":    f"Zone `{zone}` SST bias is {bias:+.2f} °F (n={n})",
            "what":     (
                f"Predictions in this zone are systematically "
                f"{'over' if bias > 0 else 'under'}-shooting observations "
                f"by ~{abs(bias):.1f} °F."
            ),
            "action":   (
                f"Adjust `BIAS_CORRECTION_F[{zone!r}]` by `{delta:+.2f}`. "
                f"File: `pipeline/sst_predict/config.py`. Re-evaluate after "
                f"the next ~30 obs accumulate."
            ),
        })
    return out


def rule_zone_correlation(zones: dict) -> list[dict]:
    """R3: pearson_r < 0.5, n >= 50."""
    out: list[dict] = []
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
            "title":    f"Zone `{zone}` SST correlation r={r:.2f} (n={n})",
            "what":     (
                "Even after subtracting the bias, the model isn't tracking "
                "relative day-over-day differences in this zone. Likely "
                "missing physics: nearshore upwelling, kelp shading, "
                "estuarine outflow, or a high-relief mixing pattern."
            ),
            "action":   (
                f"Investigate Phase D corrections for `{zone}` — bathymetry-"
                f"coupled upwelling index, solar warming on shallow shelves, "
                f"or tidal mixing at high-relief features. File: "
                f"`pipeline/sst_predict/nearshore.py` (when implemented)."
            ),
        })
    return out


def rule_spot_bias(spots: dict) -> list[dict]:
    """R5: per-spot |bias_F| > 1.5, n >= 20 — spot-specific microclimate."""
    out: list[dict] = []
    for spot_id, m in sorted(spots.items()):
        n = int(m.get("n") or 0)
        bias = m.get("bias_f")
        if n < MIN_N_BIAS_SPOT or bias is None:
            continue
        bias = float(bias)
        if abs(bias) <= BIAS_THRESHOLD_F:
            continue
        delta = round(-bias, 2)
        out.append({
            "rule":     "R5",
            "severity": "medium",
            "spot_id":  spot_id,
            "spot_name": m.get("name", spot_id),
            "n":        n,
            "bias_f":   round(bias, 2),
            "title":    (
                f"Spot `{m.get('name', spot_id)}` has SST bias "
                f"{bias:+.2f} °F (n={n})"
            ),
            "what":     (
                f"This dive site systematically reads "
                f"{abs(bias):.1f} °F {'colder' if bias > 0 else 'warmer'} "
                f"than the bbox cell average — typical for spots dominated "
                f"by local microclimate (upwelling at headlands, sun-trap "
                f"coves, river-mouth mixing)."
            ),
            "action":   (
                f"Add or update `SPOT_CORRECTIONS[{spot_id!r}].alpha_f` "
                f"by `{delta:+.2f}`. File: `pipeline/sst_predict/config.py`. "
                f"This adjusts the per-spot prediction the saved-spots panel "
                f"shows; the bbox grid is unaffected."
            ),
        })
    return out


# ----- Markdown rendering ----------------------------------------------

def _section(findings: list[dict], heading: str) -> str:
    if not findings:
        return ""
    lines = [f"## {heading}", ""]
    for f in findings:
        sev = f.get("severity", "medium")
        title = f.get("title", "(no title)")
        lines.append(f"### {title} _({sev})_")
        lines.append("")
        if f.get("what"):
            lines.append(f.get("what"))
            lines.append("")
        if f.get("action"):
            lines.append(f"**Action:** {f.get('action')}")
            lines.append("")
    return "\n".join(lines)


def render(zones: dict, spots: dict, findings: list[dict]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    lines = [
        "# SST watchdog",
        "",
        f"_Computed {now}._",
        "",
        f"**Findings:** {len(findings)}",
        "",
    ]
    if not findings:
        lines.append("All zone + per-spot SST checks pass.")
    by_rule: dict[str, list[dict]] = {}
    for f in findings:
        by_rule.setdefault(f.get("rule", "?"), []).append(f)
    for rule_id, group in sorted(by_rule.items()):
        heading = {
            "R1": "Zone bias (R1)",
            "R3": "Zone correlation (R3)",
            "R5": "Per-spot bias (R5)",
        }.get(rule_id, f"Rule {rule_id}")
        section = _section(group, heading)
        if section:
            lines.append("")
            lines.append(section)

    # Always include the metrics tables for an at-a-glance view.
    if zones:
        lines.append("")
        lines.append("## Zone metrics")
        lines.append("")
        lines.append("| Zone | n | bias_f | rmse_f | pearson_r |")
        lines.append("|------|---|--------|--------|-----------|")
        for z, m in sorted(zones.items()):
            if not m.get("n"):
                continue
            lines.append(
                f"| `{z}` | {m['n']} | {m['bias_f']:+.2f} | "
                f"{m['rmse_f']:.2f} | {m.get('pearson_r')!s} |"
            )
    if spots:
        non_empty = {sid: m for sid, m in spots.items() if m.get("n")}
        if non_empty:
            lines.append("")
            lines.append("## Per-spot metrics")
            lines.append("")
            lines.append("| Spot | n | bias_f | rmse_f | suggested Δα |")
            lines.append("|------|---|--------|--------|--------------|")
            for sid, m in sorted(non_empty.items()):
                lines.append(
                    f"| {m.get('name', sid)} | {m['n']} | {m['bias_f']:+.2f} | "
                    f"{m['rmse_f']:.2f} | {m.get('suggested_alpha_delta_f', 0):+.2f} |"
                )
    return "\n".join(lines)


# ----- Main -------------------------------------------------------------

def main() -> int:
    zone_doc = _load_json(ZONE_METRICS_PATH)
    spot_doc = _load_json(SPOT_METRICS_PATH)
    zones = (zone_doc or {}).get("zones") or {}
    spots = (spot_doc or {}).get("spots") or {}

    findings: list[dict] = []
    findings.extend(rule_zone_bias(zones))
    findings.extend(rule_zone_correlation(zones))
    findings.extend(rule_spot_bias(spots))

    body = render(zones, spots, findings)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(body, encoding="utf-8")
    print(f"sst_watchdog: wrote {SUMMARY_PATH} ({len(findings)} findings)")
    for f in findings:
        sev = f.get("severity", "medium")
        print(f"  [{sev:>10}] {f.get('rule')}: {f.get('title')}")

    # Exit 1 on any finding so the workflow opens/updates the issue.
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
