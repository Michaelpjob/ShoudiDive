"""PR-X-1 — region scaffold tests.

The ``pipeline/regions/`` package is additive in PR-X-1 — no running
code imports from it yet — but the test suite *does*, both to verify
the scaffold loads cleanly and to gate against silent drift between
the CA region snapshot in ``regions/ca.py`` and the still-hardcoded
values in ``fetch.py`` / ``viz_predict/config.py``.

Drift checks:
  * ``REGION.bbox`` for ``ca`` matches the ``BBOX`` dict in
    ``pipeline/fetch.py``.
  * ``REGION.lat_zone_bounds`` for ``ca`` matches
    ``viz_predict.config.LAT_ZONE_BOUNDS``.

If either drifts, the dev-checks pipeline-tests job goes red and the
contributor is told exactly which file to bump. This is the cheapest
way to keep PR-X-1 from rotting between now and PR-X-2 wiring the
fetcher imports.

Run:
    python -m pytest pipeline/tests/test_regions.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regions import (                       # noqa: E402
    DEFAULT_REGION,
    Region,
    active_region,
    get_region,
    list_regions,
)
from viz_predict.config import LAT_ZONE_BOUNDS  # noqa: E402


# ---------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------

def test_default_region_is_ca():
    assert DEFAULT_REGION == "ca"


def test_all_regions_load():
    names = list_regions()
    assert "ca" in names
    assert "pnw" in names
    assert "tropical" in names
    for n in names:
        r = get_region(n)
        assert isinstance(r, Region)
        assert r.name == n


def test_get_region_unknown_raises():
    with pytest.raises(KeyError):
        get_region("atlantis")


def test_active_region_defaults_to_ca(monkeypatch):
    monkeypatch.delenv("SHOULDIDIVE_REGION", raising=False)
    assert active_region().name == "ca"


def test_active_region_respects_env(monkeypatch):
    monkeypatch.setenv("SHOULDIDIVE_REGION", "pnw")
    assert active_region().name == "pnw"


# ---------------------------------------------------------------------
# Per-region invariants — every region must have a sane bbox
# ---------------------------------------------------------------------

@pytest.mark.parametrize("name", ["ca", "pnw", "tropical"])
def test_bbox_is_geographically_sane(name):
    r = get_region(name)
    b = r.bbox
    assert b["lat_min"] < b["lat_max"], f"{name}: lat_min must be < lat_max"
    assert b["lng_min"] < b["lng_max"], f"{name}: lng_min must be < lng_max"
    assert -90 < b["lat_min"] < 90, f"{name}: lat_min out of range"
    assert -90 < b["lat_max"] < 90, f"{name}: lat_max out of range"
    assert -180 < b["lng_min"] < 180, f"{name}: lng_min out of range"
    assert -180 < b["lng_max"] < 180, f"{name}: lng_max out of range"


@pytest.mark.parametrize("name", ["ca", "pnw", "tropical"])
def test_bbox_array_matches_dict(name):
    r = get_region(name)
    arr = r.bbox_array
    assert arr == [r.bbox["lng_min"], r.bbox["lat_min"],
                   r.bbox["lng_max"], r.bbox["lat_max"]]


@pytest.mark.parametrize("name", ["ca", "pnw", "tropical"])
def test_lat_zone_bounds_non_empty(name):
    r = get_region(name)
    assert r.lat_zone_bounds, f"{name}: must define at least one lat zone"


@pytest.mark.parametrize("name", ["ca", "pnw", "tropical"])
def test_dist_labels_non_empty(name):
    r = get_region(name)
    assert r.dist_labels, f"{name}: must define dist_labels"


@pytest.mark.parametrize("name", ["ca", "pnw", "tropical"])
def test_data_dir_slug_safe(name):
    r = get_region(name)
    assert r.data_dir_slug
    # No path separators or capital letters — used directly in URLs.
    assert "/" not in r.data_dir_slug
    assert "\\" not in r.data_dir_slug
    assert r.data_dir_slug == r.data_dir_slug.lower()


def test_viz_model_variant_is_known():
    # Either of the two declared variants — Literal type check at
    # runtime (Literal is a static-time hint; this guard is the
    # runtime equivalent).
    known = {"chl_based", "subtractive_tropical"}
    for name in list_regions():
        r = get_region(name)
        assert r.viz_model_variant in known, (
            f"{name} declares unknown viz variant "
            f"{r.viz_model_variant!r}"
        )


# ---------------------------------------------------------------------
# CA-specific drift gates — keep regions/ca.py in lockstep with the
# hardcoded values in fetch.py + viz_predict/config.py until PR-X-2
# rewires the fetchers to import from regions/.
# ---------------------------------------------------------------------

def test_ca_bbox_matches_fetch_py():
    """If you bump BBOX in fetch.py, bump regions/ca.py too — or
    the scaffold's snapshot diverges from reality and PR-X-2 will
    quietly behavior-change the production fetcher.
    """
    fetch_py = (ROOT / "fetch.py").read_text(encoding="utf-8")
    # Match the literal BBOX line. Loose regex because numeric
    # formatting in the source could vary (31.8 vs 31.80).
    import re
    m = re.search(
        r"BBOX\s*=\s*dict\(\s*"
        r"lat_min\s*=\s*([\d.\-]+)\s*,\s*"
        r"lat_max\s*=\s*([\d.\-]+)\s*,\s*"
        r"lng_min\s*=\s*([\d.\-]+)\s*,\s*"
        r"lng_max\s*=\s*([\d.\-]+)\s*\)",
        fetch_py,
    )
    assert m, (
        "Couldn't find the BBOX = dict(...) line in pipeline/fetch.py. "
        "If you renamed the constant or restructured the assignment, "
        "update this test."
    )
    fetch_bbox = {
        "lat_min": float(m.group(1)),
        "lat_max": float(m.group(2)),
        "lng_min": float(m.group(3)),
        "lng_max": float(m.group(4)),
    }
    ca = get_region("ca")
    assert ca.bbox == fetch_bbox, (
        f"DRIFT: regions/ca.py bbox {ca.bbox} != fetch.py BBOX {fetch_bbox}. "
        f"PR-X-2 will migrate fetch.py to import from regions/; until "
        f"then keep both in sync."
    )


def test_ca_lat_zone_bounds_match_viz_predict_config():
    """Same drift guard for the lat band definitions."""
    ca = get_region("ca")
    assert ca.lat_zone_bounds == LAT_ZONE_BOUNDS, (
        f"DRIFT: regions/ca.py.lat_zone_bounds != "
        f"viz_predict/config.py.LAT_ZONE_BOUNDS. "
        f"regions/ca.py = {ca.lat_zone_bounds}; "
        f"viz_predict/config.py = {LAT_ZONE_BOUNDS}"
    )
