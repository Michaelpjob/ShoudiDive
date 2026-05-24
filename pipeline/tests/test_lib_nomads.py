"""Unit tests for pipeline/lib/nomads.py.

The functions under test do network I/O (head_ok) and time-based
cycle-walking (find_latest_run). Tests mock the requests.Session and
inject a clock via the `now=` kwarg so they're deterministic and run
without network access.

Run:
    python -m pytest pipeline/tests/test_lib_nomads.py -v
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import nomads  # noqa: E402


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


# ---------------------------------------------------------------------
# head_ok
# ---------------------------------------------------------------------

def test_head_ok_returns_true_on_200():
    with patch.object(nomads._SESSION, "head", return_value=FakeResponse(200)):
        assert nomads.head_ok("https://example.test/x.idx") is True


def test_head_ok_returns_false_on_hard_404():
    with patch.object(nomads._SESSION, "head", return_value=FakeResponse(404)):
        assert nomads.head_ok("https://example.test/x.idx") is False


def test_head_ok_falls_back_to_range_get_on_throttle():
    # HEAD returns 429 (throttled), range GET returns 206 — should
    # succeed on the GET fallback within the first attempt.
    head_mock = MagicMock(return_value=FakeResponse(429))
    get_mock = MagicMock(return_value=FakeResponse(206))
    with patch.object(nomads._SESSION, "head", head_mock), \
         patch.object(nomads._SESSION, "get", get_mock), \
         patch.object(nomads.time, "sleep"):  # don't actually wait
        assert nomads.head_ok("https://example.test/x.idx") is True
    assert head_mock.call_count == 1
    assert get_mock.call_count == 1


def test_head_ok_retries_up_to_three_times_then_gives_up():
    head_mock = MagicMock(return_value=FakeResponse(429))
    get_mock = MagicMock(return_value=FakeResponse(503))
    with patch.object(nomads._SESSION, "head", head_mock), \
         patch.object(nomads._SESSION, "get", get_mock), \
         patch.object(nomads.time, "sleep"):
        assert nomads.head_ok("https://example.test/x.idx") is False
    # 3 attempts, each tries HEAD then GET = 3 HEADs + 3 GETs.
    assert head_mock.call_count == 3
    assert get_mock.call_count == 3


# ---------------------------------------------------------------------
# find_latest_run
# ---------------------------------------------------------------------

def test_find_latest_run_returns_current_cycle_when_published():
    # Mock the clock to 2026-05-24 14:00z. The current 6-hour cycle is
    # 12z. head_ok returns True for the first probe.
    fake_now = datetime(2026, 5, 24, 14, 0, tzinfo=timezone.utc)
    with patch.object(nomads, "head_ok", return_value=True):
        d, h = nomads.find_latest_run(
            idx_url_for=lambda dd, hh: f"https://test/{dd}/{hh}",
            now=fake_now,
            label="HRRR f48",
        )
    assert d == date(2026, 5, 24)
    assert h == 12


def test_find_latest_run_walks_back_through_unpublished_cycles():
    # 14:00z → start at 12z. First two cycles unpublished, third resolves.
    # That's 12z, 06z, then 00z — should return 00z of the same day.
    fake_now = datetime(2026, 5, 24, 14, 0, tzinfo=timezone.utc)
    head_calls = [False, False, True]
    with patch.object(nomads, "head_ok", side_effect=head_calls):
        d, h = nomads.find_latest_run(
            idx_url_for=lambda dd, hh: f"https://test/{dd}/{hh}",
            now=fake_now,
            label="GFS f120",
        )
    assert d == date(2026, 5, 24)
    assert h == 0


def test_find_latest_run_crosses_day_boundary():
    # 03:00z on the 24th → start at 00z. Then 18z on the 23rd, then 12z.
    fake_now = datetime(2026, 5, 24, 3, 0, tzinfo=timezone.utc)
    with patch.object(nomads, "head_ok", side_effect=[False, False, True]):
        d, h = nomads.find_latest_run(
            idx_url_for=lambda dd, hh: f"https://test/{dd}/{hh}",
            now=fake_now,
            label="test",
        )
    assert d == date(2026, 5, 23)
    assert h == 12


def test_find_latest_run_raises_after_exhausting_lookback():
    fake_now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    with patch.object(nomads, "head_ok", return_value=False):
        with pytest.raises(RuntimeError, match="No HRRR f48 run found"):
            nomads.find_latest_run(
                idx_url_for=lambda dd, hh: f"https://test/{dd}/{hh}",
                now=fake_now,
                label="HRRR f48",
                max_lookback_cycles=4,
            )


# ---------------------------------------------------------------------
# URL composers
# ---------------------------------------------------------------------

def test_hrrr_sfc_idx_url_shape():
    url = nomads.hrrr_sfc_idx_url(date(2026, 5, 24), 12, fhour=48)
    assert url == (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/"
        "hrrr.20260524/conus/hrrr.t12z.wrfsfcf48.grib2.idx"
    )


def test_gfs_pgrb2_idx_url_shape():
    url = nomads.gfs_pgrb2_idx_url(date(2026, 5, 24), 6, fhour=120)
    assert url == (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"
        "gfs.20260524/06/atmos/gfs.t06z.pgrb2.0p25.f120.idx"
    )


def test_gfswave_idx_url_picks_subset():
    wcoast = nomads.gfswave_idx_url(
        date(2026, 5, 24), 18, fhour=0, subset="wcoast.0p16"
    )
    atlocn = nomads.gfswave_idx_url(
        date(2026, 5, 24), 18, fhour=0, subset="atlocn.0p16"
    )
    assert "wcoast.0p16" in wcoast
    assert "atlocn.0p16" in atlocn
    assert wcoast != atlocn
