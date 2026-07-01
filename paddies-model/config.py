"""Tunables for the standalone kelp-paddy positioning prototype.

Full lifecycle: detachment (amount) -> drift (transport) -> fate
(beach OR sink) -> the floating survivors = where to find paddies.
Self-contained; reads only public data (Open-Meteo) + ShoudiDive's
published land.geojson (read-only). Touches nothing in prod.
"""

# Southern California Bight + offshore drift room (paddies travel far over
# a week, so the field is padded south/west of the sources).
# Open-Meteo trailing hindcast drives the drift (it serves past_days);
# coarse but TIME-VARYING. ShoudiDive (forecast-only) can't do past data.
FIELD_BBOX = dict(lat_min=31.0, lat_max=34.8, lng_min=-121.5, lng_max=-116.8)
GRID_STEP_DEG = 0.3          # Open-Meteo can't be queried at finer than this

# Trailing window: kelp that broke loose 0..N days ago.
PAST_DAYS = 21              # was 7 — Hobday 2000: SCB Macrocystis rafts persist weeks-to-months (max 63-109 d), not days
FORECAST_DAYS = 3            # forecast forcing for the +days frames (margin over +2)
# Release cohorts spanning the ~3-week horizon (subsampled past ~1 wk). Age 0 stays
# for the detachment/abundance accounting + the beds' "shedding now" marker, but a
# freshly-shed paddy still on its source forest is NOT a findable open-water raft,
# so the drift only SEEDS findable particles from MIN_FINDABLE_AGE_DAYS on (the
# age-0 layer otherwise piles up on the biggest beds -- Catalina worst -- into an
# over-large at-source blob; gating it tightens the field, primary patch 56->62%).
RELEASE_AGES_DAYS = [0, 1, 2, 3, 4, 5, 7, 9, 11, 14, 17, 21]
MIN_FINDABLE_AGE_DAYS = 0   # real per-cell canopy seeding spreads sheds across the actual beds (no hand-centroid pile), so no gate needed -- keeps nearshore kelp in

# --- Time slider: render the paddy field at each "as-of" day --------------
# Past is observation-backed (HFRNet 6 km currents + Open-Meteo hindcast);
# the future is FORECAST only (no future radar, drift error compounds) so we
# cap it at +2 d and flag it lower-confidence rather than fake a +7 d position.
TIMELINE_OFFSETS_DAYS = [-3, -2, -1, 0, 1, 2]
PARTICLES_PER_RELEASE = 4    # per kelp cell per age (~99 real-canopy cells now, vs 24 hand beds)

DT_HOURS = 1.0
USE_WIND = True

# --- Multi-driver transport: v_kelp = current + Stokes + windage + diffusion
CURRENT_BLEND_RTOFS = 0.65   # ocean-model backbone (persistent California Current)
CURRENT_BLEND_HFR = 0.35     # HFRNet surface-obs nearshore detail
WINDAGE_ALPHA = 0.02         # floating kelp drifts at ~2% of wind (leeway)
STOKES_COEF = 1.0            # multiplier on textbook deep-water surface Stokes
DIFFUSION_K_M2S = 5.0        # horizontal eddy diffusivity (sub-grid dispersion)

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_DIR_TOWARD = True

# --- Land stranding (fate = wash up) --------------------------------------
LAND_MASK_STEP_DEG = 0.01

# --- Detachment: amount of kelp entering the system -----------------------
# Two mechanistically-distinct drivers, recalibrated 2026-06-30 against the
# SCB kelp-temperature literature (5-strand deep-research pass):
#   detach = BASE_SHED
#          + K_WAVE * relu(Hs - HS0)^HS_POW            # SWELL: instantaneous
#          + K_WARM * dose^WARM_POW                     # WARM: cumulative dose
#
# WHY the asymmetry: storms rip kelp loose ON the day (instantaneous), but
# warm-water canopy loss is CUMULATIVE over ~weeks (N-reserve buffer ~2-3 wk
# Gerard 1982; frond turnover 1-3 mo Reed/Rodriguez; staggered MHW minima
# Cavanaugh 2019). `dose` = trailing-window MEAN of max(0, SST - T0_C) in degC
# (a degree-week-style thermal dose), computed by forcing.ThermalHistory.
BASE_SHED = 0.10
# Legacy stateless swell term (HS0_M/HS_POW/K_WAVE) is SUPERSEDED by the
# canopy-dynamics wave-energy DOSE below (Phase 1): per-bed directional
# exposure x wave ENERGY (Hs^2*Tp) integrated over the storm window. Kept only
# for the model_sweep fallback path. See wave.py + exposure.py.
HS0_M = 2.5
HS_POW = 2.0
K_WAVE = 1.00
# --- Wave-energy dose (canopy-dynamics P1): Hs^2*Tp x exposure, windowed ------
# Energy/momentum flux ~ Hs^2*Tp rewards long-period groundswell (deeper orbital
# penetration L~T^2 + bigger breaking rollers at the surface canopy), which
# dominates SCB Macrocystis loss (Seymour et al. 1989). Per-bed `exposure`
# (exposure.py) makes a swell only load the beds open to its direction; the
# window integrates the storm (fatigue: individual waves are too weak, failure
# is cumulative crack-growth + entanglement cascade, Mach 2009/2011).
WAVE_E_CRIT = 100.0     # energy threshold Hs^2*Tp (~Hs 2.9 m @ 12 s; destructive onset ~2.5-3 m)
K_WAVE_E = 0.04         # gain (rough; Seymour-1989 storm-mortality calibration lands in the P2 reservoir)
WAVE_WINDOW_DAYS = 5    # storm / fatigue integration window (days)
# Warm term. Threshold 20 degC = field-effective stress onset (Cavanaugh 2019;
# nitrate ~0 above ~14.5 degC so SST is a defensible nutrient proxy, Snyder 2020
# / Konotchick 2012 NO3=-5.8T+81.7). Convex (POW=2) to match the observed
# threshold-then-cliff: gentle 20-22 degC, steep toward the ~23-24 degC near-
# total-loss cliff. K_WARM gain set ~1.3:1 wave:warm SCB-wide (Bell 2015:
# waves 37% vs nitrate 29% of SCB sites) -- but per-bed SST sampling lets warmth
# dominate at the sheltered island beds (Catalina/Clemente), which is correct.
# Gain to be finalized by the catch-report skill sweep (model_sweep.py).
T0_C = 20.0
WARM_POW = 2.0
K_WARM = 0.13
WARM_DOSE_WINDOW_DAYS = 42   # ~6-week trailing thermal-dose window
# ABUND_SCALE recentred 1.20 -> 0.50 for the recalibrated (lower-magnitude,
# more peaked) terms so the band still spans: a calm/mild week reads "Low"
# (idx ~18), a storm/warm-spell climbs to Moderate/High, a sustained 24 degC
# MHW dose reaches Extreme. index = 100*(1-exp(-mean_detach/ABUND_SCALE)).
# NOTE/limitation: warm water ALSO shrinks the standing canopy, but the kelp
# SOURCE here is a fixed Landsat snapshot (kelp_source.py) -- so in a long warm
# spell the model can over-credit a source that is actually dying back. A
# dynamic canopy-decay feedback is a separate follow-up.
ABUND_SCALE = 0.50

# --- Sinking: epibiont-ballast model --------------------------------------
# Mechanism (Graiff/Rothausler 2016, "Epibiont load causes sinking of viable
# kelp rafts"): bryozoan fouling accretes until ballast reaches ~40% of raft
# biomass and the still-PHYSIOLOGICALLY-VIABLE raft sinks. Warm water grows the
# foulers faster -> earlier sinking; it does NOT thermally kill the kelp at
# SoCal summer SST. Hard physiological collapse only above ~24 C (Rothausler
# 2009: >24 C sank in ~5 days). Calibrated to observed persistence: cool
# ~15-16 C median ~39 d, warm ~20 C ~27 d, max tail ~80 d (Hobday 2000 SCB max
# 63-109 d). 19-21 C June water is the stressed-but-survivable shoulder, NOT a
# kill zone. survival(age) = exp(-integral hazard dt).
SINK_BASE_PER_DAY = 0.010    # base fouling/senescence hazard (cool-water long e-fold)
SINK_AGE_TAU_DAYS = 25.0     # bryozoan load accretes over weeks (hazard grows with age)
SINK_WARM_PER_C = 0.15       # MULTIPLIER on the fouling rate: +15%/degC above SINK_T0 (faster bryozoan growth)
SINK_T0_C = 16.0             # fouling accelerates above this; gentle through 19-21 C
SINK_HOT_C = 24.0            # physiological-collapse onset (Rothausler: >24 C sinks in days)
SINK_HOT_PER_C = 0.50        # steep extra hazard/day per degC above SINK_HOT_C
SINK_DEAD_BELOW = 0.12       # survival below this -> counted as sunk

# --- Findability surface (currently-floating paddies only) ----------------
DENSITY_STEP_DEG = 0.04
DENSITY_SIGMA_KM = 8.0
DENSITY_REF = 75.0           # FIXED display saturation (~swell-scenario peak)
DENSITY_GAMMA = 0.5

# --- Hotspots: ranked "go here" waypoints ---------------------------------
N_HOTSPOTS = 6
HOTSPOT_MIN_FRAC = 0.30      # candidate must be >= this fraction of peak
HOTSPOT_NMS_KM = 18.0        # min separation between hotspots
OFFSHORE_MIN_KM = 3.0        # hotspots must be open water, not the surf line
REACHABLE_NM = 40.0

# Launch points to measure distance/bearing from (lat, lng).
LAUNCHES = {
    "San Diego (Mission Bay)": (32.77, -117.25),
    "Dana Point": (33.46, -117.70),
    "Oxnard / Channel Islands": (34.16, -119.22),
    "Santa Barbara": (34.40, -119.69),
}
DEFAULT_LAUNCH = "San Diego (Mission Bay)"

# --- Rough count anchor (order-of-magnitude only) -------------------------
# At the +2 m swell reference, treat the floating amount as ~Hobday-high
# density over the fishable offshore area. Everything else scales linearly.
HOBDAY_DENSITY_PER_KM2 = 1.0   # count the FISHABLE/findable rafts (~1/km2, ~1/3 of Hobday's ~3/km2 all-raft density; the big fish-holding paddies are the subset worth counting). Order-of-magnitude only.
FISHABLE_AREA_KM2 = 9000.0
FLOAT_AMOUNT_REF = 1955.0    # = +2m-swell floating amount -> Hobday-high anchor

# --- Offshore focus -------------------------------------------------------
# Inshore paddies rarely hold fish, so the OPPORTUNITY (fish-holding) is
# weighted to 0 near the beach and ramps to full offshore. (The kelp drift
# itself is unaffected — kelp is still there inshore, it just holds no fish.)
# Distance measured from the MAINLAND coast only (island/channel water
# counts as offshore). Fishable paddy grounds are well off the beach.
OFFSHORE_NEAR_KM = 6.0       # ~3.2 nm from the mainland — suppressed inside
OFFSHORE_FAR_KM = 17.0       # ~9.2 nm (9-Mile-Bank class) — full fish weight beyond
# Real reports: productive paddies start ~5 mi out, the 9-Mile Bank (~9 nm) is
# the workhorse — so full weight by ~9 nm, not ~13. The far-offshore prize is
# unchanged; this just stops under-weighting the genuinely-fishable 5-9 nm band.

# --- Shore credit: cautiously let nearshore (shore-shed) paddies count ------
# The offshore ramp above ZEROES fish-weight within OFFSHORE_NEAR_KM of the
# mainland, which erases the real shore-shed kelp (Palos Verdes, Laguna, the
# SD mainland) that anglers DO work when paddies hold up close. Rather than
# zero, floor the inshore ramp at SHORE_CREDIT so shore paddies carry a SMALL,
# capped weight. This is deliberately cautious: 0.0 reproduces the old
# fully-suppressed behavior; start low and only dial up if the catch-report
# skill (model_sweep.py) holds or improves. Dev-only while we calibrate.
SHORE_CREDIT = 0.15

# --- Shore (mainland) beds: EVIDENCE-BASED source treatment ------------------
# Recut 2026-07-01 from a 103-agent deep-research adjudication of SCB mainland-
# vs-island beds (Hobday 2000; Seymour et al. 1988/1989 Point Loma; Leichter
# 2023; Cavanaugh/Bell Landsat-detection studies). The prior flat
# SHORE_SOURCE_BOOST=1.4 CONFLATED two mechanistically-distinct things the
# research says must be split, and applied them YEAR-ROUND — over-crediting
# mainland beds in calm summer, when the evidence says mainland detachment is
# WINTER-storm-dominated (Hobday: 23% loss of attached plants in winter vs 8%
# in fall). The 1.4 constant itself was validated by NO surviving source.
# So the flat boost is retired and split into its two real, separable parts:
#
#   (1) STATIC detection bias  -> SHORE_DETECT_CORR (below). Landsat 30 m
#       systematically under-counts the nearshore mainland fringe (misses
#       28-75% of kelp within ~30 m of shore; ~40% under-count vs UAV at
#       Saunders Reef; cannot see stands <15% of a 900 m^2 pixel). This is a
#       real, ~time-invariant measurement gap -> a flat multiplier on mainland
#       canopy AREA is the DEFENSIBLE form. Magnitude 1.2-1.4x is the modeler's
#       choice WITHIN the supported direction; no source pins the exact value,
#       so we sit conservative in-band at 1.30.
#   (2) SEASONAL wave-driven detachment -> CANOPY_SHORE_WAVE_GAIN (in the
#       canopy reservoir, config below). Mainland winter mortality is entangle-
#       ment-driven and SHALLOWEST-highest (Seymour Point Loma Jan-1988 storm:
#       94% at 12 m, 69% at 15 m, 65% at 18 m -> a shallow-fringe cascade), so
#       mainland beds shed MORE per unit wave energy. Coupling it to wave dose
#       makes it winter-weighted automatically (big swells = winter) and ~zero
#       in a calm summer, instead of a false year-round inflation.
#
# SHORE_SOURCE_BOOST kept defined (=1.0, NEUTRAL) only so the legacy
# stateless/model_sweep fallback path still imports; the live reservoir path in
# drift.py no longer uses it.
SHORE_SOURCE_BOOST = 1.0        # DEPRECATED/neutral — see SHORE_DETECT_CORR + CANOPY_SHORE_WAVE_GAIN
SHORE_DETECT_CORR = 1.30        # (b) flat Landsat nearshore under-detection correction on mainland area (modeler's choice in the evidence-supported 1.2-1.4x band)
# (c) Mainland-shed paddies plausibly beach more / escape offshore less than
# island paddies. DIRECTION now supported by Emery et al. 2025 (Commun. Biol.,
# SB Channel, 24 beaches/100 km): kelp->beach wrack connectivity is LOCAL (<10 km)
# and STRONGEST IN WINTER (storms limit which beaches catch it) -> detached
# mainland kelp preferentially beaches near its short, shallow-fringe source
# rather than reaching the far-offshore grounds. But the FRACTION (beach vs
# offshore-escape) is still unquantified -> the 0.90 magnitude stays a MODEST
# modeler's choice. Scales the FLOATING (findable-offshore) weight of mainland-
# shed rafts only; drift physics + local SHORE_CREDIT weighting are untouched.
SHORE_OFFSHORE_YIELD = 0.90     # (c) direction supported (Emery 2025 <10km winter-local wrack); magnitude a modeler's choice

# --- Water-quality gate: WHEN nearshore paddies turn on --------------------
# Real-world rule (BD/WON/SpearFactor + yellowtail SST guides): nearshore
# paddies hold fish only when the water is BOTH warm AND clean/blue. So instead
# of suppressing nearshore by distance alone, relax it where SST is warm (fish
# move inshore) AND chl is low (blue water), and gate OUT green water everywhere
# (offshore green is dead too). chl comes from ShoudiDive's published layer
# (snapshot, optional — if unavailable we fall back to distance-only).
WATER_QUALITY_GATE = True
CHL_CLEAN_MGM3 = 0.30        # <= this -> clean/blue (clarity 1.0); yellowtail water
CHL_GREEN_MGM3 = 2.0         # >= this -> green/turbid (clarity low)
WARM_ON_C = 17.5             # 63.5 F — below this nearshore stays suppressed
WARM_FULL_C = 20.5           # 69 F — warm push / fall peak: full nearshore override

# --- Conditions weighting (deep-research 2026-06-19, adversarially verified) ----
# Finding: the convergence FRONT is the primary locator (peer-reviewed: submesoscale
# convergence physically traps paddies + bait + predators on the seam, >1e5x area
# reduction); kelp abundance is a CONTRIBUTOR, not the ranking driver — so compress
# it (else the big Catalina bed always wins). And clarity is a clean-SIDE EDGE
# preference, NOT a hard veto ("green = dead" was refuted 0-3): suppress green, don't
# zero it, and reward the actual blue/green color break.
KELP_GAMMA = 0.4             # compress kelp density HARD: data sweep + literature agree raw abundance is a gate-of-presence, NOT the ranker (big outer-island beds otherwise auto-win)
CLARITY_FLOOR = 0.40         # green water suppressed to this, NOT vetoed to 0

# --- Research-grade reconciled weighting (2026-06-22, data sweep x literature) -
# 3888-model sweep vs catch reports + 53-agent literature survey. Current model
# was ANTI-predictive (skill 0.42<0.50); data-optimal = compress kelp, low BASE,
# reachability lens, coast-on. Literature adds: paddy QUALITY q(SST), STRUCTURE
# scoring, and PROMOTING catch reports into the field.
SEED_AREA_POW = 0.5          # compress real-canopy seed weighting (outer islands shouldn't auto-win)
# Reachability lens REMOVED (user decision 2026-06-22): a single-launch lens
# nuked the rest of the bight (N. Channel Islands, northern shore), and anglers
# already know their own range + what it takes to get there. The opportunity
# field is launch-AGNOSTIC -- show every ground honestly, let the angler choose.
# (Also kills the per-launch prod-blocker.)
STRUCT_WEIGHT = 0.4          # bathymetry bonus: a paddy over a bank/break holds more + bigger fish (practitioner top-3, was unscored). Modifier, not a patch-maker.
STRUCT_RADIUS_NM = 5.0       # bank-proximity scale (broad enough that the clustered SD banks merge into one structure zone, not 6 spikes)
QUAL_T0_C = 18.0             # paddy fish-holding quality: full at/below, ramps down as it sits in warm water
QUAL_HOT_C = 24.0            # quality -> 0 (physiological collapse, Rothausler >24C)
REPORT_PROMOTE = True        # promote crowd catch-reports from display-only into the opportunity field (literature #1 gap)
N_CONES = 8                  # cap drift cones to the biggest corridors (99 real-kelp cells would throw 99 busy amber fans)
OPP_SMOOTH_SIGMA = 1.5       # gaussian-smooth the opportunity field before contouring: consolidates ~30 small patches into ~11 bigger zones (primary 2%->56%) WITHOUT hiding any ground

# --- Report assimilation (catch reports = the strongest signal; research's #1 gap) -
# Recent catches directly mark where fish are. We boost the opportunity field
# around them, decaying with recency (zone-level persistence holds for days) and
# distance. No lookahead: a frame only sees catches on/before its as-of date.
REPORTS_FILE = "reports.json"
REPORT_WEIGHT = 1.6          # strength of a fresh catch's boost (the strongest signal)
REPORT_DECAY_DAYS = 5.0      # catch influence e-folds over ~5 days
REPORT_RADIUS_NM = 8.0       # a catch lights up a ~8 nm zone around it
REPORT_MAX_AGE_DAYS = 14.0   # ignore catches older than this

# --- Attainable-area framing (HANDOFF addendum: "Active Map") -------------
# Lead with a tight high-odds CORE + a START point, demote 50/80% to fallback.
CORE_FRACTION = 0.30         # mass fraction of the "attainable zone" (tighter than 50%)
DIFFUSE_AREA_KM2 = 2600.0    # core bigger than this -> honest "paddies scattered" copy
FEATURE_SNAP_MAX_NM = 16.0   # only name a feature edge if the peak is within this of one
POS_FLOOR_NM = 10.0          # irreducible multi-day drift error -> never claim a tighter ±

# --- Illustrative scenario ------------------------------------------------
SCENARIO = "live"
SWELL_BOOST_M = 2.0
WARM_BOOST_C = 4.0
# A time-localized storm N days ago — demonstrates event -> drift -> now:
STORM_DAYS_AGO = 3.0
STORM_HS_BOOST_M = 3.0       # +3 m Hs spike on the storm day(s)
STORM_WINDOW_H = 36.0        # storm lasts ~1.5 days

# --- Canopy-dynamics reservoir (P2/P3): per-bed weakening, turnover-fed stock ---
# Recalibrated 2026-07-01 after a deep-research review (Rodriguez 2013 Ecology;
# Rassweiler 2018 Ecology; Bell/Cavanaugh 2021 PNAS; Gerard 1982; Hobday 2000;
# Leichter 2023) that CORRECTED the first cut. Three temperature effects are now
# kept DISTINCT instead of conflated:
#   1. GROWTH/REGROWTH gate  <- SST as a nutrient proxy; throttles regrowth but
#      NEVER to zero (internal-wave + ammonium N sustain a floor; Gerard's
#      3.6->0.9%/day residual, Leichter refugia). This is the ONLY thing SST
#      does to the stock.
#   2. SHEDDING flux         <- dominated by intrinsic senescence/turnover
#      (frond age = 58% of frond-loss variation, Rodriguez) + wave dislodgement.
#      Because Macrocystis turns its ~0.4 kg/m^2 standing canopy over ~12x/yr,
#      turnover SUSTAINS shedding rather than the pool exhausting over a season.
#   3. Raft DECAY at sea (warm shortens time-afloat >20C) lives in the SINK
#      model (drift.py), NOT here -- do not re-penalise production for it.
# Net: warm beds stay productive-but-lower (not exhausted by July); only a real
# heatwave (SST past the sink/dieback thresholds) drives large loss.
SEASON_DAYS = 120            # season-long reservoir integration window (days)
CANOPY_GROW = 0.06          # max daily robust regrowth rate (fraction of R, gated)
CANOPY_GROW_GATE_COOL = 14.0  # SST °C at/below which regrowth is full (nitrate-replete; nitrate ~0 above ~14.2C, Snyder 2020)
CANOPY_GROW_GATE_WARM = 19.0  # SST °C of MINIMUM regrowth (min canopy condition ~19C, Bell 2021) -> the FLOOR, not zero
# Warm-water REGROWTH FLOOR raised 0.20 -> 0.30 (mainland/base) after a 102-agent
# deep-research adjudication (2026-07-01, "San Clemente under-reporting"): a 0.20
# floor drained warm SCB beds' canopy MONOTONICALLY to ~16% fullness by July,
# which INVERTS the observed default (Bell 2019, 34-yr Landsat: SCB canopy PEAKS
# spring/summer, bottoms WINTER; 20C is sub-lethal, collapse threshold ~24C
# Cavanaugh 2019). Warm beds keep growing on REGENERATED N — ammonium+urea are
# often MORE abundant than nitrate in the SCB (Brzezinski 2013; Lees 2024) and a
# transplant held ~25% of max growth on reserves near Catalina (Gerard 1982) — so
# the SST-nitrate gate must not throttle regrowth toward zero. Base 0.30 = the
# regenerated-N sustained fraction on a warm MAINLAND bed.
CANOPY_GROW_GATE_FLOOR = 0.30
# ISLAND beds get a HIGHER floor (internal-wave REFUGIA): during warm/low-upwelling
# periods internal waves supply 84-100% of NO3 exposure to SoCal kelp (Leichter
# 2023) and Macrocystis bridges the whole water column to nitrate below the
# thermocline (Fram 2013) — exposed offshore islands (San Clemente, Catalina) at
# the SCB edge decouple their nutrient supply from bulk surface SST far more than a
# sheltered mainland embayment at the same SST (deep-research verdict (e),
# SUPPORTED). Sits at the TOP of the evidence-supported 0.25-0.4 band because SCI
# is the strongest-refugia case; NOT higher, per the "proportions-of-low-flux /
# draining-transient" magnitude caveats. Only binds at SST>=19C, so it barely
# touches the already-cool northern Channel Islands (they grow well above the floor).
CANOPY_GROW_GATE_FLOOR_ISLAND = 0.40
CANOPY_WEAKEN = 0.022       # baseline daily R->V senescence -- the DOMINANT shed driver (age-structured frond turnover, Rodriguez/Rassweiler ~12x/yr)
# Warm-DETACHMENT enhancement threshold lowered 20 -> 17.5C: enhanced senescence-
# vulnerability/condition loss onsets ~17-18C (Bell 2021 min condition ~19C;
# nitrate limitation from ~14.2C), well below the ~20-24C raft-DECAY/mortality
# thresholds. Separate from T0_C (=20, kept for the sink + legacy warm-dose).
CANOPY_WARM_T0 = 17.5
CANOPY_WARM_WEAKEN = 0.15   # heat accelerates senescence (R->V), per °C above CANOPY_WARM_T0 -- warm beds shed MORE per unit canopy
CANOPY_SHED_BASE = 0.020    # baseline daily shed fraction of V (senescence self-detachment; always-on paddy trickle even in calm — Hobday's standing raft population)
CANOPY_SHED = 0.0015        # storm shed gain: + CANOPY_SHED * wave_dose on top of baseline (Seymour-anchored)
# Mainland (non-island) beds shed MORE per unit wave energy than island beds:
# the shallow mainland fringe fails by entanglement cascade in winter storms
# (Seymour Point Loma: mortality rises as depth falls — 94%@12m vs 65%@18m),
# and mainland detachment is winter-storm-dominated (Hobday: 23% winter loss vs
# 8% fall). Applied ONLY to the wave (storm) shed term, so it is winter-weighted
# automatically (kicks in on big-swell days, ~zero in a calm summer) instead of
# the retired flat year-round SHORE_SOURCE_BOOST. 0.0 = mainland sheds like an
# island; 0.6 = +60% storm-shed sensitivity on the shallow mainland fringe.
CANOPY_SHORE_WAVE_GAIN = 0.6
CANOPY_WARM_INT = 0.08      # warm×wave interaction: heat-weakened kelp sheds easier, per °C above CANOPY_WARM_T0
CANOPY_INSITU = 0.010       # daily in-situ vulnerable loss (decompose/sink, not findable)
CANOPY_INIT_ROBUST = 0.60   # spin-up: robust fraction of K at the season start
CANOPY_INIT_VULN = 0.10     # spin-up: vulnerable fraction of K at the season start
CANOPY_HEALTH_DAMP = 0.5    # recent/ever Landsat health is a NOISY prior (canopy area != biomass; tide/submergence hide 15-30%) -> damp it toward 1 by this much
CANOPY_BAND_SCALE = 0.016   # maps regional mean shed-rate -> 0-100 index (recalibrate if calm band drifts off Low)

OUT_DIR = "out"
SEED = 42
