"""Unit tests for pipeline/lib/erddap.py.

Covers:
  * URL builder shape (matches the legacy fetch.py / fetch_climatology
    URL strings byte-for-byte for representative configs).
  * lng_offset_360 flag rewrites longitude bounds correctly.
  * Cache-hit short-circuits the HTTP call.
  * Successful primary fetch writes the cache file.
  * Primary failure (transport / non-200) falls back to secondary.
  * All sources failing returns None.

Run:
    python -m pytest pipeline/tests/test_lib_erddap.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import erddap as erddap_mod  # noqa: E402
from lib.erddap import (  # noqa: E402
    BBox,
    GriddapSource,
    cache_path_for,
    griddap_fetch,
    griddap_url,
)


CA_BBOX = BBox(lat_min=32.5, lat_max=42.0, lng_min=-125.0, lng_max=-117.0)


# ---------------------------------------------------------------------------
# griddap_url
# ---------------------------------------------------------------------------


def test_griddap_url_matches_fetch_py_shape():
    """The URL string must match the legacy fetch.py shape exactly so
    a migration doesn't perturb cache keys / ERDDAP-side parsing."""
    src = GriddapSource(
        host="https://coastwatch.pfeg.noaa.gov/erddap/griddap",
        dataset="jplMURSST41",
        variable="analysed_sst",
        stride=2,
        pre_xy_dims="",
    )
    d = date(2026, 5, 22)
    got = griddap_url(src, CA_BBOX, d)
    expected = (
        "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.nc"
        "?analysed_sst"
        "[(2026-05-22T00:00:00Z):1:(2026-05-22T23:59:59Z)]"
        "[(32.5):2:(42.0)]"
        "[(-125.0):2:(-117.0)]"
    )
    assert got == expected


def test_griddap_url_includes_pre_xy_dims_for_viirs():
    """VIIRS gap-filled has a length-1 altitude axis — pre_xy_dims="[0]"."""
    src = GriddapSource(
        host="https://coastwatch.noaa.gov/erddap/griddap",
        dataset="noaacwNPPN20VIIRSDINEOFDaily",
        variable="chlor_a",
        stride=1,
        pre_xy_dims="[0]",
    )
    d = date(2026, 5, 22)
    got = griddap_url(src, CA_BBOX, d)
    assert "[(2026-05-22T00:00:00Z):1:(2026-05-22T23:59:59Z)][0]" in got
    assert "[(-125.0):1:(-117.0)]" in got


def test_griddap_url_lng_offset_360_shifts_west_to_positive():
    """fetch_climatology.py uses the MODIS Aqua W-US archive whose
    longitude is stored in 0..360°. The helper must rewrite the bbox
    bounds before serialising the URL."""
    src = GriddapSource(
        host="https://coastwatch.noaa.gov/erddap/griddap",
        dataset="erdMWchla1day",
        variable="chlorophyll",
        stride=1,
        pre_xy_dims="[0]",
    )
    d = date(2025, 5, 15)
    got = griddap_url(src, CA_BBOX, d, lng_offset_360=True)
    # CA bbox lng_min=-125, lng_max=-117  →  235.0, 243.0 under 0..360.
    assert "[(235.0):1:(243.0)]" in got
    # Lat is untouched.
    assert "[(32.5):1:(42.0)]" in got


def test_griddap_url_string_time_lo_requires_time_hi():
    src = GriddapSource(host="h", dataset="d", variable="v")
    with pytest.raises(ValueError):
        griddap_url(src, CA_BBOX, "2026-05-22T12:00:00Z")


def test_griddap_url_string_time_window():
    src = GriddapSource(host="h", dataset="d", variable="v", stride=1)
    got = griddap_url(
        src, CA_BBOX,
        "2026-05-22T00:00:00Z",
        "2026-05-22T23:59:59Z",
    )
    assert "[(2026-05-22T00:00:00Z):1:(2026-05-22T23:59:59Z)]" in got


def test_griddap_url_per_call_stride_override():
    src = GriddapSource(host="h", dataset="d", variable="v", stride=2)
    got = griddap_url(src, CA_BBOX, date(2026, 5, 22), stride=4)
    assert "[(32.5):4:(42.0)]" in got
    assert "[(-125.0):4:(-117.0)]" in got


# ---------------------------------------------------------------------------
# BBox
# ---------------------------------------------------------------------------


def test_bbox_from_dict_accepts_active_region_shape():
    bbox_dict = {
        "lat_min": 32.5, "lat_max": 42.0,
        "lng_min": -125.0, "lng_max": -117.0,
    }
    b = BBox.from_dict(bbox_dict)
    assert b.lat_min == 32.5 and b.lat_max == 42.0
    assert b.lng_min == -125.0 and b.lng_max == -117.0


# ---------------------------------------------------------------------------
# cache_path_for
# ---------------------------------------------------------------------------


def test_cache_path_for_matches_legacy_pattern(tmp_path):
    """Filename layout must match fetch.py's
    `f"{layer}_{key}_{d.isoformat()}_s{source_stride}.nc"`.
    Otherwise existing cache files become orphans on migration."""
    src = GriddapSource(host="h", dataset="jplMURSST41", variable="v", stride=2)
    path = cache_path_for(tmp_path, prefix="sst", source=src,
                          when=date(2026, 5, 22))
    assert path.name == "sst_jplMURSST41_2026-05-22_s2.nc"


def test_cache_path_for_stride_override(tmp_path):
    src = GriddapSource(host="h", dataset="d", variable="v", stride=2)
    path = cache_path_for(tmp_path, prefix="x", source=src,
                          when=date(2026, 5, 22), stride=1)
    assert path.name.endswith("_s1.nc")


# ---------------------------------------------------------------------------
# griddap_fetch
# ---------------------------------------------------------------------------


def _ok_resp(body: bytes = b"netcdf") -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = 200
    r.content = body
    return r


def _err_resp(code: int) -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = code
    r.content = b""
    return r


def test_griddap_fetch_cache_hit_short_circuits(tmp_path, monkeypatch):
    """If the cache file exists, the helper returns it without
    calling http_get at all — the legacy fetchers also short-circuit."""
    src = GriddapSource(host="h", dataset="d", variable="v", stride=1)
    cached = tmp_path / "x_d_2026-05-22_s1.nc"
    cached.write_bytes(b"old")

    monkeypatch.setattr(
        erddap_mod, "http_get",
        lambda *a, **kw: pytest.fail("http_get must not be called on cache hit"),
    )
    result = griddap_fetch(
        src, CA_BBOX, date(2026, 5, 22),
        cache_dir=tmp_path, cache_prefix="x",
    )
    assert result is not None
    path, winner = result
    assert path == cached
    assert path.read_bytes() == b"old"
    assert winner.dataset == "d"


def test_griddap_fetch_primary_succeeds(tmp_path, monkeypatch):
    src = GriddapSource(host="h", dataset="d", variable="v", stride=1)
    calls = []

    def fake_http_get(url, **kwargs):
        calls.append(url)
        return _ok_resp(b"hello")

    monkeypatch.setattr(erddap_mod, "http_get", fake_http_get)

    result = griddap_fetch(
        src, CA_BBOX, date(2026, 5, 22),
        cache_dir=tmp_path, cache_prefix="x",
    )
    assert result is not None
    path, winner = result
    assert path.read_bytes() == b"hello"
    assert winner.dataset == "d"
    assert len(calls) == 1


def test_griddap_fetch_falls_back_on_non_200(tmp_path, monkeypatch):
    """Primary 503 → fall back to secondary; secondary 200 → win."""
    primary = GriddapSource(
        host="h1", dataset="d_primary", variable="v", stride=1,
        label="primary",
    )
    fallback = GriddapSource(
        host="h2", dataset="d_fallback", variable="v", stride=1,
        label="fallback",
    )

    responses = [_err_resp(503), _ok_resp(b"fallback-bytes")]

    def fake_http_get(url, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(erddap_mod, "http_get", fake_http_get)

    logs = []
    result = griddap_fetch(
        [primary, fallback],
        CA_BBOX,
        date(2026, 5, 22),
        cache_dir=tmp_path,
        cache_prefix="x",
        log=logs.append,
    )
    assert result is not None
    path, winner = result
    assert winner.dataset == "d_fallback"
    assert path.read_bytes() == b"fallback-bytes"
    # Should have logged the failure + the fallback attempt.
    assert any("HTTP 503" in line for line in logs)
    assert any("via fallback" in line for line in logs)


def test_griddap_fetch_falls_back_on_transport_failure(tmp_path, monkeypatch):
    primary = GriddapSource(host="h1", dataset="d_primary", variable="v", stride=1)
    fallback = GriddapSource(host="h2", dataset="d_fallback", variable="v", stride=1)

    # http_get returns None on exhausted transport retries.
    responses = [None, _ok_resp(b"ok")]

    def fake_http_get(url, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(erddap_mod, "http_get", fake_http_get)

    result = griddap_fetch(
        [primary, fallback],
        CA_BBOX,
        date(2026, 5, 22),
        cache_dir=tmp_path,
        cache_prefix="x",
        log=lambda _: None,
    )
    assert result is not None
    path, winner = result
    assert winner.dataset == "d_fallback"


def test_griddap_fetch_all_sources_fail_returns_none(tmp_path, monkeypatch):
    primary = GriddapSource(host="h1", dataset="d1", variable="v", stride=1)
    fallback = GriddapSource(host="h2", dataset="d2", variable="v", stride=1)
    monkeypatch.setattr(erddap_mod, "http_get", lambda *a, **kw: _err_resp(503))
    result = griddap_fetch(
        [primary, fallback],
        CA_BBOX,
        date(2026, 5, 22),
        cache_dir=tmp_path,
        cache_prefix="x",
        log=lambda _: None,
    )
    assert result is None


def test_griddap_fetch_empty_sources_raises(tmp_path):
    with pytest.raises(ValueError):
        griddap_fetch(
            [], CA_BBOX, date(2026, 5, 22),
            cache_dir=tmp_path, cache_prefix="x",
        )
