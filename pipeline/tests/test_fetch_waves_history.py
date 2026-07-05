"""Regression tests for fetch_waves.find_history_runs.

The 2026-05-24 Stage 6a refactor (lib/nomads) removed the module-local
`_idx_url` helper but left a call to it inside find_history_runs. Every
hourly wind-cron run from then until 2026-07-04 died with a NameError
right after writing wave_now.png — silently, because the workflow step
runs with continue-on-error — freezing wave_max_3d.png and the manifest
`wave` entry at 41 days stale while the visibility model kept reading
the frozen 3-day swell envelope for bottom-stir.

These tests execute the history-walk path with nomads.head_ok mocked so
any future dangling reference in it fails unit CI, not silently in prod.

Run:
    python -m pytest pipeline/tests/test_fetch_waves_history.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fetch_waves  # noqa: E402


def test_find_history_runs_returns_daily_handles_when_all_published():
    with patch.object(fetch_waves.nomads, "head_ok", return_value=True):
        runs = fetch_waves.find_history_runs(date(2026, 7, 4), 12, n_days=4)
    # Latest run first, then one handle per prior day at the same hour.
    assert runs == [
        (date(2026, 7, 4), 12),
        (date(2026, 7, 3), 12),
        (date(2026, 7, 2), 12),
        (date(2026, 7, 1), 12),
    ]


def test_find_history_runs_walks_back_6h_when_same_hour_missing():
    # Same-hour probe 404s; the 6h-earlier cycle exists.
    calls = []

    def fake_head_ok(url, *, timeout=30):
        calls.append(url)
        # Reject the first probe of each day (same-hour), accept the second.
        return "t06z" in url

    with patch.object(fetch_waves.nomads, "head_ok", side_effect=fake_head_ok):
        runs = fetch_waves.find_history_runs(date(2026, 7, 4), 12, n_days=2)
    assert runs[0] == (date(2026, 7, 4), 12)
    assert runs[1] == (date(2026, 7, 3), 6)


def test_find_history_runs_skips_days_with_no_published_cycle():
    with patch.object(fetch_waves.nomads, "head_ok", return_value=False):
        runs = fetch_waves.find_history_runs(date(2026, 7, 4), 12, n_days=4)
    # Nothing published in the lookback window — only the latest handle.
    assert runs == [(date(2026, 7, 4), 12)]


def test_find_history_runs_probes_gfswave_idx_urls():
    seen = []

    def fake_head_ok(url, *, timeout=30):
        seen.append(url)
        return True

    with patch.object(fetch_waves.nomads, "head_ok", side_effect=fake_head_ok):
        fetch_waves.find_history_runs(date(2026, 7, 4), 12, n_days=2)
    assert seen, "history walk should probe NOMADS idx URLs"
    assert all(u.endswith(".grib2.idx") for u in seen)
    assert all("gfswave" in u for u in seen)
