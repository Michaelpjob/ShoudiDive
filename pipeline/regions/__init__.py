"""Region-aware pipeline config (PR-X-1 scaffold, additive-only).

This package is the foundation for `docs/expansion-regions.md`. v1 ships
a single `ca` region that snapshots the CURRENT hardcoded behavior;
`pnw` and `tropical` are skeleton placeholders with the proposed bbox
+ zone bounds from the scoping doc, intentionally left thin so a later
PR (PR-X-2 / PR-X-3) can fill them in alongside the actual fetcher
wire-up.

## Why scaffold-only

Today, bbox and zone bounds are duplicated across:
  * `pipeline/fetch.py`, `chl_blend.py`, `fetch_bathy.py`, …
  * `pipeline/viz_predict/config.py`
  * `src/lib/mapData.js`

Rewiring all of those in one PR is a multi-hundred-LOC change with
real regression risk. PR-X-1 is *additive*: this package exists,
exports a stable interface, and is unused by the running pipeline.
PR-X-2 / PR-X-3 will migrate consumers one fetch script at a time
behind a `--region` CLI flag, with the `ca` region preserving today's
behavior bit-for-bit.

## Interface

    from regions import get_region, list_regions, active_region

    r = get_region("ca")
    r.bbox          # dict(lat_min, lat_max, lng_min, lng_max)
    r.bbox_array    # [lng_min, lat_min, lng_max, lat_max] — manifest order
    r.lat_zone_bounds  # OrderedDict[str, (low, high)]
    r.dist_labels      # ["nearshore", "islands", "offshore"]
    r.viz_model_variant  # "chl_based" | "subtractive_tropical"
    r.data_dir_slug      # "ca" | "pnw" | "tropical"

`active_region()` resolves from the env var ``SHOULDIDIVE_REGION``
with a default of ``ca`` — matches today's behavior for callers that
import the package but don't pass an explicit name.

## What is NOT here (deliberately)

* DriverCoefficients dictionaries — still in `viz_predict/config.py`.
  Migrate in PR-X-2 once the wiring layer is settled.
* Spot pins — still in `src/lib/savedSpots` + `validation/ingest/
  _spot_lookup.json`. Migrate in PR-X-3.
* Per-region source URLs (HYCOM, SSCOFS, …) — those land with the
  per-region fetcher PRs (PR-PNW-2, PR-TROP-2).
"""
from __future__ import annotations

import os

from ._region import Region
from .ca import REGION as _CA
from .pnw import REGION as _PNW
from .tropical import REGION as _TROPICAL


_REGISTRY: dict[str, Region] = {
    _CA.name:       _CA,
    _PNW.name:      _PNW,
    _TROPICAL.name: _TROPICAL,
}

DEFAULT_REGION = "ca"


def get_region(name: str) -> Region:
    """Look up a region by canonical name. Raises ``KeyError`` on miss
    — callers should fail loud rather than silently fall back to CA.
    """
    if name not in _REGISTRY:
        valid = ", ".join(sorted(_REGISTRY.keys()))
        raise KeyError(
            f"Unknown region {name!r}; valid regions: {valid}. "
            f"Add a new entry under pipeline/regions/ if this is intentional.",
        )
    return _REGISTRY[name]


def list_regions() -> list[str]:
    """Stable-sorted region names. Useful for CI matrix generation."""
    return sorted(_REGISTRY.keys())


def active_region() -> Region:
    """Resolve the region from ``$SHOULDIDIVE_REGION`` (default ``ca``).

    Callers in fetch.py / viz_predict will use this when they don't
    take an explicit `--region` CLI flag, preserving today's behavior
    when the env var is unset.
    """
    return get_region(os.environ.get("SHOULDIDIVE_REGION", DEFAULT_REGION))


__all__ = [
    "Region",
    "get_region",
    "list_regions",
    "active_region",
    "DEFAULT_REGION",
]
