"""Shared constants for the fleet-AIS model (see README.md)."""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
# Big raw/intermediate data lives OUTSIDE the repo (tens of GB of AIS).
DATA_DIR = os.environ.get("FLEET_DATA_DIR") or os.path.abspath(
    os.path.join(_HERE, "..", "..", "ShoudiDive-fleet-data"))

# SoCal cut: Point Conception to the Mexican border and ~150 nm offshore, which
# is as far as any terrestrial receiver could possibly hear. south, west, north, east.
BBOX = (31.0, -121.5, 34.6, -116.9)

LOCAL_TZ = "America/Los_Angeles"

# Sportfishing landings (dock positions, WGS84). These seed the roster
# discovery (which vessels sleep at which dock) and define "in port". The
# dock positions were refined 2026-10-05 to the median overnight berth of the
# boats matched at each landing. The three Point Loma landings share one basin and are one cluster for trip
# segmentation; the roster still records the specific landing by berth.
LANDINGS = {
    "H&M Landing":                 (32.7243, -117.2268, "San Diego"),
    "Fisherman's Landing":         (32.7257, -117.2292, "San Diego"),
    "Point Loma Sportfishing":     (32.7227, -117.2265, "San Diego"),
    "Seaforth Sportfishing":       (32.7623, -117.2362, "Mission Bay"),
    "Oceanside Sea Center":        (33.2074, -117.3904, "Oceanside"),
    "Helgren's Sportfishing":      (33.2068, -117.3912, "Oceanside"),
    "Dana Wharf Sportfishing":     (33.4598, -117.6984, "Dana Point"),
    "Davey's Locker":              (33.6032, -117.9290, "Newport Beach"),
    "Newport Landing":             (33.6040, -117.9300, "Newport Beach"),
    "Long Beach Sportfishing":     (33.7657, -118.2162, "Long Beach"),
    "Pierpoint Landing":           (33.7603, -118.1932, "Long Beach"),
    "22nd Street Landing":         (33.7250, -118.2805, "San Pedro"),
    "LA Waterfront Sportfishing":  (33.7418, -118.2795, "San Pedro"),
    "Redondo Sportfishing":        (33.8447, -118.3937, "Redondo Beach"),
    "Marina del Rey Sportfishing": (33.9733, -118.4473, "Marina del Rey"),
    "Channel Islands Sportfishing":(34.1636, -119.2231, "Oxnard"),
    "Hook's Landing":              (34.1681, -119.2246, "Oxnard"),
    "Ventura Sportfishing":        (34.2466, -119.2656, "Ventura"),
    "Sea Landing":                 (34.4041, -119.6916, "Santa Barbara"),
}

# Trip / stop model knobs (documented in README.md "Method").
HARBOR_KM = 2.5          # within this of any landing = in harbor (bay, channel, bait receiver)
NEARSHORE_KM = 6.0       # stops closer than this to a dock are bait grounds / kelp edge: flagged, excluded from the answers by default
BERTH_KM = 0.6           # within this of its own berth = tied up
MIN_TRIP_HOURS = 1.5     # shorter excursions are moves / bait runs
MIN_TRIP_KM = 3.0        # must get at least this far from the dock
GAP_BREAK_MIN = 15       # a run of positions breaks at a gap longer than this
DRIFT_MAX_KT = 2.0       # below this = anchored / drifting
DRIFT_MIN_MIN = 10       # minimum drift/anchor stop length
TROLL_KT = (3.5, 8.5)    # trolling speed band
TROLL_MIN_MIN = 20       # minimum trolling run length
TROLL_MAX_STRAIGHTNESS = 0.7  # net displacement / path length; transits are ~1.0
H3_RES = 7               # ~1.4 km edge, ~5 km2 cells (display + scoring)
H3_RES_FINE = 8          # ~0.5 km edge, used for the "same spot" test
