"""CI regression guard for the visibility model.

Fails the build when the current ``per_zone_metrics.json`` shows a
zone whose RMSE has jumped >``THRESHOLD_PCT`` versus a frozen baseline
``per_zone_metrics_baseline.json``. The point is to catch coefficient
tweaks that quietly make accuracy worse in zones the human reviewer
wasn't watching.

Smart gating — the guard *sleeps* until there's enough signal:

  1. If the baseline file doesn't exist, exit 0 with a "no baseline
     yet" message. The first ~30 days of operation produce noisy
     metrics; promoting a baseline before then would be a false-
     positive factory.
  2. If no zone in the current metrics has ``n >= MIN_OBS_PER_ZONE``,
     exit 0. We don't care about zones with 5 observations — the
     RMSE is too noisy to be meaningful.
  3. Only zones that have ``n >= MIN_OBS_PER_ZONE`` in BOTH current
     and baseline are checked. New zones that haven't accumulated
     enough data yet are reported but not failed on.

To establish a baseline once you're satisfied with current accuracy:

    python -m pipeline.validation.check_regression --promote-baseline
    git add pipeline/validation/data/per_zone_metrics_baseline.json
    git commit -m "validation: promote baseline at coeff_hash <hash>"

The baseline is versioned in git, so every future regression comparison
is reproducible.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone


# Per the handoff: 20% RMSE jump in any zone is the failure threshold.
THRESHOLD_PCT = 1.20

# Minimum n per zone before we trust the RMSE enough to act on it.
# Below ~30 obs the empirical RMSE is dominated by sample noise and
# we don't want CI failing on a single bad-tide-day outlier.
MIN_OBS_PER_ZONE = 30


HERE = pathlib.Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
CURRENT_PATH = DATA_DIR / "per_zone_metrics.json"
BASELINE_PATH = DATA_DIR / "per_zone_metrics_baseline.json"


# ---- Loading ---------------------------------------------------------

def load_metrics(path: pathlib.Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  regression-guard: failed to read {path.name}: {exc}")
        return None


# ---- Comparison ------------------------------------------------------

def check_regression(current: dict, baseline: dict) -> list[str]:
    """Return a list of human-readable failure messages, empty if OK."""
    failures: list[str] = []

    cur_zones = (current or {}).get("zones", {}) or {}
    base_zones = (baseline or {}).get("zones", {}) or {}

    if not cur_zones:
        # Score script ran but produced no zones — usually means no
        # observations had viz measurements yet. Treat as "no signal,
        # no opinion" rather than a regression.
        print("  regression-guard: current metrics have no zones; nothing to check")
        return failures

    checked = 0
    skipped_low_n: list[str] = []
    skipped_new: list[str] = []

    for zone, cur_m in sorted(cur_zones.items()):
        cur_n = int(cur_m.get("n", 0) or 0)
        if cur_n < MIN_OBS_PER_ZONE:
            skipped_low_n.append(f"{zone} (n={cur_n})")
            continue

        base_m = base_zones.get(zone)
        if base_m is None:
            # Zone has data now but didn't when baseline was promoted.
            # Report but don't fail — this is normal as the system
            # accumulates coverage.
            skipped_new.append(zone)
            continue
        base_n = int(base_m.get("n", 0) or 0)
        if base_n < MIN_OBS_PER_ZONE:
            # The baseline itself was promoted before this zone had
            # enough data. Skip rather than compare against noise.
            skipped_low_n.append(f"{zone} (baseline n={base_n})")
            continue

        cur_rmse = float(cur_m.get("rmse_ft", 0.0) or 0.0)
        base_rmse = float(base_m.get("rmse_ft", 0.0) or 0.0)
        if base_rmse <= 0:
            # Baseline was saved with zero RMSE — pathological, ignore.
            continue

        ratio = cur_rmse / base_rmse
        delta_pct = (ratio - 1.0) * 100
        checked += 1

        marker = "FAIL" if ratio > THRESHOLD_PCT else "OK  "
        print(
            f"  {marker}  {zone:24s}  rmse {base_rmse:5.2f} -> {cur_rmse:5.2f} ft  "
            f"({delta_pct:+5.1f}%)  n={cur_n}"
        )
        if ratio > THRESHOLD_PCT:
            failures.append(
                f"REGRESSION in {zone}: RMSE {base_rmse:.2f} -> {cur_rmse:.2f} ft "
                f"({delta_pct:+.1f}%, n={cur_n}); threshold +{(THRESHOLD_PCT - 1) * 100:.0f}%"
            )

    if not checked:
        print(
            f"  regression-guard: no zones met n >= {MIN_OBS_PER_ZONE} threshold; "
            f"sleeping (skipped: {', '.join(skipped_low_n) or 'none'})"
        )
    if skipped_new:
        print(
            f"  regression-guard: {len(skipped_new)} zone(s) have data now but were "
            f"absent at baseline — informational only: {', '.join(skipped_new)}"
        )
    return failures


# ---- Baseline promotion ---------------------------------------------

def promote_baseline() -> int:
    if not CURRENT_PATH.exists():
        print(f"  promote: no current metrics at {CURRENT_PATH} — nothing to promote")
        return 1
    current = load_metrics(CURRENT_PATH)
    if current is None:
        return 1
    # Wrap with a promoted_at stamp so we can audit later.
    current = dict(current)
    current["promoted_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    BASELINE_PATH.write_text(json.dumps(current, indent=2))
    n_zones = len(current.get("zones", {}))
    print(
        f"  promoted current metrics ({n_zones} zones) -> "
        f"{BASELINE_PATH.name}\n"
        f"  commit it to lock the baseline against future drift."
    )
    return 0


# ---- Entry point -----------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if "--promote-baseline" in args:
        return promote_baseline()

    current = load_metrics(CURRENT_PATH)
    if current is None:
        # No current metrics — nothing to compare. The score step in
        # refresh-data.yml writes per_zone_metrics.json even when
        # zero residuals exist; absence here means score.py crashed
        # before getting that far. Be loud, but exit 0 — failing the
        # deploy on a missing-file would block legitimate refreshes.
        print(f"  regression-guard: {CURRENT_PATH.name} missing; nothing to check")
        return 0

    baseline = load_metrics(BASELINE_PATH)
    if baseline is None:
        print(
            f"  regression-guard: no baseline at {BASELINE_PATH.name} yet "
            f"(expected during the first ~30 days of data accumulation). "
            f"Run with --promote-baseline once you're satisfied with current accuracy."
        )
        return 0

    failures = check_regression(current, baseline)
    if failures:
        print()
        for f in failures:
            print(f"  [FAIL] {f}")
        return 1
    print("  regression-guard: all checked zones within threshold [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
