"""Tests for ingest health: streak carry-over, the health check, and the
CeNCOOS retired-dataset detection.

The 2026-10-20 run that motivated this logged ``ingest health: all
scrapers ok`` while CeNCOOS `mlml_monterey` 404'd every run (dataset
ended 2019-11-29) and three sources had returned 0 observations for
every run on record. These pin the new behaviour: those read red.

Run:
    python -m pytest pipeline/tests/test_ingest_health.py -v
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation import check_ingest_health, watchdog  # noqa: E402
from validation import ingest  # noqa: E402
from validation.ingest import cencoos  # noqa: E402
from validation.ingest._base import (  # noqa: E402
    DEFAULT_MAX_ZERO_RUNS,
    FEED_EMPTY,
    FEED_ENDED,
    FEED_FAILED,
    FEED_OK,
    BaseScraper,
)


RUN = "2026-10-20T12:37:41Z"


# ---- carry_streaks ----------------------------------------------------

def test_zero_streak_increments_and_resets():
    prev = {"source_id": "x", "observation_count": 0, "zero_obs_streak": 2,
            "last_obs_run_utc": "2026-10-19T00:00:00Z"}
    e = ingest.carry_streaks({"source_id": "x", "observation_count": 0}, prev, RUN)
    assert e["zero_obs_streak"] == 3
    assert e["last_obs_run_utc"] == "2026-10-19T00:00:00Z"

    e = ingest.carry_streaks({"source_id": "x", "observation_count": 5}, e, RUN)
    assert e["zero_obs_streak"] == 0
    assert e["last_obs_run_utc"] == RUN


def test_streak_seeds_from_pre_streak_health_file():
    # Health files written before this change have observation_count only.
    old = {"source_id": "x", "status": "ok", "observation_count": 0, "error": None}
    e = ingest.carry_streaks({"source_id": "x", "observation_count": 0}, old, RUN)
    assert e["zero_obs_streak"] == 2
    e = ingest.carry_streaks({"source_id": "x", "observation_count": 0}, None, RUN)
    assert e["zero_obs_streak"] == 1


def test_feed_bad_run_streak():
    prev = {"source_id": "c", "observation_count": 8, "zero_obs_streak": 0, "feeds": [
        {"feed_id": "a", "status": FEED_EMPTY, "bad_run_streak": 3, "last_ok_run_utc": "t0"},
        {"feed_id": "b", "status": FEED_OK, "bad_run_streak": 0, "last_ok_run_utc": "t0"},
    ]}
    cur = {"source_id": "c", "observation_count": 8, "feeds": [
        {"feed_id": "a", "status": FEED_EMPTY, "observation_count": 0},
        {"feed_id": "b", "status": FEED_OK, "observation_count": 8},
        {"feed_id": "new", "status": FEED_FAILED, "observation_count": 0},
    ]}
    e = ingest.carry_streaks(cur, prev, RUN)
    a, b, new = e["feeds"]
    assert (a["bad_run_streak"], a["last_ok_run_utc"]) == (4, "t0")
    assert (b["bad_run_streak"], b["last_ok_run_utc"]) == (0, RUN)
    assert new["bad_run_streak"] == 1 and new["last_ok_run_utc"] is None
    assert all(f["max_bad_runs"] == DEFAULT_MAX_ZERO_RUNS for f in e["feeds"])


# ---- check_ingest_health.evaluate --------------------------------------

def _src(sid, **kw):
    base = {"source_id": sid, "status": "ok", "observation_count": 1, "error": None,
            "max_zero_runs": 4, "zero_obs_streak": 0}
    base.update(kw)
    return base


def test_healthy_payload_has_no_problems():
    health = {"sources": [_src("cdip-buoy", observation_count=6),
                          _src("cencoos", feeds=[{"feed_id": "m", "status": FEED_OK,
                                                  "bad_run_streak": 0}])]}
    assert check_ingest_health.evaluate(health) == []


def test_silent_flags_at_limit_not_before():
    below = {"sources": [_src("beachcitiescuba", observation_count=0, zero_obs_streak=3)]}
    at = {"sources": [_src("beachcitiescuba", observation_count=0, zero_obs_streak=4)]}
    assert check_ingest_health.evaluate(below) == []
    (p,) = check_ingest_health.evaluate(at)
    assert p["kind"] == "silent" and p["source_id"] == "beachcitiescuba"


def test_sparse_source_uses_its_own_limit():
    health = {"sources": [_src("forum-bdoutdoors", observation_count=0,
                               zero_obs_streak=30, max_zero_runs=56)]}
    assert check_ingest_health.evaluate(health) == []


def test_failed_and_ended_flag_immediately():
    health = {"sources": [
        _src("rcca", status="failed", observation_count=0, error="BadZipFile: x"),
        _src("cencoos", observation_count=8, feeds=[
            {"feed_id": "mlml_monterey", "status": FEED_ENDED, "bad_run_streak": 1,
             "detail": "ERDDAP time_coverage_end 2019-11-29"},
        ]),
    ]}
    kinds = {(p["source_id"], p["kind"]) for p in check_ingest_health.evaluate(health)}
    assert kinds == {("rcca", "failed"), ("cencoos", "ended")}


def test_feed_down_needs_a_streak():
    one = {"sources": [_src("reddit", feeds=[
        {"feed_id": "r/scuba", "status": FEED_FAILED, "bad_run_streak": 1, "max_bad_runs": 4}])]}
    four = {"sources": [_src("reddit", feeds=[
        {"feed_id": "r/scuba", "status": FEED_FAILED, "bad_run_streak": 4, "max_bad_runs": 4}])]}
    assert check_ingest_health.evaluate(one) == []
    (p,) = check_ingest_health.evaluate(four)
    assert (p["kind"], p["feed_id"]) == ("feed-down", "r/scuba")


def test_watchdog_r7_reports_silent_sources():
    health = {"sources": [_src("dive-shop-beachcitiescuba", observation_count=0,
                               zero_obs_streak=40)]}
    (f,) = watchdog.rule_ingest_health(health)
    assert f["rule"] == "R7" and "silent" in f["what"]


# ---- CeNCOOS ERDDAP handling --------------------------------------------

INFO_CSV = """Row Type,Variable Name,Attribute Name,Data Type,Value
attribute,NC_GLOBAL,time_coverage_end,String,{end}
attribute,NC_GLOBAL,time_coverage_start,String,2013-02-14T17:10:00Z
variable,time,,double,
"""


def _http_404():
    resp = requests.Response()
    resp.status_code = 404
    return requests.HTTPError("404 Client Error", response=resp)


class _Resp:
    def __init__(self, text):
        self.text = text


def _scraper(monkeypatch, *, data, info):
    """CeNCOOS scraper with one station and canned ERDDAP answers."""
    monkeypatch.setattr(cencoos, "STATIONS", [
        {"dataset_id": "ds", "spot_name": "Test Wharf", "lat": 36.6, "lng": -121.9}])
    s = cencoos.CeNCOOSScraper()

    def fake_get(url, **_):
        body = info if "/info/" in url else data
        if isinstance(body, Exception):
            raise body
        return _Resp(body)

    monkeypatch.setattr(s, "_polite_get", fake_get)
    return s


def test_parse_coverage_end():
    end = cencoos._parse_coverage_end(INFO_CSV.format(end="2019-11-29T15:45:00Z"))
    assert end == datetime(2019, 11, 29, 15, 45, tzinfo=timezone.utc)
    assert cencoos._parse_coverage_end("Row Type,Variable Name\n") is None


def test_retired_dataset_reads_as_ended(monkeypatch):
    s = _scraper(monkeypatch, data=_http_404(),
                 info=INFO_CSV.format(end="2019-11-29T15:45:00Z"))
    assert s.fetch() == []
    (feed,) = s.feed_status
    assert feed["status"] == FEED_ENDED
    assert feed["last_data_utc"] == "2019-11-29T15:45:00Z"


def test_recent_gap_reads_as_empty_not_ended(monkeypatch):
    recent = (datetime.now(timezone.utc) - timedelta(days=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = _scraper(monkeypatch, data=_http_404(), info=INFO_CSV.format(end=recent))
    s.fetch()
    (feed,) = s.feed_status
    assert feed["status"] == FEED_EMPTY


def test_missing_dataset_reads_as_ended(monkeypatch):
    s = _scraper(monkeypatch, data=_http_404(), info=_http_404())
    s.fetch()
    assert s.feed_status[0]["status"] == FEED_ENDED


def test_live_station_reports_ok(monkeypatch):
    t = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = ("time,latitude,longitude,sea_water_turbidity,sea_water_temperature,"
            "mass_concentration_of_chlorophyll_in_sea_water\n"
            "UTC,degrees_north,degrees_east,NTU,degree_Celsius,microg.L-1\n"
            f"{t},36.6,-121.9,0.5,14.0,1.2\n")
    s = _scraper(monkeypatch, data=data, info=AssertionError("no info call when data flows"))
    obs = s.fetch()
    assert len(obs) == 1 and obs[0]["observed_secchi_ft"] is not None
    (feed,) = s.feed_status
    assert (feed["status"], feed["observation_count"], feed["last_data_utc"]) == (FEED_OK, 1, t)


def test_non_404_error_is_failed(monkeypatch):
    s = _scraper(monkeypatch, data=requests.ConnectionError("boom"), info="")
    s.fetch()
    assert s.feed_status[0]["status"] == FEED_FAILED


# ---- run_all end to end -------------------------------------------------

class _Quiet(BaseScraper):
    source_id = "quiet"
    source_confidence = 0.5

    def fetch(self):
        self.report_feed("page", FEED_EMPTY, detail="no visibility label")
        return []


class _Busy(BaseScraper):
    source_id = "busy"
    source_confidence = 0.5

    def fetch(self):
        return [{"obs_id": f"busy-{RUN[:10]}-spot-0", "source": "busy",
                 "timestamp_utc": RUN}]


def test_run_all_carries_streaks_and_check_goes_red(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "SCRAPERS", [_Quiet(), _Busy()])
    monkeypatch.setattr(ingest, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ingest, "OBS_PATH", tmp_path / "observations.jsonl")
    monkeypatch.setattr(ingest, "HEALTH_PATH", tmp_path / "ingest_health.json")
    monkeypatch.setattr(check_ingest_health, "HEALTH_PATH", tmp_path / "ingest_health.json")

    for run in range(1, DEFAULT_MAX_ZERO_RUNS + 1):
        ingest.run_all()
        health = json.loads((tmp_path / "ingest_health.json").read_text(encoding="utf-8"))
        quiet, busy = health["sources"]
        assert quiet["zero_obs_streak"] == run
        assert quiet["feeds"][0]["bad_run_streak"] == run
        assert busy["zero_obs_streak"] == 0
        assert busy["observations_added"] == (1 if run == 1 else 0)
        expected = 1 if run >= DEFAULT_MAX_ZERO_RUNS else 0
        assert check_ingest_health.main() == expected
