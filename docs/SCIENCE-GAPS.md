# Science gaps — where the models are thin, even after the analysis

**As of:** 2026-07-06. **Scope:** the physical/empirical models that turn remote-sensing +
model data into dive predictions — water clarity (`pipeline/viz_predict/`), SST correction/forecast
(`pipeline/sst_predict/`, `sst_buoy_correction.py`), depth-resolved water column
(`pipeline/viz_column/`), and the standalone kelp-paddy drift proto.

This is a **living backlog**, not a verdict that the science is wrong. It exists so the
validation work has a ranked target list instead of living in one person's head. Every claim
here is sourced to a file, a citation, or an inventory pass — check it before acting; models
drift.

> **One-line honest summary:** the science is *physically well-reasoned* (literature-anchored
> mechanisms, sensible bounds) but *empirically thin* — **fewer than 20% of ~65 deployed
> coefficients have any residual-based validation**, and the predictions users lean on hardest
> (below-thermocline clarity, cliff depth, per-spot SST) are among the least validated.

---

## The meta-gap: we built the calibration loop and it's starved

Everything needed to close the loop scientifically is **built and wired** — the watchdog rules
(R1–R5), per-zone residual scoring (`pipeline/validation/score.py`, `sst_score.py`), the archive
snapshots, the GitHub-issue feedback path. It cannot close because the **input is a trickle**:

- **~5 total ground-truth observations** in the system as of 2026-07-04
  (`pipeline/validation/data/watchdog_summary.md`: bight_nearshore n=4, central_nearshore n=1).
- The watchdog needs **≥30 observations per zone** (12+ zones) before a single calibration rule
  fires. At the current ~28 obs/day coast-wide, most zones never reach it.
- The **residual archive is ~2 days deep** — so even as obs accumulate, there is no historical
  record to calibrate against retroactively.

**Everything below is gated on this.** The fastest way to make the whole model honest is not to
re-tune coefficients — it is to feed the loop. See [§ Ground-truth sourcing](#ground-truth-sourcing-the-highest-leverage-fix).

---

## The five structural gaps (ranked by leverage)

### 1. We validate the wrong altitude of the model
Divers report **surface / overall** visibility. The model's hardest, most novel, most
likely-wrong predictions are **below** that: cliff (thermocline) depth, below-cliff visibility,
depth-resolved structure. So the parts we can least validate are the parts most likely to be
wrong. The water-column model has ~20 coefficients anchored to **one location** (Point Loma,
June — `DECISIONS.md` WC-D2), and its own code calls the internal-tide phase "the weakest-evidence
guess in the model" (WC-D3) — a guess the literature now **contradicts** (see [§ literature](#literature-verdicts), row 3).

### 2. The paddy model is validated against a proxy, circularly
Its headline "13/17 criteria pass, 100% hotspot hit-rate" scores against a **fishing-ground
catalog** (where fish get caught), not **paddy trajectories** (where kelp drifts). The
opportunity-weighting was tuned to hit that catalog, so the hit-rate is partly circular. The
actual physics — Lagrangian drift, current fusion, epibiont-ballast decay — has **zero trajectory
validation**. Encouragingly, the *thermal and persistence* science underneath it is
literature-**supported** (see [§ literature](#literature-verdicts), row 5); it's the *drift
endpoint* that's unproven.

### 3. Literature coefficients ported to regions we already know they don't fit
The Secchi exponent `b` is a coastal power-law value applied uniformly across all CA/Baja zones,
and is slated for PNW + tropical — where the expansion docs *themselves* say chl→Secchi "does not
work" (river/tidal Salish Sea; oligotrophic Caribbean needs an inverted model). NorCal
coefficients are "ported from CA-central as a starting guess" and Reef Check data **already
flagged** them as over-predicting on bloom days (`config.py` norcal notes) — but they ship,
because there's no local data to replace them.

### 4. The honesty work fixed *data* provenance, not *model* provenance
The 2026-07 observed-only / confidence-veil work (PRs #287, #292, #294) made the **data** honest —
a cell now reads "gap-filled" vs "observed," and fallback sources are labeled. But the confidence
**dot** still shows a static "Observed 4/5" even when the whole grid is interpolated, and more
subtly: a cell can be genuinely-observed chl and still feed an **unvalidated Secchi coefficient**.
The second-order uncertainty — "we measured the chl but the conversion to visibility is a
hand-tuned guess for this zone" — is invisible to the user.

### 5. Four models of one water column, never checked for mutual consistency
SST, chl, cliff-depth, and predicted-viz all describe the same physical column. A strong upwelling
event should cool SST *and* shoal the cliff *and* raise chl *and* drop visibility together — but
they're calibrated independently, and are **concretely inconsistent** (see [§ consistency
audit](#cross-model-consistency-audit)). There is no cross-model consistency check anywhere. This
is a cheap, high-value validation surface we haven't built.

---

## Literature verdicts

Peer-reviewed check on the shakiest deployed coefficients. Verdict is vs the app's *current*
value: **SUPPORTED** / **DEFENSIBLE-WITHIN-RANGE** / **QUESTIONABLE** / **CONTRADICTED**.

| # | Coefficient (app value) | Verdict | Literature | Action |
|---|---|---|---|---|
| 1 | **Secchi = a·chl^(−b)**, b≈0.28–0.32 all zones | **DEFENSIBLE** — but `b` is the wrong thing to worry about | Measured ln(Zsd)–ln(chl) slopes: coastal/case-2 **0.12–0.30** (Harvey/Stæhr et al. 2018 *Front. Mar. Sci.* 5:496), pure case-1 **~0.4–0.5** (Morel 1988; Morel et al. 2007). The app's 0.28–0.32 sits at the top of the coastal range — a reasonable case-2 compromise, and *safer* (flatter, won't over-swing on chl alone) than a case-1 exponent. **The real risk isn't `b` — it's using chl as the sole driver where CDOM/sediment dominate:** in the Bothnian Sea case, CDOM explained 46% of Secchi variance vs 6% for chl. That's the "reads clear off SD when it's not" failure mode. | Keep `b`. The rigorous fix is to drive clarity off **Kd** (which already blends CDOM+sediment+chl) rather than chl^−b — see Lee 2015 in row 2. Don't extend the power law to Salish Sea / Caribbean. |
| 2 | **Poole–Atkins Secchi ≈ 1.7 / Kd490** | **DEFENSIBLE-WITHIN-RANGE, but biased the wrong way** | Poole & Atkins 1929: product averages **1.7** (range 1.32–2.18) — but that was 14 stations and later found to rest on a calculation error (Walker 1980). Coastal-turbid water is **~1.44** (Holmes 1970, *measured off Santa Barbara*). **1.7 is the clear-water end → it over-predicts Secchi (over-states clarity) in coastal CA water — the same wrong direction as the SD "reads clear" bug.** Separately, pinning to **Kd490** is questionable: Lee et al. 2015 (*RSE* 169) show Secchi tracks the *minimum* Kd across visible bands, which shifts toward 550–570 nm in green coastal water. | If the Kd-blend activates (Phase 2), drop the constant toward **~1.44** for coastal CA/Baja, or — the state-of-the-art fix — use Lee 2015 `Zsd ≈ 1/(2.5·min(Kd over visible bands))`, which drops both the fixed-constant and single-wavelength assumptions and validated at R²=0.96 with no regional tuning. |
| 3 | **Internal-tide: cliff deepest at high water, ±6 ft (±1.8 m)** | **CONTRADICTED (both axes)** | Phase: isotherm depth on the SoCal inner shelf is **not** in phase with the surface tide — it tracks internal-bore packets with a variable multi-hour lag, often organized by spring–neap, and **cold** shoreward bores are as common as warm (Lerczak/Winant/Hendershott 2003 *JGR* 108(C3); Pineda 1991 *Science* 253). Amplitude: observed isotherm excursions at kelp depths are **5–20 m** routinely, **±3–8 m typical**, 10–20 m in events (Fales et al. 2023 *Front. Mar. Sci.* 10:1007789; Becherer et al. 2021 *JPO* 51(8); Nam & Send 2011 *JGR* 116). | **Drop "deepest at high water."** Raise amplitude to ±3–8 m typical / allow 10–20 m events. The code already isolates the phase behind `PHASE_DEEPEST_AT_HIGH_WATER` — flip it to a variable-lag model or remove the diurnal swing until there's local data. |
| 4 | **Upwelling shoals thermocline within ~10–30 km of shore** | **SUPPORTED** | Coastal upwelling band width ≈ local baroclinic Rossby radius, **10–20 km** at 33–38°N (Oregon State Rossby-radius atlas; Nature *Comms E&E* 2023). Wind-stress-curl (Ekman-suction) upwelling extends 100–200 km further, which the app doesn't claim. | No change. The app's 25 km decay scale is within range. |
| 5 | **Kelp: wave Hs²·Tp detachment; SST 19–20 °C stress / 24 °C lethal; raft 63–109 d, bryozoan-ballast sinking** | **SUPPORTED / DEFENSIBLE** | Wave: driver is orbital velocity (Hs × period) — the app's energy form is right; Cavanaugh et al. 2011 *MEPS* 429 gives Hs–loss r²=0.50. Temp: 20 °C onset is fundamentally a low-nitrate signal, damage ramps from ~22 °C, lethal ~24 °C (Zimmerman & Kremer 1984; MHW mortality studies 2024). Raft: Hobday 2000 *JEMBE* 253:97–114 (63–109 d max, temp-mediated blade-erosion clock); Graiff et al. 2016 *Mar. Biol.* 163:191 (sinks at ~40% bryozoan-biomass ballast while still viable). | Keep. Frame the wave term as orbital-velocity (long-period swell bites at lower Hs) and the 20→24 °C band as a graded ramp. **Citation fix:** the 63–109 d figure is Hobday's *JEMBE* paper, not the *MEPS* 195 dispersal companion. |
| 6 | **Below-cliff vis ≈ 40% of surface → 15% under upwelling/resuspension** | **DEFENSIBLE-WITHIN-RANGE (directional, magnitudes heuristic)** | No paper endorses the specific fractions, but every mechanism is documented (subsurface chl-max, particle-trapping at the pycnocline, bottom nepheloid layers). Visibility ∝ 1/c (beam attenuation), so 40% ⇒ c≈2.5× surface (ordinary for a turbid lower layer) and 15% ⇒ c≈6–7× (aggressive but plausible for a bloom or active resuspension). Lee et al. 2015: `Vw = −ln(Ct)/c ≈ 4–5/c`. | Cheap defensibility upgrade: derive the below-cliff ratio from a **modeled c (or Kd) contrast** via `vis_ratio = c_surface/c_deep` instead of the fixed 0.40/0.15 — reframes it from "unsourced number" to "standard optical relation." Still the lowest-confidence numbers in the stack; label as heuristics in-code. |

---

## Cross-model consistency audit

In-repo physics audit of whether the four models compute shared drivers consistently. Verdicts
with file evidence:

| Driver | Verdict | Evidence |
|---|---|---|
| **Coast-normal geometry** | **INCONSISTENT (critical)** | `viz_predict` computes a **per-cell** coast-normal from the coastline (`fetch_visibility.py`), `viz_column` hardcodes `ALONGSHORE_EQUATORWARD_DEG = 140.0` (`config.py`), `sst_predict/nearshore.py` hardcodes `COAST_NORMAL_DEG = 295.0`. The two scalars don't reconcile to one coastline orientation, so the models project the same wind onto different axes — worst at Point Conception's bend. |
| **SST↔chl coupling** | **UNCOUPLED (high)** | `viz_predict` samples **raw** `sst_1d.png` as its SST/trend chl driver — never the buoy-corrected / nearshore-cooled output `sst_predict` produces. During upwelling the temperature layer cools but the clarity model doesn't see it; the two layers can disagree on whether the event is intensifying. Low-cost fix (one input path). |
| **Wave orbital velocity** | **INCONSISTENT (medium)** | `viz_column` uses the Hunt (1979) dispersion approximation; `viz_predict.bottom_stir_index` uses a naive deep-water decay that can overstate near-bottom velocity ~50% in shallow water. "Bottom stir" means different things in the two models. |
| **Upwelling index** | **INCONSISTENT (medium)** | Three formulations: `viz_predict` couples wind+SST (geometric mean), `viz_column` uses pure Ekman wind-stress, `sst_predict` uses a wind-speed threshold. Different scales, not cross-comparable. |
| **Shared fluid constants** (ρ, Cd, f, g) | **DUPLICATED-BUT-EQUAL (low)** | Cleanly encapsulated in `viz_column/config.py`, values consistent, not duplicated with drift. Good. One caveat: single Coriolis f for the whole bbox (~15% error noted in-code). |
| **Zone definitions** | **CONSISTENT on CA baseline (medium debt)** | SST + viz share the 3×3 lat×dist scheme by design (`sst_predict/config.py` comment). But `sst_predict` still lists only the original CA zones — no Baja/NorCal — so SST forecasts would silently misclassify there. |

**Cheapest high-impact fixes:** (a) point `viz_predict` at the corrected SST — one input path,
removes a real physical contradiction; (b) reconcile the coast-normal geometry to one shared
per-cell field — kills a systematic upwelling misalignment. Both are worth a cross-model
consistency test (e.g. "a synthetic upwelling-favorable wind produces same-sign responses in all
four models").

---

## Ground-truth sourcing (the highest-leverage fix)

The meta-gap is data volume. The fastest wins are machine-readable **ERDDAP** feeds that already
emit turbidity + chlorophyll and need no auth. **Top 3 to integrate first:**

1. **SCCOOS `autoss` ERDDAP** — turbidity (NTU) + chl + SST at 4 SoCal-Bight piers (Scripps,
   Newport, Santa Monica, Stearns Wharf), 1–4 min, no auth.
   `https://erddap.sccoos.org/erddap/tabledap/autoss.csv`. Directly fixes SoCal-Bight zone counts
   **and** gives *observed* NTU to correct the interpolated-chl "reads clear when it's not" bias.
2. **CeNCOOS ERDDAP** — turbidity + chl at Monterey / Santa Cruz / Morro / Cal Poly, 15 min, no
   auth — **and the MBARI M1 offshore mooring lives on the same host**, so one integration covers
   Central-CA nearshore + offshore. `https://erddap.cencoos.org/erddap/tabledap/mlml_monterey.csv`.
3. **Reef Check California** — diver-recorded transect **visibility** spanning CA + Baja + **PNW**
   (~190 surveys/yr, ~1,900 since 2006). The only source touching every current and planned zone.
   Request-gated (`rcinfo@reefcheck.org`) — **send the request now**; turnaround, not code, is the
   long pole.

**Highest-leverage engineering move:** SCCOOS, CeNCOOS, and MBARI M1 are all ERDDAP `tabledap` —
build **one generic ERDDAP client** and you get all three (and future IOOS feeds) from a single
code path.

**Honest caveat:** these are pier/mooring **point sources** — they'll over-satisfy a handful of
zones (SD, Monterey, SB, Newport, Morro) while open-coast zones between piers stay thin. Reef
Check + an in-app diver-clarity-logging feature (EyeOnWater-style — you own the users, you own the
feed) are what eventually spread coverage. For the **paddy** model specifically, BloodyDecks
offshore reports are the only real-world kelp-paddy sighting signal, but they're unstructured HTML
against forum ToS — high-effort, proto-only.

---

## Recommended sequence (burn-down order)

1. **Feed the loop.** Build the generic ERDDAP client; wire SCCOOS + CeNCOOS + MBARI M1; send the
   Reef Check request. Nothing else in this doc pays off until the watchdog can fire.
2. **Cheap consistency fixes.** Point `viz_predict` at corrected SST; reconcile coast-normal
   geometry; add a cross-model upwelling-sign test.
3. **Fix the contradicted water-column internal tide.** Drop "deepest at high water," raise the
   amplitude, or shelve the diurnal swing behind its flag until local data exists.
4. **Deepen the archive.** Backfill residuals so Phase-4 calibration has history, not just 2 days.
5. **Surface model-confidence, not just data-confidence.** Make the confidence dot reflect
   grid-wide interpolated fraction + coefficient-validation tier, not a static ceiling.
6. **Then, and only then, re-tune coefficients** from accumulated residuals (the watchdog already
   suggests the deltas).

---

## Provenance of this document

Synthesized 2026-07-06 from: three code/doc inventory passes over `pipeline/viz_predict`,
`sst_predict`, `viz_column`, the paddy proto, and `pipeline/validation`; an in-repo cross-model
consistency audit; a ground-truth data-source survey; and a peer-reviewed literature check on six
coefficient families. Citations are inline. Numbers (obs counts, coefficient values) were current
at authoring — re-verify against the live code and `watchdog_summary.md` before acting.
