"""EARTHDATA_TOKEN expiry must be LOUD, not a quietly blank chl map.

Earthdata Login user tokens live 60 days. When one expires, NASA OB.DAAC
answers every ``ob/getfile`` with a 302 to the Earthdata login page (HTML,
HTTP 200): the download "succeeds", ``_nasa_download`` drops it as too small,
all three NASA sources report "no valid frames", and the blend falls back to
gap-fill — which the app's observed-only chl view hides. The 2026-06-23 token
expired 2026-08-23 and the chl map was blank for 69 days with every check
green. These tests pin the three places that now notice:

  * chl_blend._nasa_download counts login-page responses and
    build_blended_chl flags the manifest (``nasa_auth_failed``);
  * check_published.check_chl_provenance turns that flag — and, more
    generally, "no direct retrieval owns a cell" — into high findings that
    open the data-health issue;
  * check_feeds._probe_nasa_getfile does an authenticated download and is
    green only on a netCDF signature.

Network-free: fetchers, sessions and searches are stubbed.

Run:
    python -m pytest pipeline/tests/test_chl_nasa_auth.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest  # noqa: F401  (fixtures: tmp_path, monkeypatch)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import check_feeds as cf  # noqa: E402
import chl_blend as cb  # noqa: E402
from check_published import check_chl_provenance  # noqa: E402


# ---- helpers --------------------------------------------------------------

LOGIN_URL = ("https://urs.earthdata.nasa.gov//oauth/authorize?client_id=x"
             "&redirect_uri=https://oceandata.sci.gsfc.nasa.gov/getfile/urs/")


def _resp(*, status=200, url="https://oceandata.sci.gsfc.nasa.gov/ob/getfile/f.nc",
          ctype="application/x-netcdf", content=b""):
    return SimpleNamespace(status_code=status, url=url,
                           headers={"Content-Type": ctype}, content=content)


def _const_fetcher(value):
    def f(d):
        return np.full((cb.OUT_H, cb.OUT_W), value, dtype=np.float32)
    return f


def _stub_blend_env(monkeypatch, tmp_path, sources, token):
    monkeypatch.setattr(cb, "CHL_SOURCES", sources)
    monkeypatch.setattr(cb, "OUT_DIR", tmp_path)
    monkeypatch.setattr(cb, "_load_land_mask", lambda w, h, *a, **k: None)
    if token:
        monkeypatch.setenv("EARTHDATA_TOKEN", token)
    else:
        monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)


# ---- chl_blend: login-page detection + manifest flag ---------------------

def test_login_page_is_recognised_by_host_or_content_type():
    assert cb._looks_like_login_page(_resp(url=LOGIN_URL, ctype="text/html; charset=utf-8"))
    assert cb._looks_like_login_page(_resp(ctype="text/html"))       # HTML on the data host
    assert not cb._looks_like_login_page(_resp())                     # real netCDF response
    assert not cb._looks_like_login_page(_resp(ctype="application/json"))


def test_nasa_download_counts_login_page_responses(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "CACHE_DIR", tmp_path)
    cb._NASA_AUTH["rejected"] = 0
    # The exact prod signature: HTTP 200, ~31 KB of HTML from urs.
    monkeypatch.setattr(cb, "http_get", lambda *a, **k: _resp(
        url=LOGIN_URL, ctype="text/html", content=b"<html>login</html>" * 1500))
    assert cb._nasa_download(object(), "AQUA_MODIS.20261029.L3m.DAY.CHL.chlor_a.4km.NRT.nc") is None
    assert cb._NASA_AUTH["rejected"] == 1
    # A tiny non-HTML body (JSON error wrapper) is dropped but NOT counted
    # as an auth failure.
    monkeypatch.setattr(cb, "http_get", lambda *a, **k: _resp(
        ctype="application/json", content=b'{"error":"x"}'))
    assert cb._nasa_download(object(), "SNPP_VIIRS.20261029.L3m.DAY.CHL.chlor_a.4km.NRT.nc") is None
    assert cb._NASA_AUTH["rejected"] == 1
    assert not list(tmp_path.iterdir()), "nothing must be cached on failure"


def test_blend_flags_nasa_auth_failed_when_token_rejected(tmp_path, monkeypatch, capsys):
    # NASA source: every download is the login page (stub bumps the tally,
    # like _nasa_download does) and yields nothing; DINEOF carries the blend.
    def rejected(d):
        cb._NASA_AUTH["rejected"] += 1
        return None
    nasa = cb.ChlSource(id="aqua", label="NASA AQUA", priority=1, max_back=3,
                        fetcher=rejected, requires_earthdata=True)
    dineof = cb.ChlSource(id="dineof", label="NOAA DINEOF", priority=4, max_back=3,
                          fetcher=_const_fetcher(0.4))
    _stub_blend_env(monkeypatch, tmp_path, [nasa, dineof], token="expired-token")

    ml = cb.build_blended_chl(date(2026, 10, 30))
    assert ml is not None
    assert ml.get("nasa_auth_failed") is True
    out = capsys.readouterr().out
    assert "::warning title=NASA OB.DAAC rejected EARTHDATA_TOKEN::" in out
    assert "gh secret set EARTHDATA_TOKEN" in out


def test_blend_does_not_flag_when_nasa_contributes(tmp_path, monkeypatch):
    nasa = cb.ChlSource(id="aqua", label="NASA AQUA", priority=1, max_back=3,
                        fetcher=_const_fetcher(0.2), requires_earthdata=True)
    dineof = cb.ChlSource(id="dineof", label="NOAA DINEOF", priority=4, max_back=3,
                          fetcher=_const_fetcher(0.4))
    _stub_blend_env(monkeypatch, tmp_path, [nasa, dineof], token="good-token")
    ml = cb.build_blended_chl(date(2026, 10, 30))
    assert ml is not None
    assert "nasa_auth_failed" not in ml


def test_blend_does_not_flag_a_cloudy_week_or_an_unset_token(tmp_path, monkeypatch):
    # No frames but no login pages either (clouds / outage): not an auth failure.
    nasa = cb.ChlSource(id="aqua", label="NASA AQUA", priority=1, max_back=3,
                        fetcher=_const_fetcher(np.nan), requires_earthdata=True)
    dineof = cb.ChlSource(id="dineof", label="NOAA DINEOF", priority=4, max_back=3,
                          fetcher=_const_fetcher(0.4))
    _stub_blend_env(monkeypatch, tmp_path, [nasa, dineof], token="good-token")
    ml = cb.build_blended_chl(date(2026, 10, 30))
    assert ml is not None and "nasa_auth_failed" not in ml
    # Token unset: NASA is skipped before any download, never "rejected".
    _stub_blend_env(monkeypatch, tmp_path, [nasa, dineof], token=None)
    cb._NASA_AUTH["rejected"] = 5  # stale tally from a previous run must be reset
    ml = cb.build_blended_chl(date(2026, 10, 30))
    assert ml is not None and "nasa_auth_failed" not in ml


# ---- check_published: findings -------------------------------------------

def _manifest(chl: dict | None) -> dict:
    layers = {} if chl is None else {"chl": chl}
    return {"generated_at": "2026-10-30T18:08:50Z", "layers": layers}


def _src(priority, owned, age=1):
    return {"priority": priority, "cells_owned": owned, "age_days": age,
            "freshest_date": "2026-10-29", "frames_used": 1}


def _codes(findings):
    return {f.code for f in findings}


def test_check_chl_provenance_flags_the_2026_10_30_prod_manifest():
    # Live prod shape that day: 60% coverage, 1-day-old, and NOT ONE direct
    # retrieval — the chl map was blank in the app.
    m = _manifest({
        "nasa_auth_failed": True,
        "windows": {"1d": {"coverage_frac": 0.603, "mean_age_days": 1.01, "sources": {
            "dineof_nrt_4km": _src(4, 1100, 2),
            "dineof_sci_9km": _src(5, 16, 11),
            "cmems_globcolour": _src(6, 3692, 1),
            "viirs_upwell_nrt": _src(7, 0, 6),
        }}},
    })
    findings = check_chl_provenance(m)
    assert _codes(findings) == {"chl_nasa_auth_failed", "chl_no_direct_observation"}
    assert all(f.severity == "high" and f.layer == "chl" for f in findings)
    auth = next(f for f in findings if f.code == "chl_nasa_auth_failed")
    assert "60 days" in auth.detail and "gh secret set EARTHDATA_TOKEN" in auth.detail
    blank = next(f for f in findings if f.code == "chl_no_direct_observation")
    assert "cmems_globcolour (p6, 3692 cells, 1d)" in blank.detail


def test_check_chl_provenance_is_clean_with_a_nasa_retrieval():
    m = _manifest({"windows": {"1d": {"sources": {
        "aqua_modis_nrt": _src(1, 420, 2),
        "cmems_globcolour": _src(6, 3300, 1),
    }}}})
    assert check_chl_provenance(m) == []


def test_check_chl_provenance_counts_raw_viirs_as_direct():
    m = _manifest({"windows": {"1d": {"sources": {
        "viirs_upwell_nrt": _src(7, 12, 2),
        "cmems_globcolour": _src(6, 3300, 1),
    }}}})
    assert check_chl_provenance(m) == []


def test_check_chl_provenance_tolerates_missing_layer_or_sources():
    assert check_chl_provenance(_manifest(None)) == []
    assert check_chl_provenance(_manifest({"windows": {"1d": {"url": "/data/chl_1d.png"}}})) == []
    assert check_chl_provenance({"layers": {"chl": "bogus"}}) == []


# ---- check_feeds: authenticated getfile probe -----------------------------

class _Raw:
    def __init__(self, data):
        self._data = data

    def read(self, n, decode_content=True):
        return self._data[:n]


class _StreamResp:
    def __init__(self, *, status, url, ctype, body):
        self.status_code = status
        self.url = url
        self.headers = {"Content-Type": ctype}
        self.raw = _Raw(body)
        self.closed = False

    def close(self):
        self.closed = True


def _spec():
    return next(s for s in cf.FEEDS if s.feed_id == "nasa_obdaac_getfile")


def _wire(monkeypatch, resp, files=("AQUA_MODIS.20261028.L3m.DAY.CHL.chlor_a.4km.NRT.nc",)):
    calls = {}

    class _Session:
        def get(self, url, **kw):
            calls["url"] = url
            calls["stream"] = kw.get("stream")
            return resp

    monkeypatch.setattr(cb, "_earthdata_session", lambda: _Session())
    monkeypatch.setattr(cb, "_nasa_search_files", lambda session, sensor, d: list(files))
    return calls


def test_getfile_probe_is_registered_with_a_custom_prober_and_token_gate():
    spec = _spec()
    assert spec.probe_fn is not None
    assert spec.skip_if_env_unset == "EARTHDATA_TOKEN"
    assert spec.critical is False  # a dead token must not fail the whole health job


def test_getfile_probe_green_on_netcdf_signature(monkeypatch):
    monkeypatch.setenv("EARTHDATA_TOKEN", "t")
    resp = _StreamResp(status=200, url="https://oceandata.sci.gsfc.nasa.gov/ob/getfile/x.nc",
                       ctype="application/x-netcdf", body=b"\x89HDF\r\n\x1a\n" + b"\x00" * 64)
    calls = _wire(monkeypatch, resp)
    res = cf._probe(_spec())
    assert res.status == "green", res
    assert res.bytes_seen == 8
    assert calls["stream"] is True and calls["url"].endswith("/ob/getfile/AQUA_MODIS.20261028.L3m.DAY.CHL.chlor_a.4km.NRT.nc")
    assert resp.closed


def test_getfile_probe_red_when_redirected_to_earthdata_login(monkeypatch):
    monkeypatch.setenv("EARTHDATA_TOKEN", "expired")
    resp = _StreamResp(status=200, url=LOGIN_URL, ctype="text/html; charset=utf-8",
                       body=b"<!DOCTYPE html><html>")
    _wire(monkeypatch, resp)
    res = cf._probe(_spec())
    assert res.status == "red"
    assert "EARTHDATA_TOKEN rejected" in res.error
    assert "gh secret set EARTHDATA_TOKEN" in res.error


def test_getfile_probe_yellow_when_no_file_to_test_and_skipped_without_token(monkeypatch):
    monkeypatch.setenv("EARTHDATA_TOKEN", "t")
    _wire(monkeypatch, None, files=())
    res = cf._probe(_spec())
    assert res.status == "yellow" and "token untested" in res.error
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    assert cf._probe(_spec()).status == "skipped"
