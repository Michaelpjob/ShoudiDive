"""California region config — snapshot of today's hardcoded behavior.

Mirror of the duplicated constants in ``pipeline/fetch.py``,
``pipeline/chl_blend.py``, ``pipeline/fetch_bathy.py``,
``src/lib/mapData.js``, and ``pipeline/viz_predict/config.py``. The
test in ``pipeline/tests/test_regions.py`` cross-checks this file
against ``fetch.py`` and ``viz_predict/config.py``; if you bump the
bbox or the zone bounds in one place, the test will tell you the
other place is now drifting.

The 2026-05-10 NorCal expansion (PR-NC-1 + the bbox bumps in
``docs/expansion-norcal.md``) is already reflected here.
"""
from __future__ import annotations

from ._region import Region


REGION = Region(
    name="ca",
    display_name="California",
    # bbox history:
    # 2026-05-13: lng_min -124.6 → -127.0 to show ~270 km of Pacific
    #             west of Cape Mendocino.
    # 2026-05-14: lng_min -127.0 → -128.5 (NorCal pre-launch). The
    #             previous -127 left the Cape Mendocino / Crescent
    #             City coastline (~-124.4) crowded against the west
    #             edge once the aspect-ratio-locked map rendered. An
    #             extra 1.5° of westward room (~130 km at mid-lat)
    #             gives NorCal divers visible upwelling-zone water
    #             between the coast and the bbox edge.
    #             Aspect ratio at mid-lat 36.9°:
    #               11.7° wide × cos(36.9°) ≈ 9.36 effective wide
    #               10.2° tall                = 10.2 effective tall
    #               → 0.92 (still slightly taller than wide; mobile
    #               portrait UX unchanged).
    bbox=dict(lat_min=31.8, lat_max=42.0, lng_min=-128.5, lng_max=-116.8),
    lat_zone_bounds={
        # Insertion order matters — `classify_zone` walks the dict
        # from the highest lower-bound down. Keep this order
        # north-to-south so the walk picks the matching band on the
        # first hit.
        "norcal":     (36.00, 90.0),
        "central":    (34.45, 36.00),
        "transition": (33.70, 34.45),
        "bight":      (-90.0, 33.70),
    },
    dist_labels=["nearshore", "islands", "offshore"],
    viz_model_variant="chl_based",
    data_dir_slug="ca",
    # NOAA CO-OPS stations — even coverage from Monterey to San Diego
    # (mirrors the hardcoded list previously living in fetch_tides.py).
    tide_stations=[
        {"name": "monterey",      "id": "9413450", "lat": 36.605, "lng": -121.888},
        {"name": "port-san-luis", "id": "9412110", "lat": 35.169, "lng": -120.755},
        {"name": "santa-barbara", "id": "9411340", "lat": 34.404, "lng": -119.693},
        {"name": "los-angeles",   "id": "9410660", "lat": 33.720, "lng": -118.272},
        {"name": "la-jolla",      "id": "9410230", "lat": 32.866, "lng": -117.257},
        {"name": "san-diego",     "id": "9410170", "lat": 32.713, "lng": -117.173},
    ],
    notes=(
        "Source of truth for behavior today — the bbox + lat zones "
        "here match fetch.py / viz_predict/config.py / src/lib/mapData.js. "
        "PR-X-2 will migrate fetch.py to import from here; until then "
        "this is read-only documentation that the test_regions.py "
        "consistency test gates against drift."
    ),
)
