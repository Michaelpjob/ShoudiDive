# The strict scientific process

This is the contract for anyone (human or agent) who changes how ShouldIDive
turns data into a number a diver sees. It exists because we repeatedly did the
opposite: polished the presentation of numbers that were never checked against
reality, and painted color into space we never measured.

It has two halves:

- **The constraints** (below), named S1 through S6.
- **The harness** that enforces them: `pipeline/validation/integrity_gate.py`
  (S2 through S5), `tests/checkpoints/display-honesty.test.js` (S1), and this
  document plus the report rule (S6). Run the harness before shipping anything
  that touches display, scoring, or coefficients. CI runs it too.

## Why this exists (the audit that triggered it, 2026-07-06)

Concrete findings, all reproducible from the repo:

1. **The primary output was unvalidated and the check was dormant.** We had
   ~1,460 ground-truth observations ingested (369 with measured visibility),
   yet `residuals.jsonl` was empty and `per_zone_metrics.json` was `{}`. Nobody
   had run the comparison. When run: of 369 observations only a handful joined,
   and the correlation between our predicted visibility and observed visibility
   was **r near 0** (bight_nearshore r = -0.08 on n = 6). We do not yet have
   evidence the visibility number tracks reality.
2. **The loop could never accumulate.** `LOOKBACK_DAYS = 2` in the scorer, but
   the prediction archive retains **one day**. Observations pile up against no
   stored prediction to score them against.
3. **The ground-truth feed was starved.** Observations in the last 24h were far
   below the watchdog floor; buoy scrapers silent.
4. **The confidence dot claimed validation we never did.** `confidence.js` set
   CA `viz` to `4 = Observed`, reason "Calibrated against CA dive ground-truth
   ingestion." That score was a hand-typed literal, not derived from residuals
   (which were empty). By the file's own scale, `viz` is model output = `3 =
   Modeled`.
5. **A chl gradient painted unmeasured cells.** A "supported gradient" bloomed
   real readings out ~35 km, painting "clean" over Northeast Bank from readings
   15 to 30 km away, on a day a diver was in green water there. Interpolation
   sold as observation.

## The constraints

### S1, no display fabrication

Observed-only layers (`chl`, `viz`) are never interpolated, smoothed,
gap-filled, or bloomed into cells where nothing was measured. A blank cell is
honest: it means "no recent measurement here," not "clear water." Sparse, coarse
data rendered as a smooth field is a lie about coverage. Boxiness is the data
being honest about its resolution.

Enforced by `tests/checkpoints/display-honesty.test.js`: no interpolation set in
`DataOverlay.jsx` may be keyed to an observed-only layer, and those layers stay
on the nearest-neighbour (pixelated, blank-gap) render path. Runs in the
`web-tests` CI job.

### S2, earned confidence

`viz` is the one bespoke **derived index**: a visibility number we invent from
other fields. Every other layer is a standard geophysical product whose
confidence legitimately reflects its source. A derived index may not display
above **Modeled (3/5)**, and may not use "validated / calibrated / ground-truth"
language, unless per-zone residual metrics justify it (currently: at least
n = 30 scored pairs and pearson r >= 0.30 in a zone). Until then it reads
"Modeled" and says so.

Enforced by the gate's `confidence-earned` check, which parses `confidence.js`
and cross-checks `per_zone_metrics.json`.

### S3, the loop is live and can accumulate

The predicted-vs-observed loop must actually run and build history: residuals
non-empty after scoring, and the prediction archive retained for at least
`LOOKBACK_DAYS` so the hindcast can reach the observations we collect. A dormant
loop (empty residuals) is a failure state, never a silent pass.

Enforced by the gate's `loop-live` and `archive-depth` checks.

### S4, ground truth must flow

Ingest health is part of the contract: enough fresh observations in the last
24h, required sources not silent. If the feed that would falsify us goes dark,
that is red, not "fine for now."

Enforced by the gate's `ingest-live` check (shares the watchdog floor).

### S5, fit, don't hand-tune

Every coefficient or correction that moves a number the user sees is registered
in `knobs_registry.json` as `fit` (tuned against residuals, must name the
artifact) or `provisional` (hand-picked, not yet validated). Provisional is
allowed, it is often the honest starting point, but it is surfaced in the
registry and never described to the user as truth. A `fit` claim with no
`fit_against` artifact fails the gate.

Enforced by the gate's `knob-registry` check.

### S6, claims to the user are falsifiable

Any quantitative claim about accuracy or coverage, in chat or in docs, cites the
decoded data or the residuals it rests on, with the sample size and the
caveats. "The map looks good" is not a claim; "cell 32.45,-117.6 reads 0.17
mg/m3, source 6 gap-fill, 3 days old, nearest real cell 15 km away reads 0.12"
is. No confident summary of a number we have not checked.

This one is not mechanically enforced. It is on the author. The report template
at the bottom of this file is the format.

## The harness: how to run it

```bash
# S1 (display fabrication), part of the web-tests suite:
node --test tests/checkpoints/display-honesty.test.js

# S2 through S5 (confidence, loop, ingest, knobs):
python pipeline/validation/integrity_gate.py           # default: blocks on S2, S5
python pipeline/validation/integrity_gate.py --strict  # blocks on everything
```

**Severity model.** The gate blocks (non-zero exit) on the constraints we
control inside a single PR: S2 (confidence) and S5 (knobs). S3 and S4 depend on
the loop being repaired; they are reported as tracked RED but do not fail the
build **yet**. The day Phase 1 lands (residuals accumulating, ingest green), CI
flips to `--strict` and they become blocking. They are tracked, not hidden, and
"gate passed with tracked RED" is not "done."

CI wiring: `science-integrity` job in `dev-checks.yml` runs the gate; the S1
test runs inside `web-tests`.

## The remediation plan

Ordered. Each phase names its exit criterion in terms of the gate.

**Phase 0, stop the active dishonesty (done in this change).**
- `viz` confidence dropped from "Observed/Calibrated" to "Modeled," honest
  reason. Gate S2 green.
- The chl gradient is quarantined on its preview branch and blocked from main by
  S1. Prod stays on honest per-cell (blank-gap) rendering.
- Hand-tuned knobs registered as provisional. Gate S5 green.

**Phase 1, make the loop live (flips S3 and S4 green, then CI to --strict).**
1. Retain the daily prediction archive for at least `LOOKBACK_DAYS` (ideally a
   rolling 30 to 60 days) so the hindcast can accumulate. Today it keeps one
   day.
2. Run `score.py` inside the refresh cron and commit `residuals.jsonl` +
   `per_zone_metrics.json`, so skill is recomputed every refresh, not by hand.
3. Repair the ingest (CDIP + NDBC scrapers silent) so ground truth keeps
   flowing above the floor.
4. Surface r / rmse / in-band% somewhere we see daily (the watchdog issue
   already exists; wire the metrics into it).
- **Exit criterion:** gate `--strict` passes; S3/S4 green; a real skill number
  exists for at least one zone.

**Phase 2, earn the confidence back (may raise S2 ceiling).**
1. With residuals accumulating, refit the `viz_predict` coefficients against
   them instead of expert-setting (registry knob `viz_predict.coefficients`
   moves from provisional to fit).
2. Refit the offshore-distrust magnitudes against offshore residuals (registry
   knob `offshore_chl_distrust.magnitudes` moves to fit).
3. Only if a zone clears n >= 30 and r >= 0.30 does `viz` there earn a display
   above "Modeled," and only then does validation language return, backed by the
   metric.

## Report template (S6)

When reporting on a number's quality, use this shape:

> **Claim:** <what you are asserting>
> **Evidence:** <decoded cell / residual metric, with values>
> **Sample:** n = <count>, <freshness / source provenance>
> **Caveats:** <what this does not prove>

If you cannot fill in Evidence and Sample, you do not have a claim, you have a
guess, and you say so.
