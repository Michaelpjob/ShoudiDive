"""Source-provenance + fallback wiring tests for pipeline/fetch.py.

build_layer (SST / kd490) records which source actually served the freshest
day into manifest .source / .source_fallback, so src/lib/confidence.js can
name the source and drop the score when a coarse backup stood in. These tests
pin that wiring and guard the specific regressions found on 2026-06-18:

  * lng_offset_360 rewrites the (negative) bbox into a 0..360 dataset's frame
    (the base OISST aggregation), so the query lands on real cells;
  * the SST primary + every fallback carry a human-readable source_label;
  * the OISST last-resort points at a dataset id that actually EXISTS
    (`ncdcOisst21NrtAgg`, not the retired `_LonPM180`, which silently no-op'd
    #205's fallback) and requests it with the longitude offset;
  * fetch_day records (label, fallback flag) per served day — primary => not a
    fallback, a backup => fallback=True.

Network-free: the one fetch_day test stubs the dataset open + cache.

Run:
    python -m pytest pipeline/tests/test_fetch_sources.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest  # noqa: F401  (fixtures: tmp_path, monkeypatch)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fetch  # noqa: E402


# ---- Longitude-offset URL builder --------------------------------------

def test_lng_offset_360_rewrites_longitude():
    cfg = dict(fetch.LAYERS["sst"])
    cfg["dataset"], cfg["variable"] = "ds", "sst"
    plain = fetch.erddap_url(cfg, date(2026, 6, 16), 1)
    cfg360 = dict(cfg, lng_offset_360=True)
    shifted = fetch.erddap_url(cfg360, date(2026, 6, 16), 1)
    lo, hi = fetch.BBOX["lng_min"], fetch.BBOX["lng_max"]
    # Default frame keeps the negative bounds; offset frame adds 360.
    assert f"({lo}):" in plain and f"({lo + 360.0}):" in shifted
    assert f":({hi})]" in plain and f":({hi + 360.0})]" in shifted
    # latitude is untouched by the longitude offset
    assert f"({fetch.BBOX['lat_min']}):" in shifted


# ---- SST source roster (guards the #205 no-op regression) --------------

def test_sst_primary_and_fallbacks_have_labels():
    for c in fetch.candidate_configs(fetch.LAYERS["sst"]):
        assert c.get("source_label"), (
            f"SST source {c.get('dataset')!r} lacks a readable source_label; "
            f"manifest .source would fall back to the raw dataset id")


def test_sst_oisst_fallback_uses_a_live_dataset_with_offset():
    cands = fetch.candidate_configs(fetch.LAYERS["sst"])
    ids = [c["dataset"] for c in cands]
    # The retired -180..180 variant must not reappear (it 404s -> silent no-op).
    assert "ncdcOisst21NrtAgg_LonPM180" not in ids
    oisst = [c for c in cands if c["dataset"] == "ncdcOisst21NrtAgg"]
    assert oisst, "OISST last-resort SST fallback missing"
    assert oisst[0].get("lng_offset_360") is True, (
        "base OISST indexes longitude 0..360 — needs lng_offset_360")


# ---- fetch_day source recording ----------------------------------------

class _FakeVar:
    def __init__(self, arr):
        self._a = arr
        self.dims = ("latitude", "longitude")
        self.attrs = {"units": "degree_C"}

    @property
    def sizes(self):
        return {}

    @property
    def values(self):
        return self._a


class _FakeDS:
    # Key-agnostic: primary requests "analysed_sst", the OISST fallback "sst".
    def __init__(self, arr):
        self._v = _FakeVar(arr)

    def __getitem__(self, k):
        return self._v

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_open(monkeypatch, tmp_path, arr):
    """Make xr.open_dataset return a 2D `arr` and route the cache to tmp."""
    monkeypatch.setattr(fetch, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(fetch.xr, "open_dataset",
                        lambda p, *a, **k: _FakeDS(arr))


def _touch_cache(tmp_path, layer, dataset, d, stride):
    # fetch_day skips http_get when this cache file already exists.
    p = tmp_path / f"{layer}_{dataset}_{d.isoformat()}_s{stride}.nc"
    p.write_bytes(b"stub")
    return p


def test_fetch_day_records_primary_as_non_fallback(tmp_path, monkeypatch):
    d = date(2026, 6, 16)
    arr = np.ones((4, 5), dtype="float32")
    _stub_open(monkeypatch, tmp_path, arr)
    _touch_cache(tmp_path, "sst", "jplMURSST41", d, 2)
    fetch._LAYER_SOURCE.clear()

    out = fetch.fetch_day("sst", fetch.LAYERS["sst"], d, 2)
    assert out is not None and out.shape == (4, 5)
    rec = fetch._LAYER_SOURCE[("sst", d)]
    assert rec["fallback"] is False
    assert rec["label"] == "MUR L4 SST (1 km)"


def test_fetch_day_records_backup_as_fallback(tmp_path, monkeypatch):
    d = date(2026, 6, 16)
    arr = np.ones((4, 5), dtype="float32")
    _stub_open(monkeypatch, tmp_path, arr)
    # Only the OISST fallback has a cache; force the primary + blended to fail
    # fast so the walk falls through to the last-resort source.
    monkeypatch.setattr(fetch, "http_get", lambda *a, **k: None)
    _touch_cache(tmp_path, "sst", "ncdcOisst21NrtAgg", d, 1)
    fetch._LAYER_SOURCE.clear()

    out = fetch.fetch_day("sst", fetch.LAYERS["sst"], d, 2)
    assert out is not None
    rec = fetch._LAYER_SOURCE[("sst", d)]
    assert rec["fallback"] is True
    assert "OISST" in rec["label"]
