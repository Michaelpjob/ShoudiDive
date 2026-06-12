"""Shared HTTP helper for the data pipeline.

Stage 6 refactor — pulls the requests-with-retry-and-User-Agent pattern
out of every `pipeline/fetch_*.py` and `pipeline/check_*.py` so the
fetchers stop hand-rolling the same code (different User-Agents,
inconsistent timeouts, no retry).

The contract intentionally stays small:

  * ``DEFAULT_USER_AGENT`` — single canonical UA string, matches the
    one fetch.py used before the refactor. Other fetchers used a
    different "shouldidive/0.1" UA before; this is documented as the
    intentional convergence target.
  * ``SESSION`` — a module-level :class:`requests.Session` with
    ``DEFAULT_USER_AGENT`` set. Reuses the underlying TCP/TLS pool
    across calls, which materially reduces per-fetch latency when a
    single script makes many calls (e.g. fetch.py making 21 ERDDAP
    requests across 3 layers × 7 days).
  * :func:`http_get` — wraps ``SESSION.get`` with exponential backoff
    retries (~3 tries, ~2/4/8 second waits between them). Retries
    fire on:
      - :class:`requests.RequestException` raised by the transport
        layer (DNS, TCP, SSL, read-timeout)
      - HTTP responses with a status >=500 *or* 429 (transient
        upstream signals)
    A 4xx response that isn't 429 is returned as-is and not retried —
    those are caller errors that retrying won't fix.
  * Per-host circuit breaker (2026-06-12) — when every attempt of
    :data:`CIRCUIT_BREAKER_THRESHOLD` consecutive calls to one host
    raises at the transport layer, further calls to that host
    short-circuit to ``None`` for :data:`CIRCUIT_BREAKER_COOLDOWN`
    seconds (then one half-open probe). Prevents a connect-dead
    upstream (the 2026-06 NASA OB.DAAC outage) from stalling a
    multi-date fetch loop until the workflow step times out.

Backwards compatible with the previous behavior: callers that read
``r.status_code`` and bail on non-200 still see the same status codes
they did before, just after the helper has already exhausted its
retry budget on the transient ones. The retry is a strict superset
of "single direct call".

Tests live in ``pipeline/tests/test_lib_http.py``.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_USER_AGENT = "shouldidive-data-pipeline/1.0 (+https://shouldidive.com)"
"""Canonical User-Agent string for every pipeline HTTP call.

Matches the value fetch.py used pre-refactor. Other fetchers used
"shouldidive/0.1" variants; those will converge onto this string as
each one migrates to lib.http.
"""


DEFAULT_TIMEOUT = 180
"""Default per-request timeout in seconds — matches fetch.py's prior
``timeout=180`` against ERDDAP, which can be slow on its first cold
query. Caller can override via the ``timeout`` kwarg on
:func:`http_get`."""


DEFAULT_RETRIES = 3
"""Default total attempt count (1 original + 2 retries). Three tries
matches the prompt's spec; the backoff schedule below sleeps between
attempts so the total wall-clock budget is ~14 s for three tries.

A failing source is more likely to stay failing than recover within
20 s, so adding a fourth retry mostly trades wall-clock for an
unchanged outcome."""


# Backoff schedule (seconds) AFTER each failed attempt. Spec is "~2/4/8";
# with DEFAULT_RETRIES=3 we sleep [2, 4] (no sleep after the final
# attempt). Exposed as a tuple so tests can monkeypatch a fast schedule.
BACKOFF_SCHEDULE = (2.0, 4.0, 8.0)
"""Seconds to sleep before retry attempts 2, 3, 4. We never sleep
after the last attempt — by then the caller has the result it asked
for, success or fail."""


# Status codes we treat as transient and retry. 429 = upstream rate
# limit; 5xx = upstream broke. Everything else (404, 403, etc.) is a
# permanent answer that retrying won't fix.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


CONNECT_TIMEOUT = 10.0
"""TCP-connect timeout in seconds, split out from the read timeout.

A host that is down at the network level (SYN blackhole, no route)
should fail in seconds, not eat the caller's full read timeout per
attempt. Slow-to-first-byte sources (ERDDAP cold queries) are governed
by the READ timeout, which stays at the caller's value — connect
either completes promptly or never will. When a caller passes an
explicit ``(connect, read)`` tuple, it is forwarded untouched.

Added 2026-06-12 after the NASA OB.DAAC outage: oceandata.sci.gsfc
went connect-dead and chl_blend's ``timeout=240`` turned every search
into a ~12-minute-per-date retry stall, blowing refresh-ca-data's
35-minute step budget and leaving the production manifest >36 h stale
(#130/#6)."""


CIRCUIT_BREAKER_THRESHOLD = 2
"""Consecutive fully-failed ``http_get`` calls (every attempt raised a
transport exception) against one host before the breaker opens for
that host."""

CIRCUIT_BREAKER_COOLDOWN = 300.0
"""Seconds an open breaker short-circuits calls to a dead host before
letting a single half-open probe through. If the probe also fails at
the transport layer the breaker re-opens for another cooldown."""

_BREAKERS: dict[str, dict[str, float]] = {}
"""Per-host breaker state: ``{host: {"failures": n, "open_until": ts}}``.
Module-level on purpose — one pipeline process fans many calls at the
same few upstream hosts, and the whole point is that call site N+1
learns from call site N's dead host."""


def _now() -> float:
    """Indirection so tests can monkeypatch the breaker clock."""
    return time.monotonic()


def reset_circuit_breakers() -> None:
    """Forget all breaker state (tests; long-lived callers between runs)."""
    _BREAKERS.clear()


def _breaker_state(host: str) -> dict[str, float]:
    return _BREAKERS.setdefault(host, {"failures": 0, "open_until": 0.0})


def _breaker_allows(host: str) -> bool:
    """True if calls to this host may proceed. When an open breaker's
    cooldown has lapsed, returns True for the half-open probe (state
    stays "failed" until the probe's outcome lands)."""
    state = _breaker_state(host)
    return _now() >= state["open_until"]


def _breaker_record_failure(host: str) -> None:
    state = _breaker_state(host)
    state["failures"] += 1
    if state["failures"] >= CIRCUIT_BREAKER_THRESHOLD:
        state["open_until"] = _now() + CIRCUIT_BREAKER_COOLDOWN
        print(
            f"  [lib.http] circuit breaker OPEN for {host} — "
            f"{int(state['failures'])} consecutive transport failures; "
            f"skipping calls for {CIRCUIT_BREAKER_COOLDOWN:.0f}s",
            flush=True,
        )


def _breaker_record_success(host: str) -> None:
    state = _breaker_state(host)
    if state["failures"]:
        print(f"  [lib.http] circuit breaker reset for {host}", flush=True)
    state["failures"] = 0
    state["open_until"] = 0.0


def _build_session() -> requests.Session:
    """Construct the module-level session.

    Set Accept: */* so we don't accidentally filter binary payloads
    (NetCDF, GRIB2). User-Agent: DEFAULT_USER_AGENT. Connection-pool
    is the requests default — fine for the per-script call volumes
    we make (tens, not thousands).
    """
    s = requests.Session()
    s.headers.update({
        "Accept": "*/*",
        "User-Agent": DEFAULT_USER_AGENT,
    })
    return s


SESSION: requests.Session = _build_session()
"""Module-level session reused for every :func:`http_get` call.

Tests can monkeypatch this in place when they need to inject a mock
transport. Production code never reassigns it; reach for
:meth:`requests.Session.headers.update` if you need to bolt on extra
headers for a specific run.
"""


def _sleep(seconds: float) -> None:
    """Indirection so tests can monkeypatch sleep without monkeypatching
    the stdlib ``time`` module."""
    time.sleep(seconds)


def http_get(
    url: str,
    *,
    timeout: int | float | tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    headers: dict[str, str] | None = None,
    session: requests.Session | None = None,
    raise_on_failure: bool = False,
    use_breaker: bool = True,
    **kwargs: Any,
) -> requests.Response | None:
    """Issue an HTTP GET with exponential-backoff retry.

    Parameters
    ----------
    url
        Absolute URL to fetch.
    timeout
        Per-attempt timeout in seconds. Same semantics as
        ``requests.get(timeout=...)``.
    retries
        Total attempt count (must be >=1). With ``retries=3`` the call
        will try once and then retry up to 2 times if the previous
        attempt was transient-failure.
    headers
        Extra headers to merge on top of the session defaults. Pass an
        explicit ``User-Agent`` to override the session default (rare
        — most callers should let the session default win).
    session
        Optional session override; defaults to the module-level
        :data:`SESSION`. Useful for tests that want a fresh session
        with mounted adapters, and for callers that need a separate
        auth bearer (chl_blend.py keeps its EARTHDATA-bearing session
        instead of mutating this one).
    raise_on_failure
        If True, exhausting retries raises the last seen exception or
        a :class:`requests.HTTPError` on the last response. If False
        (default — matches fetch.py's prior behavior), the helper
        returns the last :class:`requests.Response` on a non-retriable
        non-200, or ``None`` if every attempt raised. Callers that
        already inspect ``r.status_code`` should leave this False.
    use_breaker
        If True (default), participate in the per-host circuit
        breaker: after :data:`CIRCUIT_BREAKER_THRESHOLD` consecutive
        calls to a host in which every attempt raised a transport
        exception, further calls to that host return ``None``
        immediately (or raise :class:`requests.ConnectionError` under
        ``raise_on_failure``) for :data:`CIRCUIT_BREAKER_COOLDOWN`
        seconds, then a single half-open probe is allowed through.
        Any received HTTP response — regardless of status — resets the
        host. Pass False for callers whose job is to measure
        reachability itself (e.g. check_feeds.py probing each feed
        exactly once).
    **kwargs
        Forwarded to :meth:`requests.Session.get` (e.g. ``params=``,
        ``stream=``).

    Returns
    -------
    requests.Response | None
        The last response observed, or ``None`` if every attempt raised
        a transport exception. Callers should check ``r.status_code``
        before consuming the body — the helper does not raise on
        non-200 unless ``raise_on_failure=True``.
    """
    if retries < 1:
        raise ValueError(f"retries must be >= 1, got {retries!r}")

    host = urlparse(url).hostname
    if use_breaker and host and not _breaker_allows(host):
        print(f"  [lib.http] circuit breaker open for {host} — skipping {url}",
              flush=True)
        if raise_on_failure:
            raise requests.ConnectionError(
                f"circuit breaker open for {host} "
                f"(>= {CIRCUIT_BREAKER_THRESHOLD} consecutive transport failures)")
        return None

    # Split a scalar timeout into (connect, read) so connect-dead hosts
    # fail in ~CONNECT_TIMEOUT s instead of the full read budget per
    # attempt. Caller-supplied tuples pass through untouched.
    effective_timeout: float | tuple[float, float]
    if isinstance(timeout, tuple):
        effective_timeout = timeout
    else:
        effective_timeout = (min(CONNECT_TIMEOUT, float(timeout)), float(timeout))

    sess = session if session is not None else SESSION
    merged_headers: dict[str, str] | None = None
    if headers:
        # Don't mutate the session's headers — merge per-call.
        merged_headers = dict(headers)

    last_response: requests.Response | None = None
    last_exc: Exception | None = None

    for attempt in range(retries):
        try:
            r = sess.get(
                url,
                timeout=effective_timeout,
                headers=merged_headers,
                **kwargs,
            )
        except requests.RequestException as exc:
            last_exc = exc
            last_response = None
            # Transient transport failure — retry if budget remains.
            if attempt + 1 < retries:
                _sleep(_backoff_for(attempt))
                continue
            break

        last_response = r
        last_exc = None

        if r.status_code in _RETRY_STATUSES and attempt + 1 < retries:
            _sleep(_backoff_for(attempt))
            continue

        # Non-retriable status — return now (success or permanent failure).
        break

    if use_breaker and host:
        if last_response is not None:
            # Any HTTP response — even a 5xx — means the host is alive;
            # the breaker only guards against transport-dead hosts.
            _breaker_record_success(host)
        else:
            _breaker_record_failure(host)

    if raise_on_failure:
        if last_response is None and last_exc is not None:
            raise last_exc
        if last_response is not None:
            last_response.raise_for_status()
    return last_response


def _backoff_for(attempt_index: int) -> float:
    """Return the sleep duration before the next attempt.

    ``attempt_index`` is the zero-based index of the JUST-FAILED
    attempt. We index into :data:`BACKOFF_SCHEDULE` and clamp to its
    last element when ``retries`` outruns the schedule.
    """
    if attempt_index < 0:
        return 0.0
    if attempt_index >= len(BACKOFF_SCHEDULE):
        return BACKOFF_SCHEDULE[-1]
    return BACKOFF_SCHEDULE[attempt_index]
