"""Unit tests for pipeline/lib/http.py.

Covers:
  * SESSION carries the canonical User-Agent.
  * http_get returns the response on a 200.
  * http_get retries on 5xx / 429 and returns the final response.
  * http_get retries on RequestException and gives up after `retries`.
  * http_get does NOT retry on 4xx (other than 429).
  * Backoff schedule is consulted before each retry attempt.
  * raise_on_failure surfaces the underlying exception / HTTPError.

These are unit tests; no network is touched. We monkeypatch the
module-level session's `get` method (and the helper's _sleep
indirection) so the suite runs in milliseconds offline.

Run:
    python -m pytest pipeline/tests/test_lib_http.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

# Match the sys.path pattern used by the other tests in this directory:
# they treat pipeline/ as the source root rather than a subpackage.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import http as http_mod  # noqa: E402
from lib.http import (  # noqa: E402
    DEFAULT_USER_AGENT,
    SESSION,
    http_get,
)


def _make_response(status_code: int = 200, content: bytes = b"hi") -> MagicMock:
    """Build a duck-typed Response replacement with the attrs http_get reads."""
    r = MagicMock(spec=requests.Response)
    r.status_code = status_code
    r.content = content
    r.raise_for_status = MagicMock(
        side_effect=requests.HTTPError(f"http {status_code}")
        if status_code >= 400
        else None
    )
    return r


# ---------------------------------------------------------------------------
# Session shape
# ---------------------------------------------------------------------------


def test_session_user_agent_is_canonical():
    """The module-level session must carry the canonical User-Agent."""
    assert SESSION.headers.get("User-Agent") == DEFAULT_USER_AGENT


def test_session_accepts_any_content_type():
    """ERDDAP returns NetCDF; NOMADS returns GRIB2. Accept must be wide."""
    assert SESSION.headers.get("Accept") == "*/*"


# ---------------------------------------------------------------------------
# Single-call behavior
# ---------------------------------------------------------------------------


def test_http_get_200_returns_response_no_retry(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _make_response(200, b"ok")

    sleeps = []
    monkeypatch.setattr(SESSION, "get", fake_get)
    monkeypatch.setattr(http_mod, "_sleep", lambda s: sleeps.append(s))

    r = http_get("https://example.com/x")
    assert r is not None
    assert r.status_code == 200
    assert r.content == b"ok"
    assert len(calls) == 1
    assert sleeps == []


def test_http_get_404_returns_response_no_retry(monkeypatch):
    """4xx (non-429) is a permanent answer; we do NOT retry."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _make_response(404)

    sleeps = []
    monkeypatch.setattr(SESSION, "get", fake_get)
    monkeypatch.setattr(http_mod, "_sleep", lambda s: sleeps.append(s))

    r = http_get("https://example.com/missing", retries=3)
    assert r is not None
    assert r.status_code == 404
    assert len(calls) == 1
    assert sleeps == [], "404 must not trigger retry / sleep"


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


def test_http_get_retries_on_503_then_succeeds(monkeypatch):
    responses = [
        _make_response(503),
        _make_response(503),
        _make_response(200, b"ok"),
    ]
    iter_responses = iter(responses)

    sleeps = []
    monkeypatch.setattr(SESSION, "get", lambda url, **kw: next(iter_responses))
    monkeypatch.setattr(http_mod, "_sleep", lambda s: sleeps.append(s))

    r = http_get("https://example.com/x", retries=3)
    assert r is not None
    assert r.status_code == 200
    # Two sleeps consumed (between attempts 1->2 and 2->3); no sleep before
    # the first attempt and no sleep after the final one.
    assert sleeps == [2.0, 4.0]


def test_http_get_retries_on_429(monkeypatch):
    responses = [_make_response(429), _make_response(200, b"ok")]
    iter_responses = iter(responses)
    sleeps = []
    monkeypatch.setattr(SESSION, "get", lambda url, **kw: next(iter_responses))
    monkeypatch.setattr(http_mod, "_sleep", lambda s: sleeps.append(s))

    r = http_get("https://example.com/x", retries=2)
    assert r is not None
    assert r.status_code == 200
    assert sleeps == [2.0]


def test_http_get_exhausts_retries_on_5xx(monkeypatch):
    """If every attempt is 503, we return the last response (not None)."""
    sleeps = []
    monkeypatch.setattr(SESSION, "get", lambda url, **kw: _make_response(503))
    monkeypatch.setattr(http_mod, "_sleep", lambda s: sleeps.append(s))

    r = http_get("https://example.com/x", retries=3)
    assert r is not None
    assert r.status_code == 503
    # 3 attempts → 2 sleeps between them.
    assert sleeps == [2.0, 4.0]


def test_http_get_retries_on_transport_exception(monkeypatch):
    """Connection error → retry. If all attempts raise, returns None."""
    call_count = {"n": 0}

    def boom(url, **kwargs):
        call_count["n"] += 1
        raise requests.ConnectionError("dns fail")

    sleeps = []
    monkeypatch.setattr(SESSION, "get", boom)
    monkeypatch.setattr(http_mod, "_sleep", lambda s: sleeps.append(s))

    r = http_get("https://example.com/x", retries=3)
    assert r is None
    assert call_count["n"] == 3
    assert sleeps == [2.0, 4.0]


def test_http_get_recovers_after_transport_exception(monkeypatch):
    sequence: list[object] = [
        requests.ConnectionError("dns fail"),
        _make_response(200, b"ok"),
    ]

    def step(url, **kw):
        item = sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    sleeps = []
    monkeypatch.setattr(SESSION, "get", step)
    monkeypatch.setattr(http_mod, "_sleep", lambda s: sleeps.append(s))

    r = http_get("https://example.com/x", retries=3)
    assert r is not None
    assert r.status_code == 200
    assert sleeps == [2.0]


# ---------------------------------------------------------------------------
# Header / session injection
# ---------------------------------------------------------------------------


def test_http_get_session_default_user_agent_used(monkeypatch):
    """No explicit headers → session's default User-Agent reaches the wire."""
    captured: dict = {}

    def fake_get(url, **kwargs):
        # Session.get itself merges its headers with kwargs[headers] —
        # here we just confirm that http_get does NOT override the
        # session default when no headers are passed.
        captured["headers"] = kwargs.get("headers")
        return _make_response(200)

    monkeypatch.setattr(SESSION, "get", fake_get)
    http_get("https://example.com/x")
    assert captured["headers"] is None


def test_http_get_custom_headers_propagate(monkeypatch):
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["headers"] = kwargs.get("headers")
        return _make_response(200)

    monkeypatch.setattr(SESSION, "get", fake_get)
    http_get("https://example.com/x", headers={"Range": "bytes=0-99"})
    assert captured["headers"] == {"Range": "bytes=0-99"}


def test_http_get_alternative_session_used(monkeypatch):
    """If `session=` is passed, the default SESSION is NOT touched."""
    default_called = {"n": 0}
    custom_called = {"n": 0}

    def default_get(url, **kw):
        default_called["n"] += 1
        return _make_response(200)

    def custom_get(url, **kw):
        custom_called["n"] += 1
        return _make_response(200)

    monkeypatch.setattr(SESSION, "get", default_get)

    custom = MagicMock()
    custom.get = custom_get

    http_get("https://example.com/x", session=custom)
    assert default_called["n"] == 0
    assert custom_called["n"] == 1


# ---------------------------------------------------------------------------
# raise_on_failure flag
# ---------------------------------------------------------------------------


def test_http_get_raise_on_failure_with_transport_exception(monkeypatch):
    def boom(url, **kw):
        raise requests.ConnectionError("dns fail")

    monkeypatch.setattr(SESSION, "get", boom)
    monkeypatch.setattr(http_mod, "_sleep", lambda s: None)
    with pytest.raises(requests.ConnectionError):
        http_get("https://example.com/x", retries=2, raise_on_failure=True)


def test_http_get_raise_on_failure_with_http_error(monkeypatch):
    monkeypatch.setattr(SESSION, "get", lambda url, **kw: _make_response(500))
    monkeypatch.setattr(http_mod, "_sleep", lambda s: None)
    with pytest.raises(requests.HTTPError):
        http_get("https://example.com/x", retries=2, raise_on_failure=True)


def test_http_get_rejects_zero_retries():
    with pytest.raises(ValueError):
        http_get("https://example.com/x", retries=0)
