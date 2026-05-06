"""Smoke-test the SST source registry + config skeleton.

These tests don't run any fetcher — every fetcher in sst_predict is a
NotImplementedError stub today. They verify the *declarative* part of
the framework:

  - the registry imports without errors (catches typos in dataclass usage)
  - every registered source has a valid category
  - every source id is unique across the entire registry
  - by_id() round-trips for every source
  - the four registry lists partition ALL_SOURCES exactly
  - config.ALL_ZONES is consistent with LAT × DIST cross-product
  - per-zone config dicts cover every zone (no silent gaps)
  - SIGMA_SST_BY_LEAD has enough entries for HORIZON_DAYS

This also acts as a drift detector — when phase-2 changes sources.py
(adds a new source, renames one), the assertions about
REQUIRED_*_SOURCES below force the README's source-registry tables to
be updated alongside the code, or the test fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


# Mirror test_freshness.py's sys.path setup so absolute imports work
# whether pytest is invoked from repo root or from pipeline/.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sst_predict import sources as src           # noqa: E402
from sst_predict import config as sst_cfg        # noqa: E402
from sst_predict.forecast import HORIZON_DAYS    # noqa: E402


VALID_CATEGORIES = {
    src.CATEGORY_SAT,
    src.CATEGORY_MODEL,
    src.CATEGORY_FORCING,
    src.CATEGORY_OBS,
}


# ---- Imports + structure ------------------------------------------------

def test_registry_imports():
    assert isinstance(src.ALL_SOURCES, list) and len(src.ALL_SOURCES) > 0
    assert isinstance(src.SAT_SOURCES, list)
    assert isinstance(src.MODEL_SOURCES, list)
    assert isinstance(src.FORCING_SOURCES, list)
    assert isinstance(src.OBS_SOURCES, list)


def test_partition_is_exact():
    """Every source in ALL_SOURCES belongs to exactly one category list."""
    expected = (
        src.SAT_SOURCES + src.MODEL_SOURCES
        + src.FORCING_SOURCES + src.OBS_SOURCES
    )
    assert {s.id for s in src.ALL_SOURCES} == {s.id for s in expected}, (
        "ALL_SOURCES diverges from the union of category registries")


# ---- Per-source validation ---------------------------------------------

@pytest.mark.parametrize("s", src.ALL_SOURCES, ids=lambda s: s.id)
def test_source_has_valid_category(s):
    assert s.category in VALID_CATEGORIES, (
        f"source {s.id!r} has invalid category {s.category!r}; "
        f"must be one of {sorted(VALID_CATEGORIES)}")


@pytest.mark.parametrize("s", src.ALL_SOURCES, ids=lambda s: s.id)
def test_source_priority_is_nonneg_int(s):
    assert isinstance(s.priority, int)
    assert s.priority >= 0


@pytest.mark.parametrize("s", src.ALL_SOURCES, ids=lambda s: s.id)
def test_source_in_correct_category_list(s):
    """A source's category attribute matches the list it actually lives in."""
    matrix = {
        src.CATEGORY_SAT:     src.SAT_SOURCES,
        src.CATEGORY_MODEL:   src.MODEL_SOURCES,
        src.CATEGORY_FORCING: src.FORCING_SOURCES,
        src.CATEGORY_OBS:     src.OBS_SOURCES,
    }
    assert s in matrix[s.category], (
        f"source {s.id!r} carries category {s.category!r} but isn't "
        f"in the matching registry list (drift in sources.py)")


def test_source_ids_unique():
    """No duplicate source ids — a duplicate would silently override
    the earlier entry when blend.py iterates."""
    ids = [s.id for s in src.ALL_SOURCES]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate source ids: {sorted(dupes)}"


@pytest.mark.parametrize("s", src.ALL_SOURCES, ids=lambda s: s.id)
def test_by_id_roundtrip(s):
    assert src.by_id(s.id) is s


def test_by_id_returns_none_for_unknown():
    assert src.by_id("definitely-not-a-real-source") is None


# ---- Coverage of the design's stated source list ------------------------

# These IDs are the ones called out in pipeline/sst_predict/README.md's
# source-registry tables. If anyone removes an entry from sources.py
# without updating the README (or vice-versa), this test catches the
# drift and forces the docs to stay current with the code.

REQUIRED_SAT_SOURCES = {
    "mur_l4", "viirs_snpp_nrt", "viirs_n20_nrt", "goes18_abi",
    "geopolar_blend", "modis_aqua", "modis_terra", "sentinel3_slstr",
    "mur_climo",
}

REQUIRED_MODEL_SOURCES = {
    "wcofs", "rtofs_global", "hycom_global", "cfsv2",
}

REQUIRED_FORCING_SOURCES = {
    "hrrr_forcing", "gfs_forcing", "ceres_insol", "cfs_heat_flux",
}

REQUIRED_OBS_SOURCES = {
    "ndbc_water_temp", "cdip_temp", "coops_water_temp",
    "argo_profiles", "dive_log_sst",
}


def test_satellite_registry_matches_design():
    have = {s.id for s in src.SAT_SOURCES}
    assert have == REQUIRED_SAT_SOURCES, (
        f"SAT_SOURCES drifted from README design.\n"
        f"  added:   {have - REQUIRED_SAT_SOURCES}\n"
        f"  removed: {REQUIRED_SAT_SOURCES - have}\n"
        "Update either the README or this test to match.")


def test_model_registry_matches_design():
    have = {s.id for s in src.MODEL_SOURCES}
    assert have == REQUIRED_MODEL_SOURCES


def test_forcing_registry_matches_design():
    have = {s.id for s in src.FORCING_SOURCES}
    assert have == REQUIRED_FORCING_SOURCES


def test_obs_registry_matches_design():
    have = {s.id for s in src.OBS_SOURCES}
    assert have == REQUIRED_OBS_SOURCES


# ---- Config skeleton sanity ---------------------------------------------

def test_config_zone_consistency():
    """config.ALL_ZONES uses the cross-product of LAT × DIST labels —
    if either list mutates, ALL_ZONES needs to mutate with it. The
    per-zone dicts (BIAS_CORRECTION_F, etc) all key off ALL_ZONES so
    a drift here would silently miss zones at runtime."""
    expected = [
        sst_cfg.zone_key(la, di)
        for la in sst_cfg.LAT_LABELS
        for di in sst_cfg.DIST_LABELS
    ]
    assert sst_cfg.ALL_ZONES == expected
    assert set(sst_cfg.BIAS_CORRECTION_F)    == set(sst_cfg.ALL_ZONES)
    assert set(sst_cfg.PERSISTENCE_TAU_DAYS) == set(sst_cfg.ALL_ZONES)
    assert set(sst_cfg.SIGMA_SST_BY_LEAD)    == set(sst_cfg.ALL_ZONES)
    assert set(sst_cfg.HEAT_FLUX_GAIN)       == set(sst_cfg.ALL_ZONES)
    assert set(sst_cfg.MLD_M)                == set(sst_cfg.ALL_ZONES)


def test_config_sigma_is_horizon_sized():
    """Phase-4 ensemble.py expects SIGMA_SST_BY_LEAD[zone] to have one
    entry per lead day in [0..HORIZON_DAYS]. Catch off-by-one drift."""
    for zone, sigmas in sst_cfg.SIGMA_SST_BY_LEAD.items():
        assert len(sigmas) >= HORIZON_DAYS + 1, (
            f"zone {zone!r} has {len(sigmas)} σ values but forecast "
            f"horizon is {HORIZON_DAYS} days (need {HORIZON_DAYS + 1} "
            f"including lead 0)")


def test_predict_module_imports():
    """Smoke test the top-level public-API import surface — catches
    typos that would only show up at first phase-2 wiring attempt."""
    from sst_predict import predict, blend, forecast, ensemble, encode  # noqa: F401


def test_validation_modules_import():
    """Smoke test the validation hooks under pipeline/validation/."""
    from validation import sst_score, sst_watchdog  # noqa: F401
