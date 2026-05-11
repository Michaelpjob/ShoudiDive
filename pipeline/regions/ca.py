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
    bbox=dict(lat_min=31.8, lat_max=42.0, lng_min=-124.6, lng_max=-116.8),
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
    notes=(
        "Source of truth for behavior today — the bbox + lat zones "
        "here match fetch.py / viz_predict/config.py / src/lib/mapData.js. "
        "PR-X-2 will migrate fetch.py to import from here; until then "
        "this is read-only documentation that the test_regions.py "
        "consistency test gates against drift."
    ),
)
