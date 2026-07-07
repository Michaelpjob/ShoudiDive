"""Scientific-integrity gate, the executable half of docs/STRICT-SCIENCE.md.

This is the harness. It encodes the constraints we agreed the app must
never violate, and it FAILS LOUDLY when we do. Run it before shipping any
change that touches how the app *displays* a number, *scores* its own
predictions, or *tunes* a coefficient. CI runs it too.

The constraints (see docs/STRICT-SCIENCE.md for the full rationale):

  S2  EARNED CONFIDENCE   A bespoke derived index (viz) may not display
                          above "Modeled" (3/5) unless per-zone residual
                          metrics justify it. No "validated / calibrated /
                          ground-truth" language without residual backing.
  S3  LIVE LOOP           The predicted-vs-observed loop must actually run
                          and accumulate: residuals non-empty after scoring,
                          and the prediction archive retained >= LOOKBACK_DAYS
                          so the hindcast can reach the observations.
  S4  GROUND TRUTH FLOWS  Ingest health: enough fresh observations, required
                          sources not silent.
  S5  FIT, DON'T HAND-TUNE Every coefficient/correction that moves a displayed
                          number is registered as `fit` or `provisional`.
                          `fit` must point at a residual artifact. Provisional
                          is allowed but never silently sold as truth.

(S1, NO DISPLAY FABRICATION, lives in the JS suite as
tests/checkpoints/display-honesty.test.js, because it inspects the
frontend render path. It is part of the same doctrine.)

Severity model
--------------
Each check returns PASS / WARN / FAIL. Exit code = number of *blocking*
failures. Two run modes:

  default  blocks on the checks we control inside a single PR (S2, S5).
           S3/S4 are reported as RED but do not fail the build yet, they
           depend on the loop being repaired (Phase 1 of the plan), and we
           refuse to block every PR on a loop we haven't fixed. They are
           tracked, not hidden.
  --strict blocks on ALL constraints. This is the TARGET state; flip CI to
           --strict the day Phase 1 lands (residuals accumulating, ingest
           green). The doc names that as the definition of done.

Nothing here modifies data or coefficients. It only measures and reports.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
DATA_DIR = HERE / "data"
ARCHIVE_ROOT = DATA_DIR / "archive"
OBS_PATH = DATA_DIR / "observations.jsonl"
METRICS_PATH = DATA_DIR / "per_zone_metrics.json"
CONFIDENCE_JS = REPO / "src" / "lib" / "confidence.js"
KNOBS_REGISTRY = HERE / "knobs_registry.json"

# --- thresholds (tune here; documented in STRICT-SCIENCE.md) ---------------
DERIVED_INDEX_LAYERS = {"viz"}   # layers that are OUR model, not a geophysical field
CONFIDENCE_MODELED = 3           # score ceiling for an unvalidated derived index
MIN_N_FOR_VALIDATED = 30         # residual pairs before a zone can claim skill
MIN_R_FOR_VALIDATED = 0.30       # pearson r before "Observed"/"Validated" is earned
INGEST_24H_FLOOR = 50            # observations in the last 24h (matches watchdog)
VALIDATION_WORDS = re.compile(r"validat|calibrat|ground[- ]?truth", re.I)
# A reason may mention validation while DENYING it ("not yet validated",
# "beta"). Those are honest; only a POSITIVE unbacked claim is a violation.
NEGATED_CLAIM = re.compile(
    r"not yet|unvalidat|uncalibrat|no residual|dormant|unproven|pending|beta",
    re.I)

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_GLYPH = {PASS: "✅", WARN: "\U0001f7e1", FAIL: "\U0001f534"}


class Result:
    def __init__(self, cid, name, status, detail, blocking):
        self.cid, self.name, self.status, self.detail, self.blocking = (
            cid, name, status, detail, blocking)


def _iter_jsonl(path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


# --------------------------------------------------------------------------
# S3, the loop is alive and can accumulate
# --------------------------------------------------------------------------
def check_loop_live(blocking):
    """Re-score fresh and confirm the predicted-vs-observed loop produces
    pairs. An empty residual set means we are flying blind on our primary
    output, the exact dormant state that let 369 ground-truth observations
    sit unscored."""
    sys.path.insert(0, str(HERE))
    try:
        import score  # noqa: WPS433 (local import by design)
        residuals = score.score_all_observations()
        metrics = score.per_zone_metrics(residuals)
    except Exception as exc:  # scoring crashed => loop broken
        return Result("S3a", "loop-live", FAIL,
                      f"scorer crashed: {exc!r}", blocking)

    n = len(residuals)
    obs_total = sum(1 for _ in _iter_jsonl(OBS_PATH))
    if n == 0:
        return Result("S3a", "loop-live", FAIL,
                      f"0 residuals scored from {obs_total} observations on file "
                      ",  the loop is dormant; the primary output is unvalidated",
                      blocking)
    zones = metrics.get("zones", metrics) if isinstance(metrics, dict) else {}
    lines = []
    for z, m in sorted(zones.items()):
        lines.append(f"{z}: n={m['n']} r={m['pearson_r']} rmse={m['rmse_ft']}ft "
                     f"cal={round(m['calibration_pct']*100)}%")
    status = WARN if n < MIN_N_FOR_VALIDATED else PASS
    detail = (f"{n} residuals from {obs_total} obs ({', '.join(lines) or 'no zones'})"
              + ("  [n below skill floor, cannot yet claim validation]"
                 if status == WARN else ""))
    return Result("S3a", "loop-live", status, detail, blocking)


def check_archive_depth(blocking):
    """The hindcast can only reach observations for days we retained a
    prediction snapshot. If depth < LOOKBACK_DAYS the loop can never
    accumulate, observations pile up against nothing."""
    try:
        sys.path.insert(0, str(HERE))
        import score
        lookback = int(getattr(score, "LOOKBACK_DAYS", 2))
    except Exception:
        lookback = 2
    days = sorted({p.stem for p in ARCHIVE_ROOT.rglob("*.jsonl.gz")})
    depth = len(days)
    if depth < lookback:
        return Result("S3b", "archive-depth", FAIL,
                      f"only {depth} prediction-snapshot day(s) retained "
                      f"(need >= LOOKBACK_DAYS={lookback}); hindcast cannot "
                      "accumulate skill signal", blocking)
    return Result("S3b", "archive-depth", PASS,
                  f"{depth} snapshot days retained (>= {lookback})", blocking)


# --------------------------------------------------------------------------
# S4, ground truth is flowing
# --------------------------------------------------------------------------
def check_ingest_live(blocking):
    now = datetime.now(timezone.utc)
    recent = 0
    for o in _iter_jsonl(OBS_PATH):
        ts = o.get("timestamp_utc")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (now - t).total_seconds() <= 86400:
            recent += 1
    if recent < INGEST_24H_FLOOR:
        return Result("S4", "ingest-live", FAIL,
                      f"{recent} observations in last 24h (floor {INGEST_24H_FLOOR}) "
                      ",  ground-truth feed is starved; scrapers likely broken",
                      blocking)
    return Result("S4", "ingest-live", PASS,
                  f"{recent} observations in last 24h", blocking)


# --------------------------------------------------------------------------
# S2, displayed confidence is earned
# --------------------------------------------------------------------------
def _parse_viz_confidence():
    """Pull every region's viz entry out of confidence.js:
    region -> (score:int, reason:str)."""
    text = CONFIDENCE_JS.read_text(encoding="utf-8")
    out = {}
    # region blocks look like:  ca: { ... viz: { score: 4, source: "..", reason: ".." }, ...
    for rm in re.finditer(r"(\w+):\s*\{(.*?)\n\s*\},", text, re.S):
        region, body = rm.group(1), rm.group(2)
        vm = re.search(r"viz:\s*\{\s*score:\s*(\d+)[^}]*?reason:\s*\"([^\"]*)\"", body, re.S)
        if vm:
            out[region] = (int(vm.group(1)), vm.group(2))
    return out


def _best_zone_metric():
    if not METRICS_PATH.exists():
        return 0, None
    try:
        payload = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0, None
    zones = payload.get("zones", {})
    best_n, best_r = 0, None
    for m in zones.values():
        if m.get("n", 0) > best_n:
            best_n = m["n"]
            best_r = m.get("pearson_r")
    return best_n, best_r


def check_confidence_earned(blocking):
    """viz is the one bespoke DERIVED index, a visibility number WE invent
    from other fields. Every other layer is a geophysical product whose
    confidence legitimately reflects its source. viz must earn anything above
    'Modeled' with residual metrics, and must not use validation language it
    hasn't backed."""
    entries = _parse_viz_confidence()
    if not entries:
        return Result("S2", "confidence-earned", WARN,
                      "could not parse viz entries from confidence.js", blocking)
    best_n, best_r = _best_zone_metric()
    earned = (best_n >= MIN_N_FOR_VALIDATED
              and best_r is not None and best_r >= MIN_R_FOR_VALIDATED)
    violations = []
    for region, (score, reason) in sorted(entries.items()):
        if score > CONFIDENCE_MODELED and not earned:
            violations.append(
                f"{region}.viz score={score} > Modeled({CONFIDENCE_MODELED}) "
                f"but skill unproven (best zone n={best_n}, r={best_r})")
        if (VALIDATION_WORDS.search(reason) and not NEGATED_CLAIM.search(reason)
                and not earned):
            violations.append(
                f"{region}.viz reason claims validation (\"{reason}\") with no "
                "residual backing")
    if violations:
        return Result("S2", "confidence-earned", FAIL,
                      " | ".join(violations), blocking)
    return Result("S2", "confidence-earned", PASS,
                  f"all viz entries <= Modeled or backed (best n={best_n}, r={best_r})",
                  blocking)


# --------------------------------------------------------------------------
# S5, hand-tuned knobs are registered and honest
# --------------------------------------------------------------------------
def check_knob_registry(blocking):
    if not KNOBS_REGISTRY.exists():
        return Result("S5", "knob-registry", FAIL,
                      f"missing {KNOBS_REGISTRY.name}, every coefficient that "
                      "moves a displayed number must be declared fit|provisional",
                      blocking)
    try:
        reg = json.loads(KNOBS_REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return Result("S5", "knob-registry", FAIL,
                      f"registry is not valid JSON: {exc}", blocking)
    knobs = reg.get("knobs", [])
    problems = []
    for k in knobs:
        name = k.get("name", "?")
        status = k.get("status")
        if status not in ("fit", "provisional"):
            problems.append(f"{name}: status must be fit|provisional (got {status!r})")
        if not k.get("note"):
            problems.append(f"{name}: missing note")
        if status == "fit" and not k.get("fit_against"):
            problems.append(f"{name}: marked fit but no fit_against artifact")
    if problems:
        return Result("S5", "knob-registry", FAIL, " | ".join(problems), blocking)
    prov = sum(1 for k in knobs if k.get("status") == "provisional")
    return Result("S5", "knob-registry", PASS,
                  f"{len(knobs)} knob(s) registered, {prov} provisional", blocking)


def run(strict: bool):
    # In default mode, S3/S4 report but do not block (loop not yet repaired).
    # --strict blocks on everything (the target state after Phase 1).
    checks = [
        check_confidence_earned(blocking=True),
        check_knob_registry(blocking=True),
        check_loop_live(blocking=strict),
        check_archive_depth(blocking=strict),
        check_ingest_live(blocking=strict),
    ]
    print("=" * 72)
    print(f"SCIENTIFIC-INTEGRITY GATE  ({'strict' if strict else 'default'} mode)")
    print("=" * 72)
    blocking_fails = 0
    for c in checks:
        flag = "" if c.blocking else "  (tracked, non-blocking)"
        print(f"{_GLYPH[c.status]} {c.cid:4s} {c.name:20s} {c.status}{flag}")
        print(f"      {c.detail}")
        if c.status == FAIL and c.blocking:
            blocking_fails += 1
    print("-" * 72)
    if blocking_fails:
        print(f"GATE FAILED: {blocking_fails} blocking violation(s). "
              "Fix before shipping, see docs/STRICT-SCIENCE.md.")
    else:
        red = [c for c in checks if c.status == FAIL]
        if red:
            print(f"GATE PASSED (blocking) but {len(red)} tracked RED item(s) remain "
                  ",  Phase 1 of the remediation plan. Not hidden, not done.")
        else:
            print("GATE PASSED, all constraints satisfied.")
    return blocking_fails


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="block on every constraint (target CI state post-Phase-1)")
    args = ap.parse_args()
    sys.exit(1 if run(args.strict) else 0)


if __name__ == "__main__":
    main()
