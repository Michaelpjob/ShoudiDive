"""Florida + Gulf + Caribbean region config — SKELETON.

Proposed values from ``docs/expansion-regions.md`` § 3. Two sub-bbox
splits (``gulf_se`` + ``caribbean``) because a single bbox would
push past the runtime + data-volume caps for one matrix job. The
frontend still presents this as one "Tropical" region; the pipeline
fans out at refresh time.

## What's intentionally missing

* DriverCoefficients — the existing ``chl_based`` viz model is the
  wrong shape for tropical water (oligotrophic, low chl, vis is
  driven by Sahara dust + hurricane stirring + plumes, not by
  chlorophyll concentration). The doc proposes a new
  ``subtractive_tropical`` variant; the formula lives in
  ``viz_predict/`` after PR-TROP-5 lands. This file marks the
  variant slot so the wiring is ready when the math arrives.
* HYCOM + Global RTOFS currents fetcher — PR-TROP-2.
* NASA GEOS-FP Saharan dust fetcher — PR-TROP-3.
* NOAA NHC hurricane track overlay — PR-TROP-4.
* International MPA polygons (WDPA + NMS) — PR-TROP-6.
* Spot pins (Looe Key, Stuart Cove, Bonaire, etc.) — PR-TROP-7.

DO NOT consume this region yet. The ``viz_model_variant`` value is a
forward-declaration that the test suite verifies as a valid Literal,
not a contract that the formula is implemented.
"""
from __future__ import annotations

from ._region import Region


# Outer hull of the two sub-region bboxes — the frontend's region
# switcher only knows the overall bounds; the per-subregion fetch
# jobs handle the actual data fetching.
_HULL_BBOX = dict(lat_min=10.0, lat_max=31.0, lng_min=-98.0, lng_max=-60.0)


REGION = Region(
    name="tropical",
    display_name="Florida + Caribbean",
    bbox=_HULL_BBOX,
    subregion_bboxes={
        # TX coast + FL Gulf + FL Keys + Flower Garden Banks +
        # western Cuba + Yucatan
        "gulf_se":   dict(lat_min=18.0, lat_max=31.0, lng_min=-98.0, lng_max=-80.0),
        # Greater + Lesser Antilles + Bahamas + ABCs + Trinidad
        "caribbean": dict(lat_min=10.0, lat_max=24.0, lng_min=-85.0, lng_max=-60.0),
    },
    lat_zone_bounds={
        # Several zones are longitude-gated (`gulf_fl` vs `fl_east`
        # overlap in latitude). The current ``classify_zone`` walks
        # lat-only; PR-TROP-1 will add a longitude-aware path before
        # honoring these. For now the placeholder values here let the
        # package import + scaffold tests pass.
        "fl_east":       (25.5, 31.0),
        "fl_keys":       (24.0, 25.5),
        "carib_greater": (18.0, 24.0),
        "carib_lesser":  (10.0, 18.0),
    },
    # ``reefs`` + ``banks`` + ``walls`` capture more useful structure
    # in tropical water than the CA ``nearshore`` / ``islands`` /
    # ``offshore`` split — but renaming the dist labels requires a
    # coordinated viz_predict/config.py update. Stick with the CA
    # labels for the scaffold; PR-TROP-5 will revisit when the
    # subtractive viz model lands.
    dist_labels=["nearshore", "islands", "offshore"],
    viz_model_variant="subtractive_tropical",
    data_dir_slug="tropical",
    layer_range_overrides={
        # Tropical SST runs 20-32°C year-round (Caribbean rarely below
        # 25, summer up to 32). The CA default (9-25°C) clipped
        # everything above 25 to a flat saturated red on the frontend.
        # 20-32 lets the colormap actually spread across the realistic
        # surface-temp window.
        "sst":   (20.0, 32.0),
        "sst7d": (20.0, 32.0),
        "sst5d": (20.0, 32.0),
    },
    # NOAA CO-OPS stations across FL, FL Keys, PR, USVI. Caribbean
    # tides are small (~0.3 m range typical, semi-diurnal) so the
    # tide_index signal will be weaker than CA/PNW, but still
    # captures spring-vs-neap variation. Station IDs verified against
    # https://tidesandcurrents.noaa.gov/stations.html as of 2026-05-13.
    tide_stations=[
        # Florida Gulf + Keys
        {"name": "naples-fl",        "id": "8725110", "lat": 26.131, "lng": -81.808},
        {"name": "fort-myers-fl",    "id": "8725520", "lat": 26.647, "lng": -81.871},
        {"name": "vaca-key-fl",      "id": "8723970", "lat": 24.711, "lng": -81.107},
        {"name": "key-west-fl",      "id": "8724580", "lat": 24.555, "lng": -81.808},
        # Florida east coast
        {"name": "virginia-key-fl",  "id": "8723214", "lat": 25.732, "lng": -80.162},
        {"name": "lake-worth-fl",    "id": "8722670", "lat": 26.612, "lng": -80.038},
        # Puerto Rico + USVI
        {"name": "magueyes-island",  "id": "9759110", "lat": 17.970, "lng": -67.046},
        {"name": "san-juan-pr",      "id": "9755371", "lat": 18.460, "lng": -66.118},
        {"name": "lime-tree-bay",    "id": "9751381", "lat": 17.692, "lng": -64.754},
        {"name": "christiansted",    "id": "9751364", "lat": 17.748, "lng": -64.705},
        {"name": "lameshur-bay",     "id": "9751401", "lat": 18.318, "lng": -64.724},
    ],
    notes=(
        "SKELETON — bbox hull + sub-region bboxes + viz variant marker. "
        "The chl-based model is the WRONG SHAPE for this water; do not "
        "enable in production until PR-TROP-5 implements the new "
        "subtractive_tropical formula in viz_predict/."
    ),
)
