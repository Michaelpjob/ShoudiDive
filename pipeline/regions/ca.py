"""California region config — SoCal staged launch (NorCal deferred).

This is the SoCal-pinned variant of the CA region config. The full NorCal
expansion (latMax = 42.0 + lng_min = -128.5 + norcal_* viz coefficients)
lives on `dev` and ships in a separate, later release once we have:

  1. Dive-shop scrapers covering Eureka / Fort Bragg / Mendocino / Bodega
     Bay so the visibility model has ground-truth observations to
     calibrate `norcal_*` coefficients against.
  2. At least a few weeks of those observations flowing through the
     watchdog without unresolved bias findings.

Until then, this file ships the CA bbox we've been running in prod —
SoCal + Central CA only — and the `viz_predict/config.py` `norcal_*`
zones (already on this branch as code) are dead code that won't fire
because the bbox doesn't reach NorCal latitudes.

What this branch (socal-upgrade) DOES carry to prod for SoCal users:
  * NOAA OISST v2.1 1991-2020 30-year SST climatology (replaces 1-year
    sample baseline that baked the 2024-2025 marine heatwave in)
  * Region-aware encoding ranges so an encode/decode mismatch can't
    silently saturate
  * Land mask on chl + wind so streamlines / heatmap stop at the coast
  * Bathy + climatology sidecar JSONs that force regeneration on any
    future bbox bump
  * Visibility layer marked `beta` (acknowledges the model is unvalidated
    against NorCal even within SoCal lat band the calibration is sparse)
  * 42-test data integrity suite
  * Cloudflare security hardening (CSP, HSTS, scanner-path 404s) +
    CodeQL + Dependabot
"""
from __future__ import annotations

from ._region import Region


REGION = Region(
    name="ca",
    display_name="California",
    # 2026-05-15 — SoCal launch pin. The NorCal expansion (latMax 37.6
    # → 42.0, lngMin -124.0 → -128.5) lives on `dev` and ships in its
    # own later release once NorCal ground-truth scrapers are landed.
    # See this file's module docstring for the unblock criteria.
    bbox=dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8),
    lat_zone_bounds={
        # Insertion order matters — `classify_zone` walks the dict
        # from the highest lower-bound down. Keep this order
        # north-to-south so the walk picks the matching band on the
        # first hit. The `norcal` band is kept in the dict (matches
        # viz_predict/config.py) but won't be reached given the bbox
        # latMax = 37.6 — it's harmless dead code until NorCal launch.
        "norcal":     (36.00, 90.0),
        "central":    (34.45, 36.00),
        "transition": (33.70, 34.45),
        "bight":      (-90.0, 33.70),
    },
    dist_labels=["nearshore", "islands", "offshore"],
    viz_model_variant="chl_based",
    data_dir_slug="ca",
    # NOAA CO-OPS stations — even coverage from Monterey to San Diego.
    tide_stations=[
        {"name": "monterey",      "id": "9413450", "lat": 36.605, "lng": -121.888},
        {"name": "port-san-luis", "id": "9412110", "lat": 35.169, "lng": -120.755},
        {"name": "santa-barbara", "id": "9411340", "lat": 34.404, "lng": -119.693},
        {"name": "los-angeles",   "id": "9410660", "lat": 33.720, "lng": -118.272},
        {"name": "la-jolla",      "id": "9410230", "lat": 32.866, "lng": -117.257},
        {"name": "san-diego",     "id": "9410170", "lat": 32.713, "lng": -117.173},
    ],
    notes=(
        "Source of truth for behavior today — bbox + lat zones must match "
        "fetch.py / viz_predict/config.py / src/lib/mapData.js. NorCal "
        "expansion deferred — see module docstring for unblock criteria."
    ),
)
