"""``Region`` dataclass — the shape every region must produce.

Lives in its own module so test fixtures and per-region files can
import it without circular-import gymnastics through the package
``__init__``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


VizModelVariant = Literal["chl_based", "subtractive_tropical"]


@dataclass(frozen=True)
class Region:
    """Per-region configuration consumed by the pipeline + frontend.

    Required:
        name              Short canonical slug (``ca``, ``pnw``,
                          ``tropical``). Doubles as the data-dir
                          subfolder under ``public/data/``.
        display_name      Human-readable name for the region switcher
                          chip in the frontend.
        bbox              Geographic bbox in dict form. Pipeline
                          fetchers consume this directly.
        lat_zone_bounds   Insertion-ordered dict ``{name: (low, high)}``.
                          ``classify_zone`` walks this generically; a
                          new band only needs an entry here.
        dist_labels       Distance-band labels (``["nearshore",
                          "islands", "offshore"]`` in CA). The
                          DriverCoefficients dict keys these labels
                          against lat-zone names; renaming requires a
                          coordinated viz_predict/config.py update.
        viz_model_variant ``chl_based`` (CA/PNW outer) or
                          ``subtractive_tropical`` (FL/Caribbean).
                          Controls which formula viz_predict picks.
        data_dir_slug     Folder under ``public/data/`` where this
                          region's PNGs land. Allows CA/PNW/tropical
                          to coexist without overwrite.

    Optional / forward-looking:
        subregion_bboxes  For regions that need multiple fetch passes
                          to stay under data volume / runtime caps
                          (``tropical`` splits into ``gulf_se`` +
                          ``caribbean``). Empty for single-bbox regions.
        notes             Free-text. Useful for "this is a placeholder,
                          fill before PR-X-N" markers in the skeletons.
    """

    name: str
    display_name: str
    bbox: dict
    lat_zone_bounds: dict
    dist_labels: list
    viz_model_variant: VizModelVariant
    data_dir_slug: str
    subregion_bboxes: dict = field(default_factory=dict)
    notes: str = ""

    @property
    def bbox_array(self) -> list[float]:
        """Manifest-order bbox: ``[lng_min, lat_min, lng_max, lat_max]``.

        Frontend ``manifest.json`` reads this shape; using a method
        avoids stashing two representations in the dataclass body.
        """
        b = self.bbox
        return [b["lng_min"], b["lat_min"], b["lng_max"], b["lat_max"]]

    def data_output_dir(self, repo_root) -> "Path":  # type: ignore[name-defined]
        """Where this region's data PNGs / JSON outputs live under ``public/data/``.

        Convention:
          * CA stays at ``public/data/`` for backward compatibility
            (every existing PNG path the frontend expects).
          * Every other region nests under ``public/data/<slug>/``.

        The directory is created on access so callers can write to
        it immediately without their own mkdir bookkeeping.
        """
        from pathlib import Path

        root = Path(repo_root)
        base = root / "public" / "data"
        if self.name == "ca":
            base.mkdir(parents=True, exist_ok=True)
            return base
        out = base / self.data_dir_slug
        out.mkdir(parents=True, exist_ok=True)
        return out
