# Canopy-Dynamics Shedding Model — Spec & Build Plan

**Status:** design, pre-build (2026-06-30). Supersedes the current stateless
detachment formula (`detach = BASE + K_WAVE·(Hs−2.5)² + K_WARM·dose²`).
**Author context:** written after two literature passes — a 5-strand temperature
pass and a 1 comprehensive wave-biomechanics pass (citations in §9).

---

## 1. Why — the core idea

Shedding **depletes** the kelp. The current model recomputes shedding from each
day's weather independently, with no memory — so it cannot represent the most
important fact the operator flagged:

> *"If peak summer, everything's shed, it doesn't matter how hot it is or how
> much wind there is."*

A bed is a **finite, weakening reservoir**. Forcing (heat, wind, swell) sets the
**rate** of shedding; the bed's remaining sheddable stock sets the **ceiling**.
Paddy output is therefore **path-dependent**: the same October storm produces a
big pulse on a bed that's been cool/calm all summer (full reservoir) and almost
nothing on a bed that already shed itself out in a hot, stormy summer.

This model makes the canopy a **stateful seasonal simulation**: each bed carries
a biomass state that grows, weakens, and is drained by shedding events through
the whole season. "General health" = the state of that reservoir.

## 2. State

Per bed `b`, two pools (a third is bookkeeping):

| Symbol | Meaning | Units |
|---|---|---|
| `R(b,t)` | **Robust** attached canopy — healthy, resists shedding | km² canopy area |
| `V(b,t)` | **Vulnerable** canopy — senescent / heat-weakened / fouled, ripe to shed | km² canopy area |
| `K(b)`   | carrying capacity (max canopy this bed supports) | km² canopy area |

**Units decision (recommended): real Landsat-observed canopy AREA (km²), split
into a robust/vulnerable health state.** Rationale:
- It is the honest answer to "the *quantity* of shedding when it happens" — the
  shedding flux is in real canopy-area-shed/day, not a unitless index or a fake
  absolute paddy count (we explicitly rejected fake counts — see the band→density
  decision).
- `K(b)` comes free from the SBC LTER Landsat product we already read
  (per-bed recent-peak canopy area), so the reservoir is anchored to observation,
  not invented.
- Output still presents as the **Hobday-anchored paddy density** (~1–3/km²) the
  UI now uses; canopy-area-shed maps to that, so we never claim a precise count.

## 3. Dynamics (daily difference equations, per bed)

```
# 1. GROWTH  — robust canopy regrows toward capacity, gated by environment
grow      = r_g · R · (1 − (R+V)/K) · g_env(SST)      # logistic, nutrient/temp-gated

# 2. WEAKENING  — robust ages into vulnerable; heat + senescence + fouling speed it
weaken    = w0 · R · (1 + a_warm·warmdose + a_age·age + a_foul·foul)

# 3. SHEDDING  — vulnerable pool drained by wave forcing; THE paddy flux
shed      = s0 · V · wave_dose(b,t) · (1 + a_int·warmdose)   # warm×wave interaction
                                                              # bounded: shed ≤ V

# 4. IN-SITU LOSS — vulnerable that decomposes/sinks in place (not findable paddies)
insitu    = d0 · V

# update
R(t+1)    = R + grow − weaken
V(t+1)    = V + weaken − shed − insitu
paddies(b,t) = shed          # <-- seeds the existing drift/fate Monte Carlo
```

**Rate-by-rate drivers + anchors:**

- `g_env(SST)` — nutrient/temperature growth gate. High when SST < ~16 °C
  (nitrate-replete), falling to ~0 by ~20 °C (nitrate ≈ 0 above 14.5 °C,
  Snyder 2020 / Konotchick 2012). `r_g` ≈ Macrocystis max growth ~2 %/day
  (Reed 2008; Rodriguez 2013).
- `weaken` — baseline senescence `w0` from frond turnover (canopy turns over
  6–7×/yr → `w0` ~ 0.02–0.03/day; Rodriguez 2013). `a_warm·warmdose`: heat
  accelerates senescence/fouling (the 6-week thermal dose already built).
  `a_age`: senescence clock since the spring reset (older canopy weaker;
  Burnett & Koehl 2019). `a_foul`: epibiont load (optional v2).
- `shed` — `wave_dose(b,t)` is the per-bed, period-weighted, direction-exposed,
  duration-integrated wave energy (§4). `s0` calibrated to Seymour 1989 storm
  mortality (§9). `(1 + a_int·warmdose)` is the **warm×wave interaction**:
  heat-weakened tissue sheds at lower wave energy (Simonson 2015: 40–70 %
  strength loss at 21 °C). `shed` is clamped to `≤ V` (can't shed what's gone) —
  this clamp is the whole point.
- `insitu` — fraction of vulnerable canopy that decomposes/sinks locally rather
  than drifting as a findable paddy (Krumhansl & Scheibling 2012).

## 4. Wave-energy / exposure sub-model (drives `wave_dose`)

```
wave_dose(b,t) = Σ over the trailing storm window of:
                   exposure(b, Dp) · max(0, Hs²·Tp − E_crit)
```

- **Energy, not height:** `Hs²·Tp` (wave energy/momentum flux). Long-period
  groundswell penetrates deeper (L ∝ T²) and throws bigger breaking rollers at
  the surface canopy → more damaging than a short windswell of equal height
  (Seymour 1989; standard wave-energy flux). Confidence: medium-high on form.
- **Per-bed directional exposure `exposure(b, Dp)` ∈ [0,1]:** precomputed by
  ray-casting each bed against `land.geojson` in ~36 compass directions; a
  direction with open-ocean fetch = exposed, blocked by land/island = sheltered.
  Incident swell from bearing `Dp` (with angular spread ±σ ≈ 20–30°) loads only
  the beds open to that bearing. This is the "angle of the swell" gradient.
  Island shadowing produced 2.8→6.7 m Hs over 150 km in ONE storm (Seymour
  1989 Fig 3); fetch/exposure indices are standard (Burrows; NOAA WEMo).
  Confidence: high; largest SCB spatial signal. New module `exposure.py`.
- **Duration / fatigue:** integrate energy-above-threshold over the storm window
  (not just the daily peak). Macroalgal failure is cumulative fatigue —
  individual waves are too weak; cracks grow under repeated loading + an
  entanglement cascade (Mach 2009/2011). A sustained moderate storm out-sheds a
  brief big set. Confidence: high.
- **Threshold `E_crit`:** calibrated to the Seymour mortality anchors (§9), not a
  textbook value (no clean single dislodgement velocity exists for Macrocystis).

## 5. Observation correction (the Landsat anchor)

A months-long per-bed integration will drift. Anchor it to reality:

- Between satellite passes, physics (§3) propagates `R + V`.
- When a Landsat canopy-area reading `C_obs(b)` lands, **rescale**:
  `factor = C_obs / (R+V)`; `R,V *= factor` (preserve the health ratio) — a
  simple multiplicative nudge (a 1-state Kalman-style correction; can weight by
  obs confidence). If a canopy *condition* index is available (Bell 2015 RSE),
  also shift the R:V split.
- This is the difference between a plausible simulation and an *observed* one:
  physics fills the gaps between passes; the satellite re-grounds the standing
  stock and prevents accumulation error.

**Data:** use the SBC LTER "Kelp from Landsat" **time series** (the same `.nc` we
already read for the snapshot — we currently ignore its temporal dimension).
Cadence is ~quarterly historically, denser recently; interpolate between, anchor
on each real pass.

## 6. Spin-up

Start each bed's simulation at the **post-winter canopy minimum** (~Jan–Mar, the
seasonal reset when storms strip the old canopy), initialized from the first
Landsat reading in the window, and integrate forward through the real
SST + wave history to "now." ~4–6 months of history. The seasonal arc
(spring regrowth → summer weakening → fall senescence/shedding) then *emerges*
from the dynamics instead of being a hand-coded calendar curve.

## 7. Output → existing drift/fate machinery

`paddies(b,t) = shed(b,t)` (canopy-area-shed/day per bed) **replaces** the current
per-bed detachment weight that seeds the drift Monte Carlo. Everything downstream
is unchanged: particles seed from each bed weighted by `shed`, drift through
currents+Stokes+windage, beach/sink/float, and the floating survivors form the
density → opportunity → HDR field. Output presents as the Hobday-anchored
**density** read-out already shipped.

## 8. What stays / what changes

| Component | Disposition |
|---|---|
| Drift (advect, Stokes, windage, diffusion) | unchanged |
| Fate (beach / epibiont-sink / quality) | unchanged (sink temps already lit-correct) |
| Findability / opportunity / HDR / cones | unchanged (re-seeded by the new flux) |
| `detachment.py` | **replaced** by the canopy simulation as the seeder |
| 6-week thermal dose (`forcing.ThermalHistory`) | **reused + extended** to season length |
| Wave term | **replaced** by the per-bed exposure-weighted energy dose |
| `kelp_source.py` | **extended** to read the Landsat time series, not just the snapshot |

## 9. Calibration plan (literature → form, data → gains)

- **Shed rate `s0`, `E_crit`:** anchor to Seymour 1989 storm mortality — benign
  2–9 %, Hs 3.8 m/15 s → 31–37 %, Hs 6.7 m/13 s → 65–94 % (fraction of
  vulnerable pool shed per storm).
- **Growth `r_g`, gate `g_env`, senescence `w0`:** fit so the simulated per-bed
  canopy area reproduces the **observed Landsat seasonal cycle** (the phenology
  is *fit*, not assumed). Macrocystis growth ~2 %/day, turnover 6–7×/yr.
- **Warm coefficients (`a_warm`, `a_int`):** thresholds from the temperature pass
  (stress 20 °C, cliff 23–24 °C, Cavanaugh 2019); strength-loss magnitude
  (Simonson 2015, 40–70 % @21 °C) ported cautiously (Atlantic kelp → tunable).
- **Output density scale:** Hobday SCB raft density (~1–3/km²) maps canopy-area-
  shed → paddy density (already in the UI).
- **Catch-report skill:** *validation only*, not primary fit — 20 reports are
  selection-biased (the lesson from the warm recalibration).

**Key sources:** Seymour, Tegner, Dayton & Parnell 1989 (ECSS 28:277);
Cavanaugh et al. 2011/2019; Snyder 2020; Konotchick 2012; Rodriguez et al. 2013;
Simonson, Scheibling & Metaxas 2015 (MEPS 537:89); Mach 2009/2011;
Burnett & Koehl 2019; Reed et al. 2011; Bell et al. 2015; Hobday 2000;
Gaylord & Denny 1997; Krumhansl & Scheibling 2012; Burrows / NOAA WEMo.

## 10. Validation plan

1. **Mass balance:** no negative pools; `R+V ≤ K`; conservation across flows.
2. **Seasonal arc:** simulated per-bed canopy area tracks the observed Landsat
   seasonal cycle (the core fit).
3. **Depletion behaviour (the operator's case):** synthetic hot+stormy summer →
   `V` drawn down → a late-October event sheds little; cool+calm summer → `V`
   full → the same October event throws a big pulse. Path-dependence must appear.
4. **Saturation:** holding `V` fixed and ramping forcing → shedding saturates
   (can't exceed `V`).
5. **Skill:** catch-report percentile (secondary) holds vs the current model.
6. **Before/after:** side-by-side density field vs current, on the live period
   + a historical warm-spell + a storm.

## 11. Build plan — staged, each layer validated, dev-first

Each phase ships to dev and is validated before the next. One feat branch per
phase (so a finished phase isn't blocked by an unfinished one).

- **Phase 0 — `exposure.py` (per-bed directional exposure).** Ray-cast beds vs
  `land.geojson`, cache the per-bed open-ocean window. Validate against known
  geography (Catalina backside exposed to S/SW, lee sheltered). *Self-contained,
  no behaviour change yet — lowest risk, immediately inspectable.*
- **Phase 1 — wave-energy dose.** Replace `(Hs−2.5)²` with the exposure-weighted,
  period-weighted (`Hs²·Tp`), duration-integrated `wave_dose`. Calibrate `E_crit`
  to Seymour. Still stateless (no reservoir yet) — validates the "angle + period
  + duration" wave physics in isolation.
- **Phase 2 — the reservoir (`canopy.py`).** The two-pool R/V state + grow /
  weaken / shed / insitu dynamics, integrated over the season from the spin-up.
  `shed` (clamped to V) becomes the seeder. This is the depletion core — validate
  the seasonal arc + depletion behaviour (§10.2–10.4).
- **Phase 3 — interactions + senescence.** Warm×wave (`a_int`) and the
  warm-accelerated weakening (`a_warm`, `a_age`). Validate winter-vs-fall and the
  hot-summer-exhausts-the-reservoir case.
- **Phase 4 — Landsat time-series anchor.** Read the temporal dimension; add the
  observation-correction (§5). This is the "observed, not inferred" upgrade —
  removes the fixed-snapshot limitation entirely.

Phases 0–1 are tractable and independently valuable (they answer "angle &
duration" even without the reservoir). Phases 2–4 are the architecture shift.

## 12. Risks / open questions

- **Macrocystis-specific coefficients are sparse** — several magnitudes ported
  from Atlantic Laminariales (Simonson) or general kelp; treat as tunable, fit to
  Landsat + Seymour, don't claim precision.
- **Landsat cadence vs daily sim** — quarterly anchors with daily physics between;
  interpolation + the correction step handle it, but spin-up before the first
  in-window pass is weakly constrained.
- **Compute** — 99 beds × ~180 days × simple difference eqs = trivial; the wave
  history fetch is the main added cost (already have the SST fetch pattern).
- **Spin-up sensitivity** — initial R:V split at the winter reset; mitigated by
  the first Landsat anchor pulling it to reality quickly.
- **Scope discipline** — this is a real engine, not a tuned term. Ship per phase,
  keep each falsifiable, never let the simulation's plausibility substitute for
  the Landsat anchor.

## 13. Open decision for sign-off

- **Units:** recommended = Landsat-anchored canopy **area (km²)** with an R/V
  health split (real relative quantity, no fake absolute counts). Alternative =
  normalized 0–1 "fullness" per bed (simpler, relative-only, loses the
  area-grounded quantity). *Recommendation: area-anchored.*
- **Build start:** recommended = Phase 0 (`exposure.py`) — lowest risk, immediately
  inspectable, and the "angle of swell" piece the operator most wants.
