# Validation pipeline — operator's guide

How to read the data this system produces, and how the automated
review surfaces issues without you having to ask.

---

## TL;DR — where things live

| File | What it is | When to look |
|---|---|---|
| `data/observations.jsonl` | One line per ground-truth observation. Append-only. | Spot-check what's flowing in. |
| `data/residuals.jsonl` | Predicted vs observed pairs. Rewritten each refresh-data run. | Sanity-check a single zone or source. |
| `data/per_zone_metrics.json` | Per-zone RMSE / bias / calibration / Pearson r. **The dashboard.** | Daily glance. |
| `data/per_zone_metrics_baseline.json` | Frozen baseline for the regression guard. | After you promote one. |
| `data/watchdog_summary.md` | Human-readable findings from the latest watchdog run. | When the bot opens an issue. |
| `data/archive/{YYYY}/{MM}/{DD}.jsonl.gz` | Per-cell prediction snapshots (gitignored). | Only inside CI; you won't see this on local clones. |

---

## How automated review works

You don't prompt me to flag issues — the system does it for you.

After every nightly `refresh-data.yml` run:

1. `score.py` joins observations to that day's archive snapshot, writes `residuals.jsonl` and `per_zone_metrics.json`.
2. `check_regression.py` fails the workflow if any zone's RMSE jumped >20% versus the frozen baseline (sleeps until you promote one).
3. `watchdog.py` runs four rule checks (see below) and writes `watchdog_summary.md`.
4. **A GitHub Action then opens, updates, or closes a rolling Issue** tagged `validation-watchdog`:
   - Findings present → upsert the issue with the latest findings markdown
   - No findings → close any open issue
5. You get a GitHub email notification when the issue opens or updates. You don't have to check anything yourself; the bot opens the report when it has something to say.

The Issue body lists each finding with a suggested action and a file path to edit. When the next run produces a clean report, the issue auto-closes — so the inbox stays low-noise.

### Watchdog rules

| Rule | Threshold | Min n | Action |
|---|---|---|---|
| **R1** Zone systematic bias | `\|bias_ft\| > 5.0` | 30 | Bump `SECCHI_COEFFS[zone].a` |
| **R2** Zone interval calibration | `calibration_pct ∉ [0.60, 0.95]` | 30 | Tweak `SIGMA_LOG_CHL[zone]` |
| **R3** Zone correlation | `pearson_r < 0.30` | 50 | Structural — revisit `visibility.py` |
| **R4** Data-flow health | `obs_24h < 50` OR a required source silent >24h | n/a | Check ingest cron logs |

Thresholds and gates are constants in `watchdog.py` — tighten or relax them there as the system matures.

### Regression guard

Separate from watchdog. The guard exists to catch deploys that *worsen* accuracy versus a snapshot you've explicitly locked in:

1. After ≥30 days of obs, run **Actions → Promote validation baseline → Run workflow**. That commits `per_zone_metrics_baseline.json` to main.
2. From that point on, `refresh-data.yml`'s regression guard step **fails the build** if any zone's RMSE jumps >20% above the baseline. Your deploy doesn't go out, you investigate, you fix or accept the new normal by re-promoting the baseline.

The watchdog *suggests*; the regression guard *enforces*. They don't overlap.

---

## How to read each file

### `per_zone_metrics.json` — the dashboard

```json
{
  "computed_at": "2026-04-28T06:15Z",
  "lookback_days": 2,
  "zones": {
    "bight_nearshore": {
      "n": 47,
      "rmse_ft": 6.8,
      "bias_ft": -2.4,
      "mae_ft": 5.1,
      "calibration_pct": 0.71,
      "pearson_r": 0.52
    },
    "central_offshore": { ... }
  }
}
```

How to read each field:

- **n** — sample count. Anything <30 is too noisy to act on. R1/R2 require ≥30; R3 requires ≥50.
- **rmse_ft** — typical magnitude of error in feet. <5 is good; 5–10 is workable; >10 means the model is unreliable in this zone.
- **bias_ft** — `predicted − observed`. Positive = over-predicting (model says clearer than it really is); negative = under-predicting. >5 ft trips the watchdog.
- **mae_ft** — mean absolute error. Robust to outliers. Compare to `rmse_ft` — if `rmse >> mae`, you have a few bad outlier days dominating the metric.
- **calibration_pct** — fraction of observations that landed inside the model's `[p10, p90]` interval. Honest target: 0.80. <0.60 = the interval is too narrow (overconfident); >0.95 = the interval is too wide (underconfident).
- **pearson_r** — correlation between predicted and observed. >0.7 = the model captures relative differences well; <0.3 = even after subtracting the bias, the model isn't tracking which days are clearer than others. R3 catches that.

### `residuals.jsonl` — the audit trail

One line per observation that got scored. Useful when you want to investigate a specific finding:

```bash
# what did the model predict vs reality at La Jolla over the last week?
jq 'select(.zone == "bight_nearshore" and .source == "dive-shop-justgetwet")' \
  pipeline/validation/data/residuals.jsonl

# everything where the model was off by more than 10 ft:
jq 'select(.residual_ft | fabs > 10)' \
  pipeline/validation/data/residuals.jsonl

# count residuals per zone in this scoring run:
jq -r '.zone' pipeline/validation/data/residuals.jsonl | sort | uniq -c
```

Each row carries the full driver values used by the model that day, so you can see *why* the model said what it said:

```json
{
  "obs_id":           "dive-shop-justgetwet-20260428-la-jolla-0",
  "predicted_p50_ft": 24.5,
  "observed_ft":      18.0,
  "residual_ft":      6.5,
  "in_p10_p90":       false,
  "zone":             "bight_nearshore",
  "drivers":          {"upwell": 0.04, "seasonal": 0.15, ... },
  "source":           "dive-shop-justgetwet",
  "source_confidence": 0.85,
  "coeff_hash":       "807b5c95e604"
}
```

That `coeff_hash` is the SHA of the active `viz_predict/config.py`. Every coefficient change rolls the hash, so you can attribute residuals to specific config versions.

### `observations.jsonl` — what the scrapers found

Append-only, full history of every observation. Buoys (`source: cdip-buoy`, `ndbc-buoy`) populate `observed_sst_f` + `observed_swell_ft` but leave `observed_secchi_ft` null — they don't measure water clarity. Dive shops populate `observed_secchi_ft`.

```bash
# how many obs per source today?
today=$(date -u +%Y-%m-%d)
grep "\"timestamp_utc\":\"${today}" pipeline/validation/data/observations.jsonl \
  | jq -r '.source' | sort | uniq -c

# last 5 dive-shop obs:
grep '"source":"dive-shop-' pipeline/validation/data/observations.jsonl \
  | tail -5 | jq .
```

---

## The cadence

- **Hourly** (`:15 UTC`) — `ingest-ground-truth.yml` runs every scraper, dedupes, commits new observations.
- **Daily** (`06:00 UTC`) — `refresh-data.yml` runs prediction → archive → score → regression guard → watchdog → deploy. The watchdog opens/updates/closes its rolling issue at the end.
- **Manual** (workflow_dispatch) — `promote-baseline.yml` locks the current `per_zone_metrics.json` as the regression guard's baseline.

---

## When to act

| Signal | What to do |
|---|---|
| Watchdog Issue opens with **R1** finding | Read the suggested `SECCHI_COEFFS` delta. If you trust it, edit `pipeline/viz_predict/config.py` and commit. The next refresh-data run scores against the new coefficients and the issue auto-closes if the bias clears. |
| Watchdog issue with **R2** finding | Less urgent. Tweak `SIGMA_LOG_CHL[zone]` by ±0.05; re-evaluate in 7 days. |
| Watchdog issue with **R3** finding | Don't tweak coefficients. The model is *structurally* missing something for this zone. Open `viz_predict/visibility.py` and `zones.py`; consider whether a new driver term is needed. |
| Watchdog issue with **R4** finding | Open the latest hourly ingest workflow run; look for `FAILED — …` lines per scraper. Most scraper outages are URL changes. |
| Regression guard fails a deploy | A coefficient change just made things worse. Either revert the change, or re-promote the baseline if the new normal is intentional. |

That's the whole interaction model — the system tells you when something needs your attention, and tells you what file to edit. You don't need to ask.

---

## Where the rules are tuned

- **Watchdog thresholds** (R1–R4): module constants at the top of `pipeline/validation/watchdog.py`. Tighten as confidence in the data builds.
- **Regression guard threshold** (+20%, n≥30): module constants at the top of `pipeline/validation/check_regression.py`.
- **Source confidence weights**: per-scraper `source_confidence` class attribute; affects how much each obs counts in the weighted RMSE/bias.
- **Lookback window**: `LOOKBACK_DAYS` in `score.py` — how far back the scorer joins observations against the archive.
