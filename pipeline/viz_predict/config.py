"""All tunable parameters for the visibility prediction algorithm."""
from dataclasses import dataclass
from typing import Dict


# Latitude zone boundaries (deg N).
LAT_ZONE_BOUNDS = {
    "central":    (34.45, 90.0),
    "transition": (33.70, 34.45),
    "bight":      (-90.0, 33.70),
}

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
    "central_nearshore": 1.5, "central_islands": 2.5, "central_offshore": 4.5,
    "transition_nearshore": 2.0, "transition_islands": 3.0, "transition_offshore": 5.0,
    "bight_nearshore": 2.5, "bight_islands": 3.5, "bight_offshore": 6.0,
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
    # Nearshore zones unchanged — observed mismatch with Tempbreak was
    # confined to offshore + islands (model was too pessimistic out there).
    "central_nearshore":  DriverCoefficients(upwell=0.18, swell=0.30, precip=0.20, river=0.30, sst=-0.06, seasonal=0.40, exposure=0.20, tide=0.10, substrate=0.15, cloud=-0.08),
    "central_islands":    DriverCoefficients(upwell=0.12, swell=0.10, precip=0.05, river=0.05, sst=-0.05, seasonal=0.35, exposure=0.30, tide=0.02, substrate=0.05, cloud=-0.06),

    # Offshore + islands: seasonal / upwell / exposure dialed down. The
    # California Current keeps offshore pixels decoupled from spring
    # nearshore upwelling, so the seasonal climatology signal was
    # over-weighted; exposure was the largest single coefficient and was
    # over-penalising deep-water pixels that just happen to fall inside
    # the 10 km island radius. See offshore-calibration.md for the full
    # before/after table and rationale.
    "central_offshore":   DriverCoefficients(upwell=0.08, swell=0.02, precip=0.00, river=0.00, sst=-0.04, seasonal=0.18, exposure=0.03, tide=0.00, substrate=0.00, cloud=-0.04),

    "transition_nearshore": DriverCoefficients(upwell=0.10, swell=0.25, precip=0.18, river=0.28, sst=-0.04, seasonal=0.30, exposure=0.18, tide=0.08, substrate=0.12, cloud=-0.06),
    "transition_islands":   DriverCoefficients(upwell=0.06, swell=0.08, precip=0.04, river=0.04, sst=-0.03, seasonal=0.16, exposure=0.22, tide=0.02, substrate=0.05, cloud=-0.05),
    "transition_offshore":  DriverCoefficients(upwell=0.04, swell=0.02, precip=0.00, river=0.00, sst=-0.02, seasonal=0.12, exposure=0.03, tide=0.00, substrate=0.00, cloud=-0.03),

    "bight_nearshore":  DriverCoefficients(upwell=0.05, swell=0.20, precip=0.16, river=0.25, sst=-0.02, seasonal=0.20, exposure=0.15, tide=0.10, substrate=0.18, cloud=-0.04),
    "bight_islands":    DriverCoefficients(upwell=0.03, swell=0.06, precip=0.03, river=0.03, sst=-0.02, seasonal=0.12, exposure=0.20, tide=0.02, substrate=0.05, cloud=-0.03),
    "bight_offshore":   DriverCoefficients(upwell=0.02, swell=0.01, precip=0.00, river=0.00, sst=-0.01, seasonal=0.08, exposure=0.02, tide=0.00, substrate=0.00, cloud=-0.02),
}


SIGMA_LOG_CHL: Dict[str, float] = {
    "central_nearshore": 0.55, "central_islands": 0.45, "central_offshore": 0.35,
    "transition_nearshore": 0.50, "transition_islands": 0.40, "transition_offshore": 0.32,
    "bight_nearshore": 0.45, "bight_islands": 0.38, "bight_offshore": 0.30,
}


@dataclass
class SecchiCoefficients:
    a: float = 7.0
    b: float = 0.30


SECCHI_COEFFS: Dict[str, SecchiCoefficients] = {
    # secchi_m = a · chl^(-b). The exponent `b` comes from coastal-CA
    # literature and is left alone; only the multiplier `a` is tuned.
    # Offshore + islands bumped up so the baseline at chl=0.15 mg/m³ lands
    # at ~17 m / 56 ft instead of ~14 m / 47 ft — closer to what divers
    # actually report on stable offshore days.
    "central_nearshore":    SecchiCoefficients(a=4.0,  b=0.28),
    "central_islands":      SecchiCoefficients(a=6.5,  b=0.30),
    "central_offshore":     SecchiCoefficients(a=10.0, b=0.32),  # was 8.5
    "transition_nearshore": SecchiCoefficients(a=4.5,  b=0.28),
    "transition_islands":   SecchiCoefficients(a=8.5,  b=0.30),  # was 7.0
    "transition_offshore":  SecchiCoefficients(a=10.0, b=0.32),  # was 8.5
    "bight_nearshore":      SecchiCoefficients(a=5.0,  b=0.28),
    "bight_islands":        SecchiCoefficients(a=9.0,  b=0.30),  # was 7.5
    "bight_offshore":       SecchiCoefficients(a=10.0, b=0.32),  # was 8.5
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
    "central_nearshore":    TurbidityCorrections(swell=8.0, runoff=4.0, river=5.0, kelp=2.0, substrate=2.5, tide=1.5),
    "central_islands":      TurbidityCorrections(swell=2.0, runoff=0.5, river=0.5, kelp=1.0, substrate=0.5, tide=0.2),
    "central_offshore":     TurbidityCorrections(swell=0.0, runoff=0.0, river=0.0, kelp=0.0, substrate=0.0, tide=0.0),
    "transition_nearshore": TurbidityCorrections(swell=6.0, runoff=3.0, river=4.5, kelp=2.0, substrate=2.0, tide=1.2),
    "transition_islands":   TurbidityCorrections(swell=1.5, runoff=0.4, river=0.4, kelp=1.0, substrate=0.5, tide=0.2),
    "transition_offshore":  TurbidityCorrections(swell=0.0, runoff=0.0, river=0.0, kelp=0.0, substrate=0.0, tide=0.0),
    "bight_nearshore":      TurbidityCorrections(swell=5.0, runoff=3.5, river=4.0, kelp=2.0, substrate=2.5, tide=1.5),
    "bight_islands":        TurbidityCorrections(swell=1.0, runoff=0.3, river=0.3, kelp=1.0, substrate=0.4, tide=0.2),
    "bight_offshore":       TurbidityCorrections(swell=0.0, runoff=0.0, river=0.0, kelp=0.0, substrate=0.0, tide=0.0),
}


CHL_MIN_MGPM3 = 0.03
CHL_MAX_MGPM3 = 50.0
SECCHI_MIN_M  = 1.0
SECCHI_MAX_M  = 25.0


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
