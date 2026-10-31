"""Source-resolution + error-path tests for pipeline/fetch_kelp_landsat.py.

Since 2026-07-30 EDI's PASTA API answers anonymous calls with HTTP 403 and
a sentence ("User 'EDI-... (Public Access)' is not authorized ..."). The old
resolver split that sentence and crashed on int('User'), which hid the real
cause behind a ValueError for two quarterly runs (07-05, 10-05). These tests
pin the replacement:

  * an auth refusal or non-integer revision list raises EdiAccessError that
    names EDI_API_KEY and the key page, never a ValueError;
  * the EDI key is sent as a query param but never appears in an error;
  * DataONE (the anonymous default) picks the newest revision's .nc and
    reads the member-node URL from the 303 without following it;
  * resolve_netcdf falls back DataONE -> PASTA only when a key is set;
  * download() rejects a short file or a checksum mismatch.

Network-free: requests.get is replaced with a canned router.

Run:
    python -m pytest pipeline/tests/test_fetch_kelp_landsat.py -v
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fetch_kelp_landsat as k  # noqa: E402

PUBLIC_403 = ("User 'EDI-078e6e3cee4f7f2812f150701da9351acb51e089 (Public "
              "Access)' is not authorized to execute service method "
              "'listDataPackageRevisions'. (2026-10-20 14:35:31.987)")
DATA_PREFIX = "https://pasta.lternet.edu/package/data/eml/knb-lter-sbc/74/"
GMN_URL = "https://gmn.lternet.edu/mn/v2/object/r34"


class FakeResp:
    def __init__(self, status=200, text="", json_data=None, headers=None,
                 chunks=()):
        self.status_code = status
        self.text = text
        self._json = json_data
        self.headers = headers or {}
        self._chunks = chunks

    def json(self):
        if self._json is None:
            raise ValueError("no JSON")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def iter_content(self, chunk_size=None):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def route(monkeypatch, table):
    """Patch requests.get with `table`: [(url_substring, FakeResp)], first
    match wins. Records (url, params) per call on the returned list."""
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append((url, params))
        for needle, resp in table:
            if needle in url:
                return resp
        raise AssertionError(f"unrouted URL {url}")

    monkeypatch.setattr(k.requests, "get", fake_get)
    return calls


def solr_docs(*revs):
    return {"response": {"docs": [
        {"id": f"{DATA_PREFIX}{r}/c2bea785267fa434c40a22e2239bb337",
         "fileName": f"LandsatKelpBiomass_r{r}.nc", "size": 11,
         "checksum": "ABC123", "checksumAlgorithm": "SHA-1"} for r in revs]}}


# ---- PASTA revision parsing (the 07-05 / 10-05 crash) -------------------

def test_parse_revisions_accepts_integer_list():
    assert k.parse_revisions("31\n32\n34\n33\n") == [31, 32, 34, 33]


@pytest.mark.parametrize("body", [PUBLIC_403, "", "<html>502</html>"])
def test_parse_revisions_rejects_non_integers_without_valueerror(body):
    with pytest.raises(k.EdiAccessError) as ei:
        k.parse_revisions(body)
    assert "EDI_API_KEY" in str(ei.value)


def test_pasta_403_names_the_key_instead_of_crashing(monkeypatch):
    route(monkeypatch, [("pasta.lternet.edu", FakeResp(403, PUBLIC_403))])
    with pytest.raises(k.EdiAccessError) as ei:
        k.latest_netcdf_url(key=None)
    msg = str(ei.value)
    assert "HTTP 403" in msg and "Public Access" in msg
    assert "EDI_API_KEY" in msg and k.EDI_KEY_PAGE in msg


def test_pasta_bad_key_error_does_not_leak_the_key(monkeypatch):
    calls = route(monkeypatch, [
        ("pasta.lternet.edu", FakeResp(401, "401: API key resolution failed"))])
    with pytest.raises(k.EdiAccessError) as ei:
        k.latest_netcdf_url(key="s3cret-key")
    assert "s3cret-key" not in str(ei.value)
    assert calls[0][1] == {"key": "s3cret-key"}


def test_pasta_network_error_does_not_leak_the_key(monkeypatch):
    def boom(url, params=None, **kw):
        raise requests.ConnectionError(f"Max retries exceeded with url: "
                                       f"{url}?key={params['key']}")
    monkeypatch.setattr(k.requests, "get", boom)
    with pytest.raises(k.EdiAccessError) as ei:
        k.latest_netcdf_url(key="s3cret-key")
    assert "s3cret-key" not in str(ei.value)


def test_pasta_happy_path_picks_newest_revision(monkeypatch):
    base = "pasta.lternet.edu/package"
    calls = route(monkeypatch, [
        (f"{base}/name/eml/knb-lter-sbc/74/34/ent1",
         FakeResp(200, "Satellite kelp biomass since 1984\n")),
        (f"{base}/data/eml/knb-lter-sbc/74/34", FakeResp(200, "ent1\n")),
        (f"{base}/eml/knb-lter-sbc/74", FakeResp(200, "32\n34\n33\n")),
    ])
    src = k.latest_netcdf_url(key="kk")
    assert src.rev == 34 and src.via == "PASTA"
    assert src.url.endswith("/data/eml/knb-lter-sbc/74/34/ent1")
    assert src.params == {"key": "kk"}
    assert all(p == {"key": "kk"} for _, p in calls)


# ---- DataONE (anonymous default) ----------------------------------------

def test_dataone_picks_newest_nc_and_reads_redirect(monkeypatch):
    calls = route(monkeypatch, [
        ("/query/solr/", FakeResp(200, json_data=solr_docs(32, 34, 33))),
        ("/resolve/", FakeResp(303, "<url>ignored</url>",
                               headers={"Location": GMN_URL})),
    ])
    src = k.dataone_netcdf()
    assert (src.rev, src.via, src.url) == (34, "DataONE", GMN_URL)
    assert src.checksum == ("SHA-1", "abc123") and src.size == 11
    assert src.params is None
    # the resolved PID is the r34 entity, percent-encoded as one path segment
    assert calls[1][0].endswith(
        "https%3A%2F%2Fpasta.lternet.edu%2Fpackage%2Fdata%2Feml%2F"
        "knb-lter-sbc%2F74%2F34%2Fc2bea785267fa434c40a22e2239bb337")


def test_dataone_falls_back_to_url_in_resolve_body(monkeypatch):
    route(monkeypatch, [
        ("/query/solr/", FakeResp(200, json_data=solr_docs(34))),
        ("/resolve/", FakeResp(303, f"<x><url>{GMN_URL}</url></x>")),
    ])
    assert k.dataone_netcdf().url == GMN_URL


def test_dataone_no_netcdf_is_an_access_error(monkeypatch):
    route(monkeypatch, [("/query/solr/", FakeResp(200, json_data={
        "response": {"docs": [{"id": DATA_PREFIX.replace("data", "report")
                                      + "34", "fileName": "r.xml"}]}}))])
    with pytest.raises(k.EdiAccessError, match="no NetCDF"):
        k.dataone_netcdf()


# ---- resolve_netcdf ordering ---------------------------------------------

def _dataone_down(monkeypatch):
    def down():
        raise k.EdiAccessError("DataONE Solr query failed (503)")
    monkeypatch.setattr(k, "dataone_netcdf", down)


def test_resolve_without_key_says_exactly_what_to_set(monkeypatch):
    _dataone_down(monkeypatch)
    monkeypatch.delenv("EDI_API_KEY", raising=False)
    with pytest.raises(k.EdiAccessError) as ei:
        k.resolve_netcdf()
    msg = str(ei.value)
    assert "DataONE" in msg and "EDI_API_KEY is not set" in msg
    assert k.EDI_KEY_PAGE in msg


def test_resolve_uses_pasta_only_when_dataone_fails_and_key_set(monkeypatch):
    _dataone_down(monkeypatch)
    monkeypatch.setenv("EDI_API_KEY", " kk \n")
    seen = {}

    def pasta(key):
        seen["key"] = key
        return k.KelpSource("u", 34, "PASTA", params={"key": key})
    monkeypatch.setattr(k, "latest_netcdf_url", pasta)
    assert k.resolve_netcdf().via == "PASTA" and seen["key"] == "kk"


def test_resolve_prefers_dataone_even_with_key(monkeypatch):
    monkeypatch.setenv("EDI_API_KEY", "kk")
    monkeypatch.setattr(k, "dataone_netcdf",
                        lambda: k.KelpSource(GMN_URL, 34, "DataONE"))
    monkeypatch.setattr(k, "latest_netcdf_url",
                        lambda key: pytest.fail("PASTA should not be called"))
    assert k.resolve_netcdf().via == "DataONE"


def test_main_exits_1_with_message_on_access_error(monkeypatch, capsys):
    _dataone_down(monkeypatch)
    monkeypatch.delenv("EDI_API_KEY", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(sys, "argv", ["fetch_kelp_landsat.py"])
    assert k.main() == 1
    err = capsys.readouterr().err
    assert err.startswith("::error::") and "%0A" in err and "\n" == err[-1]


# ---- download integrity --------------------------------------------------

def _src(body, **kw):
    sha = hashlib.sha1(body).hexdigest()
    return k.KelpSource(GMN_URL, 34, "DataONE",
                        checksum=kw.get("checksum", ("SHA-1", sha)),
                        size=kw.get("size", len(body)))


def test_download_verifies_checksum(monkeypatch, tmp_path):
    body = b"\x89HDF\r\n\x1a\nkelp"
    route(monkeypatch, [(GMN_URL, FakeResp(200, chunks=[body[:5], body[5:]]))])
    dest = tmp_path / "k.nc"
    k.download(_src(body), dest)
    assert dest.read_bytes() == body


def test_download_rejects_checksum_mismatch(monkeypatch, tmp_path):
    body = b"kelp-bytes"
    route(monkeypatch, [(GMN_URL, FakeResp(200, chunks=[body]))])
    with pytest.raises(k.EdiAccessError, match="SHA-1 mismatch"):
        k.download(_src(body, checksum=("SHA-1", "0" * 40)), tmp_path / "k.nc")


def test_download_rejects_short_file(monkeypatch, tmp_path):
    body = b"kelp-bytes"
    route(monkeypatch, [(GMN_URL, FakeResp(200, chunks=[body[:4]]))])
    with pytest.raises(k.EdiAccessError, match="short download"):
        k.download(_src(body), tmp_path / "k.nc")


def test_download_auth_refusal_names_the_key(monkeypatch, tmp_path):
    route(monkeypatch, [(GMN_URL, FakeResp(403, PUBLIC_403))])
    with pytest.raises(k.EdiAccessError, match="EDI_API_KEY"):
        k.download(_src(b"x"), tmp_path / "k.nc")
