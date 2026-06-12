"""Coefficients for the v1 water-column visibility heuristic (PRD C1).

Every constant here is a documented guess chosen to be physically
reasonable and to reproduce the Point Loma acceptance anchor
(clear ~20-30 ft over a cliff ~22-28 ft with ~5-12 ft below, June).
The C4 calibration harness (gliders / Scripps Pier / diver reports)
is the mechanism that tunes these — do not hand-tweak against single
anecdotes. Rationale for each block lives in DECISIONS.md (WC-D
entries).

All depths/visibilities are in FEET to match the existing viz layer
contract (range_ft, unit "ft"); intermediate physics in SI.
"""
from __future__ import annotations

# ---- Physical constants -------------------------------------------------

RHO_AIR = 1.225          # kg/m3
RHO_SEAWATER = 1025.0    # kg/m3
DRAG_COEFFICIENT = 1.2e-3  # neutral 10 m drag coefficient (constant, v1)
GRAVITY = 9.81           # m/s2
CORIOLIS_F = 8.0e-5      # s^-1 — f at ~33.5N (SoCal). v1 uses one value
                         # for the whole CA bbox; per-cell f is a later
                         # refinement (error < 15% across the bbox).

FT_PER_M = 3.28084

# ---- Upwelling index (Bakun/Ekman from existing wind) -------------------

# CA coastline mean orientation: the coast runs NW-SE; the equatorward
# alongshore direction (the one whose wind drives offshore Ekman
# transport / upwelling) points toward ~140 deg true. v1 uses a single
# angle for the whole bbox — Point Conception's bend is the known
# casualty, noted in DECISIONS.md.
ALONGSHORE_EQUATORWARD_DEG = 140.0

# Ekman transport (m^2/s per meter of coastline) at which the
# upwelling term saturates to 1.0. Bakun indices of ~100-200
# (m^3/s per 100 m coast) = 1-2 m^2/s are strong events.
UPWELLING_SATURATION_M2S = 1.5

# ---- Near-bottom resuspension (swell x depth) ----------------------------

# Critical near-bottom orbital velocity below which fine sediment
# stays put (fine sand ~0.1 m/s; we start the ramp a touch earlier).
ORBITAL_VEL_CRITICAL_MS = 0.08
# Ramp width: u_b at critical + scale -> resuspension_norm = 1.
ORBITAL_VEL_SCALE_MS = 0.25
# Default swell period when no period field is available in the wave
# data (long-period SoCal groundswell).
DEFAULT_SWELL_PERIOD_S = 14.0

# ---- Cliff (thermocline proxy) depth -------------------------------------

# Monthly climatological mixed-layer/thermocline depth for SoCal, in
# FEET. Anchors: summer SoCal thermocline ~6-8 m (20-26 ft) — the
# Point Loma "cliff at ~25 ft" anchor is June; winter mixing erodes
# stratification to ~15 m+ (deep/weak cliff).
CLIFF_BASE_FT_BY_MONTH = {
    1: 50.0, 2: 50.0, 3: 42.0, 4: 33.0, 5: 28.0, 6: 25.0,
    7: 22.0, 8: 22.0, 9: 25.0, 10: 30.0, 11: 40.0, 12: 48.0,
}

# Strong upwelling lifts the thermocline; at saturation the cliff
# shoals by this fraction of its base — but only near the coast (see
# UPWELLING_DECAY_KM) and damped inside the Bight (below).
UPWELLING_CLIFF_SHOALING_FRAC = 0.30

# ---- Regional bands (v1.1, 2026-06-12) -----------------------------------
# The coastline's regime changes at two real capes, not at a round
# latitude:
#   * SoCal Bight (south of Pt. Conception): the coast turns E-W and
#     the Channel Islands shelter it — upwelling is weak/episodic,
#     summer stratification strong, thermocline shallow + sharp. The
#     monthly base table is tuned here (Point Loma anchor).
#   * CenCal (Conception -> Pt. Arena): the classic upwelling core.
#     Mean state colder/less stratified (deeper base), but coastal
#     upwelling events legitimately drag the nearshore pycnocline up,
#     so the full shoaling term applies.
#   * NorCal (north of Pt. Arena): strongest wind mixing; weak, often
#     diffuse stratification — deepest base. (C2's model MLD replaces
#     all of this; C5 should also carry lower confidence here.)
PT_CONCEPTION_LAT_DEG = 34.45
PT_ARENA_LAT_DEG = 38.95
CENCAL_CLIFF_DEEPEN_FRAC = 0.15
NORCAL_CLIFF_DEEPEN_FRAC = 0.30

# The Bight's E-W coast makes the prevailing NW wind largely
# cross-shore (and the single ALONGSHORE_EQUATORWARD_DEG above is a
# CenCal/NorCal angle), so the same wind produces less upwelling
# there: damp the shoaling term.
BIGHT_UPWELLING_DAMPING = 0.6

# ---- Cross-shore structure (v1.1) -----------------------------------------
# Coastal upwelling lifts the pycnocline only within roughly the
# baroclinic deformation radius of the coast (~10-30 km off CA); the
# shoaling term decays offshore on this scale. Beyond it the seasonal
# thermocline RELAXES DOWN toward its open-ocean depth — offshore CA
# Current summer mixed layers sit well below the upwelled nearshore
# pycnocline. Without these two terms the v1.0 model shoaled the
# cliff everywhere the wind blew (and winds are stronger offshore),
# producing a wrong-signed offshore gradient.
UPWELLING_DECAY_KM = 25.0
OFFSHORE_DEEPEN_FT = 20.0
OFFSHORE_DEEPEN_KM = 40.0

CLIFF_MIN_FT = 10.0
CLIFF_MAX_FT = 80.0

# ---- Internal-tide cliff swing -------------------------------------------

# Base peak-to-peak diurnal swing of the cliff depth (ft), plus extra
# swing when stratification is strong (internal tides ride the
# pycnocline — stronger stratification, larger displacement).
SWING_BASE_FT = 6.0
SWING_STRATIFICATION_EXTRA_FT = 4.0
# Season-strength normalization: 1.0 when the cliff base is at its
# summer-shallow extreme, 0.0 at the winter-deep extreme.
SWING_SEASON_SHALLOW_FT = 22.0
SWING_SEASON_DEEP_FT = 50.0

# Semidiurnal lunar period drives the dominant internal tide.
M2_PERIOD_HOURS = 12.42
# v1 phase assumption: the cliff sits DEEPEST near high water
# (downwelling phase of the internal tide at the coast) — a
# documented guess for C4 to confirm or flip (DECISIONS.md).
PHASE_DEEPEST_AT_HIGH_WATER = True

# ---- Below-cliff visibility ----------------------------------------------

# Fraction of surface visibility that survives below the cliff in calm,
# non-upwelling conditions...
BELOW_ATTENUATION_BASE = 0.40
# ...reduced when upwelled nutrient water (plankton) or resuspended
# sediment occupies the lower layer.
BELOW_ATTENUATION_UPWELLING = 0.15
BELOW_ATTENUATION_RESUSPENSION = 0.15
BELOW_ATTENUATION_MIN = 0.10
BELOW_ATTENUATION_MAX = 0.55

BELOW_VIS_FLOOR_FT = 3.0
