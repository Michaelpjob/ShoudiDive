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
    notes=(
        "SKELETON — bbox hull + sub-region bboxes + viz variant marker. "
        "The chl-based model is the WRONG SHAPE for this water; do not "
        "enable in production until PR-TROP-5 implements the new "
        "subtractive_tropical formula in viz_predict/."
    ),
)
