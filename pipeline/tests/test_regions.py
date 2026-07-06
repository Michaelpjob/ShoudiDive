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
    assert "baja" in names
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

@pytest.mark.parametrize("name", ["ca", "pnw", "tropical", "baja"])
def test_bbox_is_geographically_sane(name):
    r = get_region(name)
    b = r.bbox
    assert b["lat_min"] < b["lat_max"], f"{name}: lat_min must be < lat_max"
    assert b["lng_min"] < b["lng_max"], f"{name}: lng_min must be < lng_max"
    assert -90 < b["lat_min"] < 90, f"{name}: lat_min out of range"
    assert -90 < b["lat_max"] < 90, f"{name}: lat_max out of range"
    assert -180 < b["lng_min"] < 180, f"{name}: lng_min out of range"
    assert -180 < b["lng_max"] < 180, f"{name}: lng_max out of range"


@pytest.mark.parametrize("name", ["ca", "pnw", "tropical", "baja"])
def test_bbox_array_matches_dict(name):
    r = get_region(name)
    arr = r.bbox_array
    assert arr == [r.bbox["lng_min"], r.bbox["lat_min"],
                   r.bbox["lng_max"], r.bbox["lat_max"]]


@pytest.mark.parametrize("name", ["ca", "pnw", "tropical", "baja"])
def test_lat_zone_bounds_non_empty(name):
    r = get_region(name)
    assert r.lat_zone_bounds, f"{name}: must define at least one lat zone"


@pytest.mark.parametrize("name", ["ca", "pnw", "tropical", "baja"])
def test_dist_labels_non_empty(name):
    r = get_region(name)
    assert r.dist_labels, f"{name}: must define dist_labels"


@pytest.mark.parametrize("name", ["ca", "pnw", "tropical", "baja"])
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
# Wiring gates — every pipeline fetcher must source BBOX from
# regions/, not redefine it. PR-X-2 migrated all 14 fetch_*.py +
# chl_blend.py to `BBOX = active_region().bbox`. These tests catch
# any future fetcher (or accidental regression) that reintroduces a
# hardcoded dict-literal BBOX.
# ---------------------------------------------------------------------

import re

# Files in pipeline/ that legitimately set BBOX. Migrated in PR-X-2.
_FETCH_FILES = [
    "fetch.py",
    "chl_blend.py",
    "fetch_bathy.py",
    "fetch_climatology.py",
    "fetch_coastline.py",
    "fetch_currents.py",
    "fetch_mpa.py",
    "fetch_precip.py",
    "fetch_sst_5day.py",
    "fetch_swell_5day.py",
    "fetch_visibility.py",
    "fetch_waves.py",
    "fetch_wind.py",
    "fetch_wind_5day.py",
]


@pytest.mark.parametrize("fname", _FETCH_FILES)
def test_fetcher_imports_bbox_from_regions(fname):
    """Every fetcher sources BBOX via active_region(). If you add a
    new pipeline/fetch_*.py, append it to ``_FETCH_FILES`` and make
    sure it imports the same way — don't reintroduce a literal
    `BBOX = dict(...)` in any production script.
    """
    text = (ROOT / fname).read_text(encoding="utf-8")
    assert "BBOX = active_region().bbox" in text, (
        f"pipeline/{fname} must read BBOX from regions/ via "
        f"`BBOX = active_region().bbox` (PR-X-2 contract). "
        f"See pipeline/fetch.py for the canonical pattern."
    )
    # The import must also be present (otherwise the BBOX line
    # would NameError at runtime).
    assert "from regions import active_region" in text or \
           "from pipeline.regions import active_region" in text, (
        f"pipeline/{fname} uses `active_region()` but does not import "
        f"it. Add the try/except import block above the BBOX line."
    )


@pytest.mark.parametrize("fname", _FETCH_FILES)
def test_fetcher_no_dict_literal_bbox(fname):
    """No fetcher may carry a `BBOX = dict(lat_min=..., ...)` literal
    anymore — the regions scaffold is the single source of truth.
    """
    text = (ROOT / fname).read_text(encoding="utf-8")
    assert not re.search(
        r"BBOX\s*=\s*dict\(\s*lat_min\s*=",
        text,
    ), (
        f"pipeline/{fname} still has a hardcoded `BBOX = dict(lat_min=...)` "
        f"literal. PR-X-2 migrated this to `BBOX = active_region().bbox`. "
        f"Either re-apply the migration or, if you intentionally need a "
        f"region-local bbox override, rename the constant so it's not "
        f"shadowing the regions/ source-of-truth."
    )


def test_ca_lat_zone_bounds_match_viz_predict_config():
    """LAT_ZONE_BOUNDS still lives in viz_predict/config.py (not yet
    migrated by PR-X-2). Drift gate stays until a future PR moves it
    into regions/ as well.
    """
    ca = get_region("ca")
    assert ca.lat_zone_bounds == LAT_ZONE_BOUNDS, (
        f"DRIFT: regions/ca.py.lat_zone_bounds != "
        f"viz_predict/config.py.LAT_ZONE_BOUNDS. "
        f"regions/ca.py = {ca.lat_zone_bounds}; "
        f"viz_predict/config.py = {LAT_ZONE_BOUNDS}"
    )


# ---------------------------------------------------------------------
# JS <-> Python bbox drift (the follow-up TODO noted in mapData.js)
# ---------------------------------------------------------------------
#
# src/lib/mapData.js hardcodes REGION_BBOX for the frontend projection
# and its comment says "must stay in lockstep with pipeline/regions/*.py".
# Until this gate existed, nothing enforced that: bumping a bbox on one
# side rendered data geographically misaligned with the basemap. The
# test parses the JS literal with a regex (no JS toolchain in the
# pipeline-tests job) — if the literal's shape changes enough to defeat
# the regex, the parse assertion fails loudly rather than passing empty.

_MAPDATA_JS = ROOT.parent / "src" / "lib" / "mapData.js"

_JS_KEY_TO_PY = {
    "latMin": "lat_min",
    "latMax": "lat_max",
    "lngMin": "lng_min",
    "lngMax": "lng_max",
}


def _parse_js_region_bbox() -> dict[str, dict[str, float]]:
    text = _MAPDATA_JS.read_text(encoding="utf-8")
    m = re.search(r"const REGION_BBOX\s*=\s*\{(.*?)\n\};", text, re.DOTALL)
    assert m, (
        "Could not locate the `const REGION_BBOX = {...};` literal in "
        "src/lib/mapData.js — if it moved or was renamed, update "
        "_parse_js_region_bbox() in this test."
    )
    out: dict[str, dict[str, float]] = {}
    for line in m.group(1).splitlines():
        entry = re.match(r"\s*(\w+)\s*:\s*\{(.*)\}", line)
        if not entry:
            continue
        name, body = entry.group(1), entry.group(2)
        vals = {
            _JS_KEY_TO_PY[k]: float(v)
            for k, v in re.findall(r"(latMin|latMax|lngMin|lngMax)\s*:\s*(-?[\d.]+)", body)
        }
        assert len(vals) == 4, (
            f"REGION_BBOX.{name} in mapData.js parsed to {vals} — expected "
            f"all four of latMin/latMax/lngMin/lngMax."
        )
        out[name] = vals
    assert out, "REGION_BBOX literal parsed to zero regions — regex rot?"
    return out


def test_js_region_bbox_covers_same_regions_as_python():
    js = _parse_js_region_bbox()
    assert sorted(js.keys()) == list_regions(), (
        f"DRIFT: region sets differ — src/lib/mapData.js REGION_BBOX has "
        f"{sorted(js.keys())} but pipeline/regions/ registers "
        f"{list_regions()}. Add the missing region to whichever side "
        f"lacks it."
    )


@pytest.mark.parametrize("name", list_regions())
def test_js_region_bbox_matches_python(name):
    js = _parse_js_region_bbox()
    if name not in js:
        pytest.skip("region-set mismatch is reported by the test above")
    py = get_region(name).bbox
    for key, js_val in js[name].items():
        assert js_val == pytest.approx(py[key], abs=1e-9), (
            f"DRIFT: {name}.{key} — src/lib/mapData.js says {js_val}, "
            f"pipeline/regions/{name}.py says {py[key]}. These must be "
            f"bumped together or the frontend projection misaligns with "
            f"the pipeline rasters."
        )
