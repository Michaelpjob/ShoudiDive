# How the visibility model works

> What ShouldiDive's "Predicted Visibility" layer actually computes, zone
> by zone, with the real coefficients — plus what we validate it against,
> what's working, and what isn't. Source of truth for every number here:
> `pipeline/viz_predict/config.py`. If the code and this doc disagree, the
> code wins — open an issue.

## TL;DR

The model turns **today's satellite chlorophyll** into a **Secchi-depth
(visibility) estimate in feet**, then nudges that estimate up or down for
the things satellites can't see: storm-driven bottom stir, river and rain
runoff, tidal mixing, kelp, and bottom type. It does this **per zone** —
the California coast is split into 3 latitude bands × 3 distance-from-shore
bands (9 zones), each with its own calibrated constants, because clear
water means something different off La Jolla than off Mendocino.

The output is **three numbers per point** — a low (p10), best (p50), and
high (p90) estimate — plus a **quality flag** saying how much of the answer
is real observation versus model fill. It is a **prediction, not a
measurement**, and it ships with a BETA tag everywhere outside core
California for exactly that reason.

---

## 1. The zones

Visibility is governed by local oceanography, so the model never uses one
global formula. Every cell is classified into a **latitude band** × a
**distance-from-shore band**.

### Latitude bands (California)

| Zone | Boundary | Character |
|---|---|---|
| `norcal` | ≥ 36.0°N (Pt. Conception north) | Cold, upwelling-driven, green; satellite blocked by marine-layer fog for 10–30 days at a stretch |
| `central` | 34.45–36.0°N (Monterey / Big Sur) | Upwelling core; green blooms are the norm |
| `transition` | 33.70–34.45°N (W. Santa Barbara Channel) | Mixing zone between the cold coast and the warm Bight |
| `bight` | < 33.70°N (the SoCal Bight) | Sheltered, less productive, the clearest mainland water |

(Baja, when selected, uses its own `north/mid/south_baja` bands plus a
Pacific-vs-Cortez longitude split — covered in §6.)

### Distance-from-shore bands

| Band | Rule | Why it matters |
|---|---|---|
| `nearshore` | < 5 km from shore **or** depth < 30 m | Runoff, surf-zone stir, and river plumes dominate |
| `islands` | < 10 km from a named Channel Island | Backsides of the islands sit in clean offshore water |
| `offshore` | everything else | Open water; clearest, least disturbed |

That's **9 zones** (`bight_nearshore`, `central_offshore`, …). Each carries
its own chlorophyll-persistence, Secchi, and turbidity constants. The named
Channel Islands are an explicit list of nine (San Miguel, Santa Rosa, Santa
Cruz, Anacapa, San Nicolas, Santa Barbara, San Clemente, Catalina,
Coronados) — a 2026-05-05 fix that stopped bay islets and breakwaters near
San Diego from masquerading as "islands" and dragging the shore estimate
down.

---

## 2. The calculation, end to end

```
 satellite chl-a ──► persistence blend ──► driver adjustment ──► chl estimate
 (today, gap-filled)   (fill gaps with        (11 environmental    (p10/p50/p90)
                        climatology, decay      drivers nudge it)
                        by age)                                       │
                                                                      ▼
                                              chl → Secchi  (power law, per zone)
                                                                      │
                                                                      ▼
                                              turbidity penalties (subtract feet
                                              for swell / runoff / rivers / kelp
                                              / substrate / tides)
                                                                      │
                                                                      ▼
                                              clip, score 0–100, label
                                              Poor / Fair / Good / V.Good / Excellent
```

### Step 1 — Chlorophyll, gap-filled and adjusted

Satellites miss days (clouds, fog, orbit gaps). So the chl input is built
in three stages:

1. **Climatology anchor** — the long-term seasonal average chl for that
   zone and month, the fallback floor.
2. **Persistence blend** — today's real observation, blended toward
   climatology as it ages, on a log scale:
   `weight = exp(−age_days / τ_zone)`. The decay constant **τ** is
   zone-specific (5–8 days; NorCal nearshore runs at 7 days precisely
   because fog leaves 10–30-day satellite gaps there).
3. **Driver adjustment** — eleven environmental signals nudge the chl
   estimate up or down (log-additive), each scaled by a per-zone
   coefficient. For `central_nearshore`:

   | Driver | Coef | Effect |
   |---|---|---|
   | seasonal | +0.40 | this month vs the annual mean |
   | swell | +0.30 | wave-driven bottom stir lifts sediment/chl |
   | river | +0.30 | discharge anomaly vs normal (decays 8 km offshore) |
   | precip | +0.20 | 7-day rain runoff (decays 5 km offshore) |
   | exposure | +0.20 | wind + swell aimed at the shore |
   | upwell | +0.18 | wind **and** cold SST together (coupled) |
   | substrate | +0.15 | sandy bottom + stir |
   | tide | +0.10 | tidal range in shallow nearshore water |
   | trend | +0.08 | 3-day SST cooling rate |
   | sst | −0.06 | warm anomaly = less bloom |
   | cloud | −0.08 | persistent cloud cover |

   Upwelling is deliberately **coupled** (v3.5): it only fires when wind
   anomaly **and** cold-SST anomaly are both positive — wind alone was
   producing false upwelling signals.

This yields a **distribution**, not a point: `p50 = exp(log_chl)`, with
`p10/p90 = exp(log_chl ∓ 1.28·σ)`. The spread **σ** is zone-specific
(0.28–0.65 on a log scale) and **grows with observation age** — a 10-day-old
estimate is honestly wider than a fresh one. When a genuinely fresh
observation exists, σ collapses to 0.05 and the band tightens.

### Step 2 — Chlorophyll → Secchi depth

A power law per zone: `secchi_m = a · chl^(−b)`. The exponent **b**
(0.28–0.32) is fixed at literature values; the multiplier **a** is the
**calibrated knob** the validation loop tunes. Higher `a` = clearer baseline
water:

| Zone | a | Typical clear-day vis |
|---|---|---|
| `central_nearshore` | 3.5 | ~18 ft (green upwelling coast) |
| `bight_nearshore` | 6.5 | sheltered SoCal mainland |
| `bight_islands` | 8.5 | Catalina/SCI backsides reach 50+ ft |
| `bight_offshore` | 9.0 | clearest CA water, 45+ ft |
| `south_baja_offshore` | 17.0 | 100+ ft on a calm day at Cabo |

Worked example: `central_nearshore`, chl = 2.0 mg/m³ →
`3.5 × 2.0^(−0.28) ≈ 5.5 m ≈ 18 ft` → **Fair/Good**.

### Step 3 — Turbidity penalties

Satellites see the surface optical layer, not the things that wreck a dive.
So the model **subtracts feet** for in-water disturbance, per zone:

| Penalty | What it captures | `central_nearshore` |
|---|---|---|
| swell | bottom stir (wave orbital velocity × depth) | up to −8 m |
| river | discharge plume near a river mouth | up to −5 m |
| runoff | 7-day rain × distance-to-river-mouth | up to −4 m |
| substrate | sandy bottom resuspending | up to −2.5 m |
| kelp | canopy debris **only when waves are present** | up to −2 m |
| tide | tidal mixing in shallow nearshore water | up to −1.5 m |

Offshore zones get **zero** turbidity penalty — there's no bottom to stir
and no runoff that far out. The kelp penalty is conditional (v2): kelp
forests *filter* water on calm days (no penalty) and only shed debris when
swell stirs the canopy.

### Step 4 — Score and label

The Secchi depth (clipped to 3.3–115 ft) maps through a piecewise-linear
curve to a 0–100 score and a five-step label:

| Feet | Label | Color | What it feels like |
|---|---|---|---|
| 0–10 | **Poor** | burnt orange | silty / blown out |
| 10–20 | **Fair** | yellow-green | diveable but washed out |
| 20–30 | **Good** | green | typical CA kelp diving |
| 30–50 | **Very good** | cyan | clean blue water |
| 50+ | **Excellent** | deep navy | once-a-year clarity |

### The quality flag

Every cell also gets a **provenance code** so the UI can be honest about how
much is real:

| Code | Meaning |
|---|---|
| `OBSERVED_1D` | fresh satellite chl today |
| `OBSERVED_3D` | real chl ≤ 3 days old |
| `INTERPOLATED` | gap-filled from neighbors |
| `PREDICTED_HIGH/MED/LOW_CONF` | model fill, 5 / 10 / >10 days since real data |
| `CLIMATOLOGY_ONLY` | no recent observation anywhere — seasonal average only |

When the satellite chl is missing it falls back 1-day → 2-day → 3-day
composite, then to climatology, downgrading the quality flag at each step.

---

## 3. What feeds it

MUR SST · VIIRS chlorophyll-a · HRRR + GFS wind (7-day history) · WaveWatch
III swell (3-day max) · CPC/IMERG precipitation · USGS river discharge ·
NOAA CO-OPS tides · MODIS-Aqua climatology. Recomputed **once daily** (the
satellite products update on that cadence; wind refreshes hourly for other
layers). An optional second clarity signal (satellite Kd₄₉₀, converted to
Secchi via the Poole-Atkins factor 1.7) blends in when it's fresher than the
chl — with a guard that stops a stale Kd reading from overriding a fresh
bloom.

---

## 4. How we validate it

The model is scored against **real diver and instrument observations**, on
an hourly ingest + daily scoring loop.

### Ground truth

| Source | What it gives | Confidence |
|---|---|---|
| Dive shops (Just Get Wet, DiveViz) | **visibility** reports (the primary target) | 0.85 |
| CDIP buoys (6 CA stations) | swell + SST (validates those *drivers*, not vis) | 0.95 |
| NDBC buoys | swell + SST | 0.95 |

Each observation is matched to the **nearest model grid cell within 25 km**,
on the same day, and the residual `predicted_p50 − observed` is recorded.

### The metrics, per zone

- **Bias** — mean residual. Positive = the model over-predicts.
- **RMSE / MAE** — typical error magnitude in feet.
- **Calibration** — fraction of observations that land inside the p10–p90
  band (target ≈ 80%).
- **Pearson r** — does the model move the right direction when reality does.

### The watchdog (automated accuracy gates)

A daily job flags — but never silently changes — accuracy regressions:

| Rule | Trips when | Min sample |
|---|---|---|
| Bias | \|bias\| > 5 ft | 30 obs |
| Calibration | <60% or >95% inside the band | 30 obs |
| Correlation | r < 0.30 | 50 obs |
| Data flow | <50 observations ingested in 24 h | — |
| Feed health | a critical input feed goes dark | — |

When it trips, it opens a GitHub issue with a suggested coefficient
adjustment. A separate **regression gate** blocks any deploy where a zone's
RMSE jumps >20% above the promoted baseline.

---

## 5. What works, what doesn't (honest scorecard)

**Working well:**

- **The core chl → Secchi physics.** Where we have observations, the
  *direction* is right and the band is honest. The latest scored zone,
  `bight_nearshore`, shows **Pearson r = 0.94** and **100% calibration**
  (every observation fell inside the predicted band) over its recent
  sample.
- **Graceful degradation.** Persistence + the quality flags mean a foggy
  week doesn't break the map; it widens the band and downgrades the
  provenance label, which is the honest behavior.
- **Zone awareness.** Splitting the coast fixed whole classes of error
  (San Diego mis-classified as "island" water; Baja's upwelling Pacific
  shelf read as Cortez-clear).

**Known weak spots — all tagged BETA in-app:**

- **Thin ground truth right now.** The current scored sample is *tiny*
  (`bight_nearshore` n = 4) and the ingest pipeline is running **below its
  50-obs/day floor** (~19/day lately) — the watchdog is flagging exactly
  this. Most zones don't yet have the **30+ observations** needed to trust a
  coefficient re-tune. More dive-shop and community sources are the top
  priority.
- **A persistent cool bias nearshore.** The same recent sample shows
  **bias ≈ −5.5 ft** — the model reads a touch *pessimistic* in the SoCal
  Bight. It's inside the ±5 ft gate (barely) and we'd want a bigger sample
  before adjusting, but it's real and worth watching.
- **NorCal / PNW / Tropical are extrapolations.** Coefficients there are
  ported from California, not locally validated (confidence: NorCal/PNW/
  Tropical = "inferred", Baja = "modeled", core CA = "observed"). NorCal
  especially: marine-layer fog blocks the satellite for weeks, so the map
  leans hard on persistence and climatology.
- **It's a surface model.** The headline number describes the **surface
  optical layer** — which is why the new water-column feature exists, to
  model the vis cliff below the thermocline separately.

**Out of scope by design:** it describes water, it does not tell anyone
whether to dive. No go/no-go advice, no safety calls.

---

## 6. Regional notes

- **Baja** uses its own latitude bands plus a **Pacific-vs-Cortez longitude
  split**: the peninsula spine is approximated as a line, and Pacific-side
  cells (cold upwelling shelf — Vizcaíno, Magdalena) are relabeled to the
  low-clarity `north_baja` coefficients instead of inheriting the clear
  Cortez-side values. Without this, Punta Abreojos read 70+ ft when it's
  really 25–40.
- **The Secchi ceiling** was raised to 115 ft (from 82 ft) in 2026-05 so
  Cabo Pulmo / Espíritu Santo can report the 100+ ft they actually hit.

---

## 7. Where the numbers live

- Zones + all coefficients: `pipeline/viz_predict/config.py`
- The math: `pipeline/viz_predict/{model,features,visibility,predict}.py`
- Driver into rasters: `pipeline/fetch_visibility.py`
- Validation: `pipeline/validation/{score,watchdog}.py`, `ingest/`
- Current scorecard: `pipeline/validation/data/per_zone_metrics.json`
- Per-layer confidence shown in-app: `src/lib/confidence.js`
