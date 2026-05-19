"""All tunable parameters for the visibility prediction algorithm."""
from dataclasses import dataclass
from typing import Dict

# Latitude zone boundaries (deg N). Region-aware as of 2026-05-18:
# pulls from active_region().lat_zone_bounds so Baja gets its own
# north/mid/south_baja zones instead of falling through to CA's
# `bight` catch-all (which has bight-specific coefficients calibrated
# for SoCal nearshore productivity, NOT for the clearer subtropical
# water further south).
#
# Falls back to the CA snapshot when running outside the pipeline
# context (test fixtures that don't set SHOULDIDIVE_REGION).
_CA_LAT_ZONE_BOUNDS = {
    "norcal":     (36.00, 90.0),
    "central":    (34.45, 36.00),
    "transition": (33.70, 34.45),
    "bight":      (-90.0, 33.70),
}
try:
    try:
        from pipeline.regions import active_region as _active_region
    except ModuleNotFoundError:
        from regions import active_region as _active_region
    LAT_ZONE_BOUNDS = dict(_active_region().lat_zone_bounds)
except Exception:
    LAT_ZONE_BOUNDS = dict(_CA_LAT_ZONE_BOUNDS)

NEARSHORE_DIST_KM = 5.0
NEARSHORE_MAX_DEPTH_M = 30
ISLANDS_DIST_KM = 10.0

ZONE_LABELS = ["nearshore", "islands", "offshore"]
LAT_LABELS = list(LAT_ZONE_BOUNDS.keys())


def zone_key(lat_label: str, dist_label: str) -> str:
    return f"{lat_label}_{dist_label}"


# Channel Islands cluster centroids: (lat, lng, current-regime side)
CHANNEL_ISLAND_CENTROIDS = {
    "san_miguel":   (34.04, -120.37, "west"),
    "santa_rosa":   (33.97, -120.10, "west"),
    "santa_cruz":   (34.00, -119.74, "east"),
    "anacapa":      (34.01, -119.40, "east"),
    "san_nicolas":  (33.25, -119.50, "open"),
    "santa_barbara":(33.48, -119.04, "open"),
    "san_clemente": (32.92, -118.50, "open"),
    "catalina":     (33.39, -118.42, "east"),
    "coronados":    (32.42, -117.27, "open"),
}


PERSISTENCE_TAU_DAYS: Dict[str, float] = {
    # v3.5 (2026-05-10) — norcal_* added per PR-NC-1.
    # v3.5.1 (2026-05-11) — norcal_* tau values BUMPED UP after a
    # dev-preview review showed the chl PNG painting bloomed water
    # in Monterey Bay while the viz prediction stayed in Good. Root
    # cause: with the original tight tau (1.0d nearshore), a 14-day-
    # old bloom observation gets weight exp(-14) ≈ 1e-6 in the
    # persistence blend, so the viz model effectively saw climatology
    # while the chl LAYER still painted the bloom. NorCal marine-
    # layer/fog frequently blocks the satellite for 10-30 days at a
    # stretch; the persistence tau has to be long enough that those
    # gaps don't silently flip the prediction to "normal water."
    #
    # v3.5 → v3.5.1 trajectory:
    #   norcal_nearshore   1.0 → 7.0  (14-day-old bloom keeps ~14% weight)
    #   norcal_islands     2.0 → 5.0
    #   norcal_offshore    4.5 → 5.5  (mild bump for consistency)
    #
    # The original "NorCal flips fast on relaxation" rationale is
    # real but ignores the data-availability constraint: we can't
    # track day-to-day relaxation when the satellite is fogged out.
    # Trust the most recent observation longer; the prediction
    # converges anyway once a fresh observation arrives.
    "norcal_nearshore": 7.0, "norcal_islands": 5.0, "norcal_offshore": 5.5,
    "central_nearshore": 1.5, "central_islands": 2.5, "central_offshore": 4.5,
    "transition_nearshore": 2.0, "transition_islands": 3.0, "transition_offshore": 5.0,
    "bight_nearshore": 2.5, "bight_islands": 3.5, "bight_offshore": 6.0,
    # Baja zones (2026-05-18). Longer persistence than CA because:
    #   * cloud cover over the eastern Pacific + Cortez is less frequent
    #     than NorCal marine-layer days, so fresh observations arrive
    #     more reliably (shorter taus could work in principle);
    #   * BUT chl regime changes more slowly in subtropical / oligotrophic
    #     water — south Baja water structure is stable for 2-3 weeks at
    #     a stretch, so long persistence is the right physical model.
    "north_baja_nearshore": 3.0, "north_baja_islands": 4.0, "north_baja_offshore": 6.0,
    "mid_baja_nearshore":   4.0, "mid_baja_islands":   5.0, "mid_baja_offshore":   7.0,
    "south_baja_nearshore": 5.0, "south_baja_islands": 6.0, "south_baja_offshore": 8.0,
}


@dataclass
class DriverCoefficients:
    upwell:    float = 0.0
    swell:     float = 0.0
    precip:    float = 0.0
    river:     float = 0.0
    sst:       float = 0.0
    seasonal:  float = 0.0
    exposure:  float = 0.0
    tide:      float = 0.0
    substrate: float = 0.0
    cloud:     float = 0.0


DRIVER_COEFFS: Dict[str, DriverCoefficients] = {
    # v3.5 (2026-05-10) — norcal_* added per PR-NC-1.
    # NorCal nearshore is the most upwelling- + bloom-driven water on
    # the CA coast. Higher seasonal + exposure than central; bigger
    # swell coefficient because Mendocino-area shorelines see real
    # ocean swell unlike Monterey's lee-protected pockets. Sst sign
    # flipped slightly more negative because cold-anomaly here often
    # comes WITH a clearer-water relaxation rather than a green bloom.
    "norcal_nearshore":   DriverCoefficients(upwell=0.25, swell=0.35, precip=0.25, river=0.35, sst=-0.10, seasonal=0.45, exposure=0.30, tide=0.10, substrate=0.18, cloud=-0.06),
    "norcal_islands":     DriverCoefficients(upwell=0.16, swell=0.12, precip=0.06, river=0.06, sst=-0.07, seasonal=0.40, exposure=0.35, tide=0.02, substrate=0.05, cloud=-0.05),
    "norcal_offshore":    DriverCoefficients(upwell=0.14, swell=0.02, precip=0.00, river=0.00, sst=-0.05, seasonal=0.35, exposure=0.05, tide=0.00, substrate=0.00, cloud=-0.03),

    # Central CA (Monterey area, 34.45–36.00°N as of v3.5) is an
    # upwelling-dominated zone: cold water, persistent spring/summer
    # blooms, often green nearshore even on otherwise calm days. v2's
    # optimistic seasonal/upwell cuts didn't make sense for this region
    # — Tempbreak consistently shows greener water here than v3 was
    # predicting. v3.1 restored upwell + seasonal coefficients to ~v0.2
    # levels for ALL central zones (the productivity assumption is
    # real, not a bug).
    # v3.5: lat boundary moved from 34.45..90 to 34.45..36.00 — Big Sur,
    # Monterey, Farallons, etc. now classify as `norcal_*` instead.
    "central_nearshore":  DriverCoefficients(upwell=0.18, swell=0.30, precip=0.20, river=0.30, sst=-0.06, seasonal=0.40, exposure=0.20, tide=0.10, substrate=0.15, cloud=-0.08),
    "central_islands":    DriverCoefficients(upwell=0.12, swell=0.10, precip=0.05, river=0.05, sst=-0.05, seasonal=0.35, exposure=0.30, tide=0.02, substrate=0.05, cloud=-0.06),
    "central_offshore":   DriverCoefficients(upwell=0.10, swell=0.02, precip=0.00, river=0.00, sst=-0.04, seasonal=0.30, exposure=0.05, tide=0.00, substrate=0.00, cloud=-0.04),

    "transition_nearshore": DriverCoefficients(upwell=0.08, swell=0.25, precip=0.18, river=0.28, sst=-0.04, seasonal=0.22, exposure=0.13, tide=0.08, substrate=0.12, cloud=-0.06),
    "transition_islands":   DriverCoefficients(upwell=0.06, swell=0.08, precip=0.04, river=0.04, sst=-0.03, seasonal=0.16, exposure=0.22, tide=0.02, substrate=0.05, cloud=-0.05),
    "transition_offshore":  DriverCoefficients(upwell=0.04, swell=0.02, precip=0.00, river=0.00, sst=-0.02, seasonal=0.12, exposure=0.03, tide=0.00, substrate=0.00, cloud=-0.03),

    "bight_nearshore":  DriverCoefficients(upwell=0.04, swell=0.20, precip=0.16, river=0.25, sst=-0.02, seasonal=0.15, exposure=0.10, tide=0.10, substrate=0.18, cloud=-0.04),
    "bight_islands":    DriverCoefficients(upwell=0.03, swell=0.06, precip=0.03, river=0.03, sst=-0.02, seasonal=0.12, exposure=0.20, tide=0.02, substrate=0.05, cloud=-0.03),
    "bight_offshore":   DriverCoefficients(upwell=0.02, swell=0.01, precip=0.00, river=0.00, sst=-0.01, seasonal=0.08, exposure=0.02, tide=0.00, substrate=0.00, cloud=-0.02),
}


SIGMA_LOG_CHL: Dict[str, float] = {
    # v3.5 (2026-05-10) — norcal_* added per PR-NC-1.
    # Higher than central because chl variance is greater up the coast
    # (relaxation cycle alternates clear with bloomed water on multi-
    # day windows). Offshore drops to 0.40 since cold-deep regimes
    # there are more stable than nearshore's bloom-pulse swings.
    "norcal_nearshore": 0.65, "norcal_islands": 0.55, "norcal_offshore": 0.40,
    "central_nearshore": 0.55, "central_islands": 0.45, "central_offshore": 0.35,
    "transition_nearshore": 0.50, "transition_islands": 0.40, "transition_offshore": 0.32,
    "bight_nearshore": 0.45, "bight_islands": 0.38, "bight_offshore": 0.30,
    # Baja zones (2026-05-18). Open Pacific Baja + Cortez vary far less
    # day-to-day than CA's bloom-prone nearshore — lower sigma reflects
    # that. North Baja Pacific is the variability outlier (cold-water
    # upwelling + summer chl blooms off Cedros) so keep it close to CA
    # bight values.
    "north_baja_nearshore": 0.55, "north_baja_islands": 0.45, "north_baja_offshore": 0.35,
    "mid_baja_nearshore":   0.45, "mid_baja_islands":   0.38, "mid_baja_offshore":   0.30,
    "south_baja_nearshore": 0.40, "south_baja_islands": 0.32, "south_baja_offshore": 0.28,
}


@dataclass
class SecchiCoefficients:
    a: float = 7.0
    b: float = 0.30


SECCHI_COEFFS: Dict[str, SecchiCoefficients] = {
    # secchi_m = a · chl^(-b). The exponent `b` comes from coastal-CA
    # literature and is left alone; only the multiplier `a` is tuned.
    #
    # v3 calibration: v2 over-corrected — side-by-side with Tempbreak's
    # chlorophyll observations the visibility map was running too blue
    # (a typical bbox-mean of ~45 ft put a normal day in Very Good /
    # Excellent territory instead of Good / Very Good). Walked every
    # bumped multiplier ~halfway back toward v0.2. Genuinely calm
    # offshore days still hit Excellent at chl ≤ 0.15 mg/m³, but a
    # mid-range chl reading no longer floats into the deep-blue band.
    #
    # v0.2 → v2 → v3 trajectory for reference:
    #   central_nearshore     4.0  →  6.5  →  5.5
    #   transition_nearshore  4.5  →  7.0  →  6.0
    #   transition_islands    7.0  →  8.5  →  7.5
    #   bight_nearshore       5.0  →  7.5  →  6.5
    #   bight_islands         7.5  →  9.0  →  8.0
    #   *_offshore            8.5  → 10.0  →  9.0
    # v3.2 — central CA pushed BELOW v0.2 because even the legacy
    # multipliers were too optimistic for that latitude band. Side-by-
    # side with Tempbreak the open-ocean offshore was reading ~45 ft
    # (Very Good) when reality is mid-Good (~25–35 ft) on a normal
    # day, dropping to Excellent only on the genuinely calm days that
    # punch through the bloom regime. The drop is ~30–40% from v3.1.
    #
    #                     v0.2  v3.1  v3.2
    #   central_nearshore  4.0   4.5   3.5    (kelp at chl 2 → ~9 ft, Poor/Fair edge)
    #   central_islands    6.5   6.5   5.0
    #   central_offshore   8.5   8.0   5.5    (chl 0.2 → ~31 ft, mid-Good)
    # v3.5 (2026-05-10) — norcal_* added per PR-NC-1.
    # Slightly lower nearshore `a` than central (NorCal nearshore is
    # generally greener on bloom days) but offshore `a` higher
    # (Pioneer/Davidson/Farallons see genuine deep-blue water on
    # relaxation days). Re-evaluate once residuals accumulate against
    # NorCal observations — see norcal-formula-review.md § 2.
    "norcal_nearshore":     SecchiCoefficients(a=3.0, b=0.28),
    "norcal_islands":       SecchiCoefficients(a=5.5, b=0.30),
    "norcal_offshore":      SecchiCoefficients(a=6.5, b=0.32),
    "central_nearshore":    SecchiCoefficients(a=3.5, b=0.28),
    "central_islands":      SecchiCoefficients(a=5.0, b=0.30),
    "central_offshore":     SecchiCoefficients(a=5.5, b=0.32),
    "transition_nearshore": SecchiCoefficients(a=6.0, b=0.28),
    "transition_islands":   SecchiCoefficients(a=7.5, b=0.30),
    "transition_offshore":  SecchiCoefficients(a=9.0, b=0.32),
    "bight_nearshore":      SecchiCoefficients(a=6.5, b=0.28),
    # v3.4 (2026-05-05): a=6.5 → 8.5. v3.3 dropped a from 8.0 to 6.5 to
    # fit 4 JustGetWet shore-dive observations (La Jolla / Pt Loma area)
    # that were classifying as bight_islands due to a separate bug in
    # fetch_visibility.static_fields — every non-mainland polygon in
    # land.geojson (including SD bay islets) counted as an "island" for
    # dist_to_island_km. That mis-classification dragged this calibration
    # down to fit shore observations, which then made GENUINE bight_islands
    # locations like San Clemente, Catalina, San Nicolas under-predict
    # significantly (Pearson r = -0.986 on bight_islands as of 2026-05-05).
    #
    # Now that fetch_visibility filters islands_all to only the named
    # CHANNEL_ISLAND_CENTROIDS, SD shore correctly classifies as
    # bight_nearshore, and bight_islands gets only real Channel-Island
    # data. Bumped `a` slightly past v3.2's 8.0 toward v3.1's 9.0 to
    # match the observed gin-clear days at SCI/Catalina backsides
    # (50+ ft on calm low-chl days). Re-evaluate once the residuals
    # accumulate at the proper zone.
    "bight_islands":        SecchiCoefficients(a=8.5, b=0.30),
    "bight_offshore":       SecchiCoefficients(a=9.0, b=0.32),
    # Baja zones (2026-05-18). Without these, Baja cells fall through to
    # CA's `bight` fallback in LAT_ZONE_BOUNDS and use bight coefficients
    # — tuned for SoCal nearshore productivity, NOT for the much clearer
    # subtropical water of mid + south Baja. User QA report: prediction
    # was "under reporting" — Cabo Pulmo / Espíritu Santo regularly hit
    # 60–100 ft in reality and the bight_islands coefficient (a=8.5) was
    # capping the prediction at ~50 ft even on calm chl-0.1 days.
    #
    # Calibration logic (a · chl^-b; b held at the literature value):
    #   North Baja Pacific (Cedros / San Quintín / Ensenada) — cold
    #     California-Current water with summer plankton; similar to CA
    #     bight upper end. a≈5/7/8.
    #   Mid Baja (Vizcaíno / Magdalena) — transitional, kelp gives way
    #     to clearer water moving south. a≈7/10/11.
    #   South Baja (Cabo / La Paz / Cortez south) — subtropical clear
    #     water year-round; Cabo Pulmo + Espíritu Santo + Los Islotes
    #     are famous for 80–100 ft viz on calm days. a≈9/13/14 puts
    #     chl=0.1 in the 85–95 ft band, matching reality.
    # v2 (2026-05-18): bumped south_baja + mid_baja `a` after user QA.
    # The previous values capped at ~85 ft for the south-Cortez summer
    # clarity peak (Yavaros / Espíritu Santo / Cabo Pulmo in August
    # routinely hit 100+ ft on calm low-chl days). Old → new:
    #   south_baja_islands   13.0 → 16.0  (chl 0.10 → 105 ft)
    #   south_baja_offshore  14.0 → 17.0  (chl 0.10 → 112 ft)
    #   south_baja_nearshore  9.0 → 11.0  (small bump, nearshore still
    #                                       feels rivermouth + reef edge)
    #   mid_baja_islands     10.0 → 12.0
    #   mid_baja_offshore    11.0 → 13.0
    # North Baja Pacific stays modest — that water is upwelling-cold
    # and rarely hits 100+ ft regardless of chl.
    "north_baja_nearshore": SecchiCoefficients(a=5.0,  b=0.28),
    "north_baja_islands":   SecchiCoefficients(a=8.0,  b=0.30),
    "north_baja_offshore":  SecchiCoefficients(a=9.0,  b=0.32),
    "mid_baja_nearshore":   SecchiCoefficients(a=7.0,  b=0.28),
    "mid_baja_islands":     SecchiCoefficients(a=12.0, b=0.30),
    "mid_baja_offshore":    SecchiCoefficients(a=13.0, b=0.32),
    "south_baja_nearshore": SecchiCoefficients(a=11.0, b=0.28),
    "south_baja_islands":   SecchiCoefficients(a=16.0, b=0.30),
    "south_baja_offshore":  SecchiCoefficients(a=17.0, b=0.32),
}


@dataclass
class TurbidityCorrections:
    swell:     float = 0.0
    runoff:    float = 0.0
    river:     float = 0.0
    kelp:      float = 0.0
    substrate: float = 0.0
    tide:      float = 0.0


TURBIDITY_CORRECTIONS: Dict[str, TurbidityCorrections] = {
    # NOTE: as of v2 calibration the kelp term is interpreted PER-UNIT
    # bottom-stir (not flat). visibility.py multiplies it by bottom_stir
    # so calm-day kelp = ~0 penalty, storm-day kelp = full coefficient.
    # That matches the physics: kelp filters water on calm days and only
    # sheds canopy debris when waves stir the column. Numeric values for
    # nearshore stayed the same; islands bumped 1.0 → 1.5 to compensate
    # for the now-conditional firing.
    # v3.5 (2026-05-10) — norcal_* added per PR-NC-1.
    # Bigger swell + runoff + river penalties than central because
    # NorCal nearshore takes the brunt of Pacific groundswell + the
    # Russian/Eel/Klamath river plumes drop big sediment loads after
    # winter atmospheric rivers. Islands stays similar; offshore is
    # zero (clean water beyond ~10 km from any plume mouth).
    "norcal_nearshore":     TurbidityCorrections(swell=9.0, runoff=5.0, river=6.0, kelp=2.5, substrate=2.5, tide=2.5),
    "norcal_islands":       TurbidityCorrections(swell=2.5, runoff=0.6, river=0.6, kelp=2.0, substrate=0.5, tide=0.3),
    "norcal_offshore":      TurbidityCorrections(swell=0.0, runoff=0.0, river=0.0, kelp=0.0, substrate=0.0, tide=0.0),
    "central_nearshore":    TurbidityCorrections(swell=8.0, runoff=4.0, river=5.0, kelp=2.0, substrate=2.5, tide=1.5),
    "central_islands":      TurbidityCorrections(swell=2.0, runoff=0.5, river=0.5, kelp=1.5, substrate=0.5, tide=0.2),
    "central_offshore":     TurbidityCorrections(swell=0.0, runoff=0.0, river=0.0, kelp=0.0, substrate=0.0, tide=0.0),
    "transition_nearshore": TurbidityCorrections(swell=6.0, runoff=3.0, river=4.5, kelp=2.0, substrate=2.0, tide=1.2),
    "transition_islands":   TurbidityCorrections(swell=1.5, runoff=0.4, river=0.4, kelp=1.5, substrate=0.5, tide=0.2),
    "transition_offshore":  TurbidityCorrections(swell=0.0, runoff=0.0, river=0.0, kelp=0.0, substrate=0.0, tide=0.0),
    "bight_nearshore":      TurbidityCorrections(swell=5.0, runoff=3.5, river=4.0, kelp=2.0, substrate=2.5, tide=1.5),
    "bight_islands":        TurbidityCorrections(swell=1.0, runoff=0.3, river=0.3, kelp=1.5, substrate=0.4, tide=0.2),
    "bight_offshore":       TurbidityCorrections(swell=0.0, runoff=0.0, river=0.0, kelp=0.0, substrate=0.0, tide=0.0),
    # Baja zones (2026-05-18). Lighter penalties than CA across the board:
    # the Baja peninsula is arid (no Russian/Eel/Klamath equivalents —
    # only a few small wadi-style runoff events per year), and the Sea
    # of Cortez is sheltered from the open-Pacific groundswell that
    # drives most CA nearshore turbidity. North Baja Pacific is the
    # one place real swell + kelp + cold-water particulate loads
    # matter — keep similar to bight there.
    "north_baja_nearshore": TurbidityCorrections(swell=5.0, runoff=2.0, river=1.5, kelp=2.0, substrate=2.5, tide=1.0),
    "north_baja_islands":   TurbidityCorrections(swell=1.5, runoff=0.3, river=0.2, kelp=1.5, substrate=0.4, tide=0.2),
    "north_baja_offshore":  TurbidityCorrections(swell=0.0, runoff=0.0, river=0.0, kelp=0.0, substrate=0.0, tide=0.0),
    "mid_baja_nearshore":   TurbidityCorrections(swell=3.5, runoff=1.0, river=1.0, kelp=1.5, substrate=2.0, tide=0.8),
    "mid_baja_islands":     TurbidityCorrections(swell=1.0, runoff=0.2, river=0.1, kelp=1.0, substrate=0.3, tide=0.2),
    "mid_baja_offshore":    TurbidityCorrections(swell=0.0, runoff=0.0, river=0.0, kelp=0.0, substrate=0.0, tide=0.0),
    "south_baja_nearshore": TurbidityCorrections(swell=2.5, runoff=0.5, river=0.5, kelp=0.0, substrate=1.5, tide=0.6),
    "south_baja_islands":   TurbidityCorrections(swell=0.5, runoff=0.1, river=0.0, kelp=0.0, substrate=0.2, tide=0.1),
    "south_baja_offshore":  TurbidityCorrections(swell=0.0, runoff=0.0, river=0.0, kelp=0.0, substrate=0.0, tide=0.0),
}


CHL_MIN_MGPM3 = 0.03
CHL_MAX_MGPM3 = 50.0
SECCHI_MIN_M  = 1.0
# Raised 25.0 → 35.0 (2026-05-18). 25 m = 82 ft was clipping at the
# top of the Excellent band, hiding the genuine 90-100 ft conditions
# you get on calm days at Cabo Pulmo, Espíritu Santo / Los Islotes,
# Isla Catalina / Carmen offshore, and (occasionally) the Channel
# Islands backsides at SCI. 35 m = 115 ft puts the ceiling where it
# physically belongs. Score curve (_BAND_KNOTS_M in visibility.py)
# stays anchored at 24.4 m = 100; values above that all score 100
# but display the real Secchi number for cursor + spot panel.
SECCHI_MAX_M  = 35.0


# ---- Kd_490 → Secchi blend (Phase 2) -----------------------------------
#
# The viz model's foundation is chl→Secchi via per-zone secchi = a·chl^(−b)
# coefficients. Kd_490 is a directly measured optical property (diffuse
# attenuation at 490 nm, 1/m); the Poole–Atkins / Tyler relation gives
# Secchi ≈ 1.7 / Kd_490. When a fresh Kd observation is available we
# blend its Secchi with the chl-derived Secchi, weighted toward Kd
# because it's a measurement rather than an inference.
#
# Weight schedule:
#   w(age) = KD_BLEND_WEIGHT_FRESH · exp(−age / KD_BLEND_TAU_DAYS)
#
# Rationale for the defaults:
#   * 1.7   — long-standing Poole-Atkins constant. ±15% by water type;
#             smaller than the per-zone secchi_a swing (3.5..9.0).
#   * 0.7   — Kd is the more direct measurement, so it should dominate
#             when fresh. Reserves 0.3 for the chl prior so per-zone
#             calibration still anchors the result.
#   * tau=5 — the DINEOF gap-filled Kd_490 SQ product publishes ~11 days
#             behind today; tau=5 gives weight 0.7·exp(-11/5) ≈ 0.077
#             at typical age, so Kd still nudges the chl prior even
#             when stale. Drops to ≈ 0.5 at age=2 (a fresher day) and
#             0.7 if it ever lands the same day.
KD_TO_SECCHI_FACTOR  = 1.7
KD_BLEND_WEIGHT_FRESH = 0.7
KD_BLEND_TAU_DAYS    = 5.0


# 0-100 clarity score
SCORE_FULL_SECCHI_M = 30.0

CLARITY_CATEGORIES = [
    (  0,  20, "Poor",      "#c2410c"),  #  0..10 ft  /  0..3.0 m   — silty / blown out
    ( 20,  40, "Fair",      "#eab308"),  # 10..20 ft  /  3.0..6.1 m  — washed out
    ( 40,  60, "Good",      "#84cc16"),  # 20..30 ft  /  6.1..9.1 m  — typical CA kelp diving
    ( 60,  80, "Very Good", "#06b6d4"),  # 30..50 ft  /  9.1..15.2 m — clean blue water
    ( 80, 101, "Excellent", "#0369a1"),  # 50+ ft     /  15.2+ m     — tropical / once-a-year
]


QUALITY_FLAGS = {
    "OBSERVED_1D":         "Direct satellite observation today",
    "OBSERVED_3D":         "Most recent valid pixel within 3 days",
    "INTERPOLATED":        "Spatially interpolated from neighbors",
    "PREDICTED_HIGH_CONF": "Model output, last obs <5 days, narrow interval",
    "PREDICTED_MED_CONF":  "Model output, last obs 5-10 days",
    "PREDICTED_LOW_CONF":  "Model output, last obs >10 days or active event",
    "CLIMATOLOGY_ONLY":    "No recent obs; seasonal climatology only",
}

PRED_AGE_HIGH_CONF_DAYS = 5
PRED_AGE_LOW_CONF_DAYS  = 10

CDOM_CONTAMINATION_DIST_KM = 3.0
ISLAND_EXPOSURE_RADIUS_KM = 8.0
