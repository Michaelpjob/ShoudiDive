"""The threshold tables behind every verdict.

Official triggers (NWS headlines, the San Diego steepness rule, rip current
risk) are sourced. The numeric tiers marked PROVISIONAL are our own synthesis
from DAN guidance, dive logs and practitioner consensus; they are registered
in pipeline/validation/knobs_registry.json (reports.thresholds) and presented
to readers as "our thresholds", never as standards. Evidence:
Projects/ShouldIDive/fish-reports-research.md section 6.
"""
from __future__ import annotations

LEVELS = ["within", "marginal", "outside"]
LABELS = {
    "within": "Within our thresholds",
    "marginal": "Marginal",
    "outside": "Outside our thresholds",
    "unknown": "Not enough data",
}

# NWS headlines that put a boat range outside, and the watches that do so only
# for crossings (marginal elsewhere).
MARINE_OUTSIDE_EVENTS = {
    "Small Craft Advisory", "Small Craft Advisory for Hazardous Seas", "Small Craft Advisory for Winds",
    "Small Craft Advisory for Rough Bar", "Gale Warning", "Storm Warning", "Hurricane Force Wind Warning",
    "Hazardous Seas Warning", "Special Marine Warning", "Dense Fog Advisory", "Tropical Storm Warning",
    "Hurricane Warning", "Tsunami Warning", "Tsunami Advisory", "Brisk Wind Advisory",
}
MARINE_WATCH_EVENTS = {"Gale Watch", "Storm Watch", "Hazardous Seas Watch", "Hurricane Force Wind Watch",
                       "Tropical Storm Watch", "Hurricane Watch"}
# Headline text in the zone block (used where the alerts API has no feed, i.e. PMZ009).
MARINE_OUTSIDE_WORDS = ("SMALL CRAFT", "GALE WARNING", "STORM WARNING", "HURRICANE", "HAZARDOUS SEAS",
                        "DENSE FOG", "TROPICAL STORM", "SPECIAL MARINE")
MARINE_WATCH_WORDS = ("GALE WATCH", "STORM WATCH")

# Beach headlines: outside at a site facing the swell, marginal in its lee.
BEACH_EXPOSURE_EVENTS = {"Beach Hazards Statement", "Rip Current Statement"}
BEACH_OUTSIDE_EVENTS = {"High Surf Advisory", "High Surf Warning", "Coastal Flood Warning", "Tsunami Warning",
                        "Tsunami Advisory"}

# Boat ranges (PROVISIONAL): sustained wind upper bound (kt) for within / marginal.
# The research proposal had tighter offshore tiers (10/15 kt); the first live run
# (2026-10-15) showed that marks the ordinary "NW 10 to 15 kt" day marginal at
# every offshore range. Loosened to 15/20, still under the NWS 21 kt advisory.
WIND_KT = {
    "inshore": (15, 20),
    "coronados": (15, 20),
    "offshore": (15, 20),
}
GUST_OUTSIDE_KT = {"inshore": 30, "coronados": 25, "offshore": 25}
# Combined seas upper bound (ft) for within / marginal.
SEAS_FT = {
    "inshore": (5, 7),
    "coronados": (4, 7),
    "offshore": (4, 7),
}
# Santa Ana pattern: an E/NE wind with gusts or sustained at these speeds.
SANTA_ANA_DIRS = {"NNE", "NE", "ENE", "E", "ESE"}
SANTA_ANA_GUST_KT = 20
SANTA_ANA_SUSTAINED_KT = 20
SANTA_ANA_LEVEL = {"inshore": "marginal", "coronados": "outside", "offshore": "outside"}
FOG_LEVEL = {"inshore": "marginal", "coronados": "outside", "offshore": "outside"}

# NWS San Diego Small Craft Advisory steepness rule (WR supplement 12-2003,
# appendix A): for 6-10 ft seas an SCA applies when height (ft) >= period (s);
# 11 ft or more at any period. Our PROVISIONAL marginal tier: any train whose
# period is under twice its height (short, steep chop).
SCA_STEEP_MIN_FT = 6
SCA_ANY_PERIOD_FT = 11

# Shore dive (PROVISIONAL): breaking surf at the site, sets included.
SURF_FT = (2.5, 4.5)  # < 2.5 within, < 4.5 marginal, else outside
EXPOSURE_SCALE = {"into": 1.0, "oblique": 0.6, "lee": 0.35}
EXPOSURE_INTO_DEG = 60
EXPOSURE_OBLIQUE_DEG = 100
MOP_LEE_RATIO = 0.4        # MOP sets under 40% of the county's: the entry is sheltered
MOP_INTO_RATIO = 0.75      # 75%+ of the county's: the entry takes the swell head-on
LONG_PERIOD_S = 13          # at a site facing the swell, long-period swell raises the surf rating one level
ONSHORE_WIND_KT = (15, 20)  # wind blowing into the entry: marginal at 15, outside at 20
ONSHORE_WITHIN_DEG = 60
RAIN_ADVISORY_IN = 0.2      # San Diego County: avoid ocean contact for 72 h after rain
RAIN_WINDOW_H = 72

# Boat dive (DAN): current at the site.
CURRENT_TEXT = ("Current is not measured at dive depth by any public feed. Within our thresholds up to 0.5 kt; "
                "0.5-1 kt needs descent and trail lines; over 1 kt only with training; 2 kt is beyond anyone's "
                "ability to hold position (DAN). 1 kt is about 1.7 ft per second.")
FREEDIVE_TEXT = ("Solo freediving is outside our thresholds in any conditions: one up, one down. Never "
                 "hyperventilate. Size exposure protection for the bottom temperature, not the surface.")

# Inputs older than this are treated as missing.
MAX_AGE_H = {
    "alerts": 3, "cwf": 18, "off": 18, "srf": 30, "buoy": 6, "coops": 6, "rain": 48,
    "manifest": 48, "observations": 120, "enso": 24 * 45, "tides": 48, "viz_column": 48, "mop": 36,
    "glider": 36, "fleet_now": 6, "hist": 48, "glider_series": 60, "enso_weekly": 24 * 14,
}

BANNED_WORDS = ("safe", "unsafe", "all clear", "guaranteed", "go/no-go")


def worst(levels: list[str]) -> str:
    known = [lv for lv in levels if lv in LEVELS]
    if not known:
        return "unknown"
    return max(known, key=LEVELS.index)


def raise_one(level: str) -> str:
    if level not in LEVELS:
        return level
    return LEVELS[min(LEVELS.index(level) + 1, len(LEVELS) - 1)]


def lower_one(level: str) -> str:
    if level not in LEVELS:
        return level
    return LEVELS[max(LEVELS.index(level) - 1, 0)]


def combine(factors: list[dict]) -> str:
    """Worst of the factors; a required factor with no data makes the verdict
    'unknown' unless something else already puts it outside."""
    levels = [f["level"] for f in factors]
    w = worst(levels)
    if w == "outside":
        return "outside"
    if any(f["level"] == "unknown" and f.get("required", True) for f in factors):
        return "unknown"
    return w
