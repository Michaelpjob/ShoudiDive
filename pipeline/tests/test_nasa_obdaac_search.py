"""Regression test for the NASA OB.DAAC file_search API migration (2026).

The pipeline's 3 NASA chl primaries (AQUA/SNPP/S3A) silently went DEAD when
OB.DAAC changed its file_search contract: the old `subType=1` + loose-wildcard
search started returning HTTP 422, so chl_blend got zero NASA frames and chl
fell back to a single NOAA host (verified in prod refresh logs + the
nasa_obdaac_search feed-health probe reporting http=422).

These tests pin the corrected contract so a future edit can't regress to the
422-ing params. Network-free: http_get is monkeypatched.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pytest  # noqa: F401  (monkeypatch fixture)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chl_blend as cb  # noqa: E402


class _Resp:
    status_code = 200
    text = (
        "AQUA_MODIS.20260614.L3m.DAY.CHL.chlor_a.4km.NRT.nc\n"
        "AQUA_MODIS.20260615.L3m.DAY.CHL.chlor_a.4km.NRT.nc\n"
    )


def test_nasa_search_uses_migrated_api_params(monkeypatch):
    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        captured["params"] = kw.get("params")
        return _Resp()

    monkeypatch.setattr(cb, "http_get", fake_get)
    files = cb._nasa_search_files(session=None, sensor="AQUA_MODIS", d=date(2026, 6, 14))

    p = captured["params"]
    # The 2026 OB.DAAC migration: subType is gone (it 422s); dtype=L3m is now
    # required; the search glob must match the dotted filename pattern.
    assert "subType" not in p, "subType=1 now 422s on OB.DAAC — must be removed"
    assert p.get("dtype") == "L3m"
    assert p["search"] == "AQUA_MODIS*L3m.DAY.CHL.chlor_a.4km.NRT*"
    assert p["sdate"] == "2026-06-14" and p["edate"] == "2026-06-14"
    assert "oceandata.sci.gsfc.nasa.gov/api/file_search" in captured["url"]
    # Bare filenames are still returned (no addurl) and parse unchanged.
    assert files == [
        "AQUA_MODIS.20260614.L3m.DAY.CHL.chlor_a.4km.NRT.nc",
        "AQUA_MODIS.20260615.L3m.DAY.CHL.chlor_a.4km.NRT.nc",
    ]


def test_search_glob_is_per_sensor(monkeypatch):
    captured = {}
    monkeypatch.setattr(cb, "http_get",
                        lambda url, **kw: captured.__setitem__("params", kw.get("params")) or _Resp())
    cb._nasa_search_files(session=None, sensor="S3A_OLCI_ERRNT", d=date(2026, 6, 14))
    assert captured["params"]["search"] == "S3A_OLCI_ERRNT*L3m.DAY.CHL.chlor_a.4km.NRT*"


def test_feed_health_probe_not_on_422_params():
    """The nasa_obdaac_search feed-health probe must use the migrated format,
    else it perpetually reports the source DEAD (http=422)."""
    src = (ROOT / "check_feeds.py").read_text(encoding="utf-8")
    m = re.search(r'feed_id="nasa_obdaac_search".*?probe_url="([^"]+)"', src, re.S)
    assert m, "nasa_obdaac_search FeedSpec not found"
    url = m.group(1)
    assert "subType=1" not in url, "feed-health probe still uses the 422-ing subType param"
    assert "dtype=L3m" in url
