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


# ---------------------------------------------------------------------------
# Circuit breaker + connect-timeout split (2026-06-12, NASA outage hardening)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_breaker_state():
    """Every test starts and ends with clean breaker state. Without this,
    the transport-failure tests above (all on example.com) would trip the
    breaker for every test that follows them."""
    http_mod.reset_circuit_breakers()
    yield
    http_mod.reset_circuit_breakers()


def _transport_boom(url, **kw):
    raise requests.ConnectionError("no route to host")


def test_breaker_opens_after_threshold_failed_calls(monkeypatch):
    calls = []

    def boom(url, **kw):
        calls.append(url)
        raise requests.ConnectionError("down")

    monkeypatch.setattr(SESSION, "get", boom)
    monkeypatch.setattr(http_mod, "_sleep", lambda s: None)

    # Threshold is 2 fully-failed calls; each call makes `retries` attempts.
    assert http_get("https://dead.example.net/a", retries=2) is None
    assert http_get("https://dead.example.net/b", retries=2) is None
    attempts_before_open = len(calls)

    # Breaker now open — no further transport attempts are made.
    assert http_get("https://dead.example.net/c", retries=2) is None
    assert len(calls) == attempts_before_open


def test_breaker_is_per_host(monkeypatch):
    monkeypatch.setattr(SESSION, "get", _transport_boom)
    monkeypatch.setattr(http_mod, "_sleep", lambda s: None)

    assert http_get("https://dead-a.example.net/x", retries=1) is None
    assert http_get("https://dead-a.example.net/x", retries=1) is None  # opens dead-a

    calls = []

    def alive(url, **kw):
        calls.append(url)
        return _make_response(200)

    monkeypatch.setattr(SESSION, "get", alive)
    # Different host is unaffected by dead-a's breaker.
    r = http_get("https://alive.example.net/x", retries=1)
    assert r is not None and r.status_code == 200
    assert calls == ["https://alive.example.net/x"]


def test_breaker_resets_on_non_gateway_response(monkeypatch):
    """A normal response (200/404/500) is a LIVE host and resets the breaker;
    only transport failures + gateway errors (502/503/504) count toward it."""
    monkeypatch.setattr(http_mod, "_sleep", lambda s: None)
    monkeypatch.setattr(SESSION, "get", _transport_boom)
    assert http_get("https://flaky.example.net/x", retries=1) is None  # 1 failure

    # A 404 (host alive, just no data) resets the failure counter.
    monkeypatch.setattr(SESSION, "get", lambda url, **kw: _make_response(404))
    assert http_get("https://flaky.example.net/x", retries=1).status_code == 404

    # Counter was reset by the 404; one more transport failure must NOT open
    # the breaker (threshold is 2 consecutive).
    calls = []

    def boom_counting(url, **kw):
        calls.append(url)
        raise requests.ConnectionError("down")

    monkeypatch.setattr(SESSION, "get", boom_counting)
    assert http_get("https://flaky.example.net/x", retries=1) is None
    assert http_get("https://flaky.example.net/x", retries=1) is None
    assert len(calls) == 2  # both calls really attempted (breaker was closed)


def test_breaker_trips_on_persistent_gateway_error(monkeypatch):
    """A host returning 502/503/504 is effectively dead — repeated gateway
    errors must trip the breaker so we stop hammering it. (NOAA
    coastwatch.noaa.gov outage 2026-06-14+ returned all-503 and, under the
    old 'any response resets' rule, reset the breaker every call and ground
    the whole refresh for 75 min.)"""
    monkeypatch.setattr(http_mod, "_sleep", lambda s: None)
    calls = []

    def gateway_dead(url, **kw):
        calls.append(url)
        return _make_response(503)

    monkeypatch.setattr(SESSION, "get", gateway_dead)
    # Two consecutive 503s open the breaker (threshold 2).
    assert http_get("https://dead-gw.example.net/x", retries=1).status_code == 503
    assert http_get("https://dead-gw.example.net/x", retries=1).status_code == 503
    # Third call short-circuits — breaker open, no real network attempt.
    assert http_get("https://dead-gw.example.net/x", retries=1) is None
    assert len(calls) == 2  # only the first two reached the network


def test_breaker_half_opens_after_cooldown(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(http_mod, "_now", lambda: clock["t"])
    monkeypatch.setattr(http_mod, "_sleep", lambda s: None)
    monkeypatch.setattr(SESSION, "get", _transport_boom)

    assert http_get("https://dead.example.net/x", retries=1) is None
    assert http_get("https://dead.example.net/x", retries=1) is None  # opens

    calls = []

    def recovered(url, **kw):
        calls.append(url)
        return _make_response(200)

    monkeypatch.setattr(SESSION, "get", recovered)

    # Still inside cooldown — short-circuited.
    assert http_get("https://dead.example.net/x", retries=1) is None
    assert calls == []

    # After cooldown the half-open probe goes through and resets the host.
    clock["t"] += http_mod.CIRCUIT_BREAKER_COOLDOWN + 1
    r = http_get("https://dead.example.net/x", retries=1)
    assert r is not None and r.status_code == 200
    assert len(calls) == 1


def test_breaker_open_raises_under_raise_on_failure(monkeypatch):
    monkeypatch.setattr(http_mod, "_sleep", lambda s: None)
    monkeypatch.setattr(SESSION, "get", _transport_boom)
    assert http_get("https://dead.example.net/x", retries=1) is None
    assert http_get("https://dead.example.net/x", retries=1) is None  # opens

    with pytest.raises(requests.ConnectionError):
        http_get("https://dead.example.net/x", retries=1, raise_on_failure=True)


def test_breaker_opt_out(monkeypatch):
    """use_breaker=False callers neither consult nor feed breaker state."""
    monkeypatch.setattr(http_mod, "_sleep", lambda s: None)
    monkeypatch.setattr(SESSION, "get", _transport_boom)
    assert http_get("https://dead.example.net/x", retries=1) is None
    assert http_get("https://dead.example.net/x", retries=1) is None  # opens

    calls = []

    def boom_counting(url, **kw):
        calls.append(url)
        raise requests.ConnectionError("down")

    monkeypatch.setattr(SESSION, "get", boom_counting)
    # Opt-out call still really attempts despite the open breaker.
    assert http_get("https://dead.example.net/x", retries=1, use_breaker=False) is None
    assert len(calls) == 1


def test_scalar_timeout_splits_connect_and_read(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return _make_response(200)

    monkeypatch.setattr(SESSION, "get", fake_get)
    http_get("https://example.com/x", timeout=240)
    assert seen["timeout"] == (http_mod.CONNECT_TIMEOUT, 240.0)


def test_tuple_timeout_passes_through(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return _make_response(200)

    monkeypatch.setattr(SESSION, "get", fake_get)
    http_get("https://example.com/x", timeout=(3.0, 60.0))
    assert seen["timeout"] == (3.0, 60.0)
