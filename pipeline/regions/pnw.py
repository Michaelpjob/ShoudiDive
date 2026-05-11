"""Pacific Northwest region config — SKELETON, not yet calibrated.

Proposed values from ``docs/expansion-regions.md`` § 2. The bbox and
lat-zone bounds are placeholders sufficient for the package to import
and for CI to validate the multi-region scaffold; coefficients,
spot pins, and per-pixel masks (the Salish Sea polygon) come in the
PR-PNW-N series.

## What's intentionally missing

* ``wa_inland`` zone — defined by a Salish Sea polygon, not a lat
  band. ``classify_zone`` needs a polygon-aware path before this
  zone can be honored. PR-PNW-1 will add the polygon + a per-pixel
  inland mask; until then we treat the entire bbox as outer-coast
  zones and accept the inland classification will be wrong.
* DriverCoefficients — chl-based model fits the OR/WA outer coast
  but not the Salish Sea. PR-PNW-3 introduces a ``pnw_inland``
  variant (Option A in the scoping doc).
* SSCOFS / HFRNet fetchers — PR-PNW-2.
* Olympic Coast NMS + WA DNR Aquatic Reserves + OR Marine Reserves
  polygons — PR-PNW-4.

DO NOT consume this region from the running pipeline yet. The
``test_regions.py`` smoke test only verifies it imports cleanly and
the bbox is geographically plausible.
"""
from __future__ import annotations

from ._region import Region


REGION = Region(
    name="pnw",
    display_name="Pacific Northwest",
    # Lat 42-49 covers Oregon coast through the Canadian border;
    # lng -127 to -122 includes the Salish Sea complex.
    bbox=dict(lat_min=42.0, lat_max=49.0, lng_min=-127.0, lng_max=-122.0),
    lat_zone_bounds={
        # ``wa_inland`` is intentionally omitted — it's a polygon
        # zone, not a lat band. See module docstring.
        "wa_outer":  (46.30, 49.00),
        "or_north":  (44.00, 46.30),
        "or_south":  (42.00, 44.00),
    },
    # Same labels as CA for now — PR-PNW-3 may re-label `islands`
    # to `san_juans` if that ends up more readable in the
    # DriverCoefficients dict.
    dist_labels=["nearshore", "islands", "offshore"],
    viz_model_variant="chl_based",
    data_dir_slug="pnw",
    layer_range_overrides={
        # PNW water is colder. CA range (9-25°C) wastes ~half the
        # encoding band on temperatures we'll never see in the
        # Olympic Coast / Salish Sea / OR outer coast. 5-20°C covers
        # the realistic surface-temp window for this region.
        "sst":   (5.0, 20.0),
        "sst7d": (5.0, 20.0),
        "sst5d": (5.0, 20.0),
    },
    notes=(
        "SKELETON — bbox + lat bands only. Salish Sea polygon, "
        "SSCOFS fetcher, and PNW-tuned coefficients land in "
        "PR-PNW-1..4. Do not enable in production routing until "
        "those PRs ship."
    ),
)
