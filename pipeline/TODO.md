# Pipeline backlog

Queued work for the visibility pipeline. Listed in priority order. Do
not execute without explicit go-ahead — these are loaded into the
agent's todo list and will be picked up sequentially when the user
unblocks each.

---

## PR1 — Chl freshness fix (HIGH PRIORITY)

**Symptom**: visibility model showing places clearer than they actually
are, especially Coronados / Bight islands / cloudy NorCal days.

**Root cause** (verified in `02-fix.md`):
`pipeline/fetch.py:build_layer()` walks back up to 7 days hunting for
a non-cloudy chl pixel and writes whatever it found into
`chl_1d.png`. `pipeline/fetch_visibility.py` then reads that PNG and
hardcodes `age = 0.0`, telling the model "this is fresh today's
observation" even when the actual data is 4 days stale. Three
downstream consequences:

1. `persistence_with_decay` weight stays at 1.0 instead of decaying
   toward climatology (would be 0.07–0.51 at real age).
2. `effective_sigma` keeps p10/p90 narrow when it should widen.
3. `assign_quality` flags everything `OBSERVED_1D` even when stale.

**Fix scope**: ~50 LOC, no source change.

  * `pipeline/fetch.py` — emit `chl_1d_age_days.png` sidecar (mode='L',
    pixel = age_days + 1; 0 = no data) using `build_age_array()` over
    the same `stack` it already iterates.
  * `pipeline/fetch_visibility.py` — read the sidecar, decode, replace
    the hardcoded `age = 0.0` with the real per-cell ages.
  * `viz_predict/model.py` — no change; it already handles real ages.
  * Add `pipeline/tests/test_freshness.py` with the round-trip + the
    `assign_quality` end-to-end test.

**Manual validation**: re-run `fetch.py` + `fetch_visibility.py` for a
known-cloudy day before/after the fix; `viz_p50_ft.png` should show
LOWER viz numbers in the affected cells (decayed toward climatology),
and `viz_quality.png` should shift from code 1 (OBSERVED_1D) to codes
2–5 in those same cells.

Spec: `02-fix.md` § "Smoking gun #1" + `CLAUDE (8).md` § "PR1".

---

## PR2 — Blended chl secondary source (MEDIUM PRIORITY)

**Wait for PR1 to ship + a week of data** before starting this. Most
"too clear" days are fixed by PR1 alone; PR2 only matters when VIIRS
misses a region for >7 consecutive days and we cascade to climatology.

**What it adds**: NOAA MODIS Aqua daily chl (`erdMH1chla1day` on
`coastwatch.pfeg.noaa.gov/erddap`) as a second-opinion chl source.
Independent sensor from VIIRS, same ERDDAP server (no new infra).

**Behaviour**: per-cell priority logic — prefer the blended source
when (a) it has a valid value AND (b) its age ≤ 2 days. Otherwise
fall back to VIIRS NRT. Save a `chl_source.png` diagnostic so we can
see which cells used which feed.

**Gated behind**: `ENABLE_BLENDED_CHL=1` env var. Default off until
A/B validation looks clean.

**Validation tooling**: `pipeline/diff_chl_sources.py` prints
coverage delta + per-cell value delta between the two sources; run
each day for a week before flipping the gate.

Spec: `02-fix.md` § "Smoking gun #2" + `CLAUDE (8).md` § "PR2".

---

## Queue policy

- Items are picked up in order. PR1 before PR2.
- Each item has its own spec; the agent doesn't reinterpret the design
  decisions from these handoffs at execution time, only the
  implementation tactics.
- "Add to queue" from the user means: read, file here, don't execute.
  "Pull next from queue" means: pick the topmost unstarted item.
