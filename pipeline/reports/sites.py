"""Shore-dive sites, boat ranges, and the buoys the report reads.

A boat range is rated on its own forecast zone plus every zone the run from
San Diego crosses (`route_zones`), factor by factor, keeping the worst.

`faces_deg` is the direction the entry looks out to sea (a north-facing cove
faces 0). A swell coming FROM within 60 degrees of that direction hits the
entry head-on; 60-100 is oblique; beyond 100 the site is in the swell's lee.
These are approximate orientations from the chart, registered as provisional
in pipeline/validation/knobs_registry.json (reports.site_exposure).
"""
from __future__ import annotations

SHORE_SITES = [
    {
        "id": "la-jolla-cove", "name": "La Jolla Cove", "short": "La Jolla Cove", "faces_deg": 330,
        "surf_zone": "CAZ043", "land_zone": "CAZ043", "marine_zone": "PZZ740",
        "buoy": "46254", "depth_m": 10, "beach_entry": True,
        "note": "North-facing; Point La Jolla blocks most south swell.",
    },
    {
        "id": "la-jolla-shores", "name": "La Jolla Shores (Vallecitos, Marine Room)", "short": "La Jolla Shores", "faces_deg": 275,
        "surf_zone": "CAZ043", "land_zone": "CAZ043", "marine_zone": "PZZ740",
        "buoy": "46254", "depth_m": 12, "beach_entry": True,
        "note": "West-facing beach entry; south swell wraps in obliquely.",
    },
    {
        "id": "point-loma-shore", "name": "Point Loma and Sunset Cliffs (shore entries)", "short": "Point Loma and Sunset Cliffs", "faces_deg": 250,
        "surf_zone": "CAZ043", "land_zone": "CAZ043", "marine_zone": "PZZ740",
        "buoy": "46232", "depth_m": 10, "beach_entry": False,
        "note": "Rocky west/southwest-facing entries, open to south and west swell.",
    },
    {
        "id": "laguna", "name": "Laguna Beach (Shaws Cove, Crescent Bay)", "short": "Laguna Beach", "faces_deg": 235,
        "surf_zone": "CAZ552", "land_zone": "CAZ552", "marine_zone": "PZZ655",
        "buoy": "46253", "depth_m": 10, "beach_entry": True,
        "note": "Southwest-facing coves, open to south swell. Nearest live buoy is San Pedro South, about 25 nm away.",
    },
]

BOAT_RANGES = [
    {
        "id": "inshore-sd", "name": "San Diego inshore (Point Loma and La Jolla kelp)", "short": "San Diego inshore",
        "office": "SGX", "zone": "PZZ740", "alert_zones": ["PZZ740"], "crossing": False, "kind": "inshore",
    },
    {
        "id": "coronados", "name": "Coronado Islands (Mexican waters)", "short": "the Coronados",
        "office": "NHC", "zone": "PMZ009", "route_zones": ["PZZ740"], "alert_zones": ["PZZ740"], "crossing": False, "kind": "coronados",
        "note": "Forecast is NHC zone PMZ009 (border to 30N within 60 nm). Passport, Mexican fishing licenses and CBP arrival reporting apply.",
    },
    {
        "id": "banks", "name": "Offshore banks 10-60 nm (9 Mile, the 43, 302, 371, 425)", "short": "the offshore banks",
        "office": "SGX", "zone": "PZZ745", "route_zones": ["PZZ740"], "alert_zones": ["PZZ745", "PZZ740"], "crossing": False, "kind": "offshore",
    },
    {
        "id": "catalina", "name": "Catalina and the San Pedro Channel", "short": "Catalina and the San Pedro Channel",
        "office": "LOX", "zone": "PZZ655", "alert_zones": ["PZZ655"], "crossing": True, "kind": "offshore",
    },
    {
        "id": "outer", "name": "Outer waters: San Clemente and San Nicolas Islands", "short": "the outer islands",
        "office": "LOX", "zone": "PZZ676", "route_zones": ["PZZ745"], "alert_zones": ["PZZ676", "PZZ745"], "crossing": True, "kind": "offshore",
        "note": "Check the Navy's San Clemente Island range (SHOBA) closures before going.",
    },
]

# Buoys in the "The water" table, near to far. CDIP stations publish waves and
# surface temperature only; NDBC 46086/46047 also carry wind.
BUOYS = [
    {"id": "46254", "name": "Scripps Nearshore (La Jolla)", "lat": 32.868, "lng": -117.267},
    {"id": "46232", "name": "Point Loma South", "lat": 32.517, "lng": -117.425},
    {"id": "46258", "name": "Mission Bay West", "lat": 32.749, "lng": -117.502},
    {"id": "46225", "name": "Torrey Pines Outer", "lat": 32.933, "lng": -117.391},
    {"id": "46253", "name": "San Pedro South", "lat": 33.576, "lng": -118.181},
    {"id": "46086", "name": "San Clemente Basin", "lat": 32.499, "lng": -118.034},
    {"id": "46047", "name": "Tanner Bank", "lat": 32.433, "lng": -119.533},
]

# Boat-dive sites for the surge-at-depth table.
SURGE_SITES = [
    {"name": "La Jolla Cove", "buoy": "46254", "depth_m": 10},
    {"name": "La Jolla Canyon wall", "buoy": "46254", "depth_m": 20},
    {"name": "Point Loma kelp", "buoy": "46232", "depth_m": 15},
    {"name": "Wreck Alley", "buoy": "46258", "depth_m": 20},
    {"name": "Laguna Beach coves", "buoy": "46253", "depth_m": 10},
]

TIDE_STATION = "la-jolla"
COOPS_TEMP = [
    {"id": "9410230", "name": "Scripps Pier, La Jolla"},
    {"id": "9410170", "name": "San Diego Bay (Broadway Pier)"},
]
SUN_LATLNG = (32.7157, -117.1611)  # San Diego
RAIN_GAUGE = {"station": "SAN", "network": "CA_ASOS", "name": "San Diego airport (KSAN)"}
THERMOCLINE_SPOTS = ["lajolla", "pointloma", "coronados"]
ALERT_ZONES = sorted({z for r in BOAT_RANGES for z in r["alert_zones"]} | {"CAZ043", "CAZ552", "PZZ655"})
