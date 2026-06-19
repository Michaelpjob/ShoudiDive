"""Tests for the Copernicus Marine (CMEMS) chl source in chl_blend.py.

CMEMS is an independent EU chl provider (GlobColour L4 gap-free), added as a
cross-provider backstop to the NOAA/NASA sources. It's gated on the
COPERNICUSMARINE_SERVICE_* secrets and lies dormant until they exist.

Network-free: the copernicusmarine toolbox is stubbed via sys.modules, so the
fetcher's subset -> orient -> regrid path and the credential-skip are tested
without an account or live access.
"""
from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

import numpy as np
import pytest  # noqa: F401  (monkeypatch fixture)
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chl_blend as cb  # noqa: E402


# ---- Roster wiring ------------------------------------------------------

def test_cmems_source_registered_and_gated():
    src = [s for s in cb.CHL_SOURCES if s.id == "cmems_globcolour"]
    assert src, "cmems_globcolour missing from CHL_SOURCES"
    s = src[0]
    assert s.requires_cmems is True, "must be credential-gated (dormant until secrets)"
    assert s.requires_earthdata is False
    assert "Copernicus" in s.label
    # priorities stay unique so the blend tie-break is deterministic
    prios = [x.priority for x in cb.CHL_SOURCES]
    assert len(set(prios)) == len(prios), f"duplicate priorities: {prios}"


# ---- Fetcher: subset -> orient -> regrid (toolbox stubbed) -------------

def _fake_cmems_module(lat_ascending=True):
    lat = np.linspace(32.0, 42.0, 10)
    if not lat_ascending:
        lat = lat[::-1]
    lon = np.linspace(-125.0, -117.0, 12)
    # distinct per-row values so orientation is observable
    data = np.tile(np.arange(10.0).reshape(1, 10, 1), (1, 1, 12))
    ds = xr.Dataset(
        {"CHL": (("time", "latitude", "longitude"), data)},
        coords={"time": [np.datetime64("2026-06-16")], "latitude": lat, "longitude": lon},
    )
    captured = {}

    def open_dataset(**kw):
        captured.update(kw)
        return ds

    mod = types.SimpleNamespace(open_dataset=open_dataset, _captured=captured)
    return mod


def test_cmems_fetcher_subsets_and_regrids(monkeypatch):
    mod = _fake_cmems_module(lat_ascending=True)
    monkeypatch.setitem(sys.modules, "copernicusmarine", mod)
    fetcher = cb._make_cmems_fetcher("cmems_obs-oc_glo_bgc-plankton_nrt_l4-gapfree-multi-4km_P1D")
    arr = fetcher(date(2026, 6, 16))
    assert arr is not None
    assert arr.shape == (cb.OUT_H, cb.OUT_W)
    assert np.isfinite(arr).any()
    # passed the right dataset id + variable + bbox + date to the toolbox
    c = mod._captured
    assert c["dataset_id"].endswith("gapfree-multi-4km_P1D")
    assert c["variables"] == ["CHL"]
    assert c["start_datetime"].startswith("2026-06-16")
    assert c["minimum_latitude"] == cb.BBOX["lat_min"]


def test_cmems_fetcher_orientation_row0_is_lat_max(monkeypatch):
    # Source rows increase with latitude (row i -> value i). After orient +
    # regrid, row 0 (= lat_max) must carry the HIGH-latitude (large) values.
    monkeypatch.setitem(sys.modules, "copernicusmarine", _fake_cmems_module(lat_ascending=True))
    arr = cb._make_cmems_fetcher("ds")(date(2026, 6, 16))
    top = np.nanmean(arr[0, :])
    bottom = np.nanmean(arr[-1, :])
    assert top > bottom, f"row 0 should be lat_max (higher values): top={top} bottom={bottom}"


def test_cmems_fetcher_missing_toolbox_returns_none(monkeypatch):
    # Simulate the dep not being installed.
    monkeypatch.setitem(sys.modules, "copernicusmarine", None)
    assert cb._make_cmems_fetcher("ds")(date(2026, 6, 16)) is None


# ---- Credential gating --------------------------------------------------

def test_build_skips_cmems_without_creds(monkeypatch, tmp_path):
    monkeypatch.delenv("COPERNICUSMARINE_SERVICE_USERNAME", raising=False)
    monkeypatch.delenv("COPERNICUSMARINE_SERVICE_PASSWORD", raising=False)
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    # Roster of ONLY the cmems source — with no creds it must be skipped, so
    # the blend has no contributing source and returns None (never calls out).
    only_cmems = [s for s in cb.CHL_SOURCES if s.id == "cmems_globcolour"]
    monkeypatch.setattr(cb, "CHL_SOURCES", only_cmems)
    monkeypatch.setattr(cb, "OUT_DIR", tmp_path)
    assert cb.build_blended_chl(date(2026, 6, 16)) is None
