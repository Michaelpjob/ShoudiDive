"""Tests for the ingest orchestrator's exact-content dedup guard.

The guard stops a blog/forum scraper that re-publishes (or re-stamps) an
unchanged post from re-accumulating as a fresh "daily" observation, while
leaving sensor feeds (buoys, turbidity) — which legitimately report the same
value on different days — untouched.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # pipeline/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json  # noqa: E402

from validation import ingest  # noqa: E402
from validation.ingest import _content_key, _is_resample_source  # noqa: E402


def test_resample_source_excludes_sensors():
    assert _is_resample_source("dive-shop-diveviz") is True
    assert _is_resample_source("forum-bdoutdoors") is True
    assert _is_resample_source("reddit-ca-divers") is True
    assert _is_resample_source("cdip-buoy") is False
    assert _is_resample_source("ndbc-buoy") is False
    assert _is_resample_source("cencoos") is False


def test_content_key_ignores_obs_id_and_timestamp():
    a = {"obs_id": "x-20260601-laguna-0", "timestamp_utc": "2026-06-01T10:00Z",
         "spot_name": "Laguna", "observed_secchi_ft": 25, "source": "dive-shop-diveviz"}
    same_post_later = dict(a, obs_id="x-20260615-laguna-0", timestamp_utc="2026-06-15T10:00Z")
    diff_value = dict(a, observed_secchi_ft=18)
    assert _content_key(a) == _content_key(same_post_later)  # re-scrape -> same key
    assert _content_key(a) != _content_key(diff_value)       # new reading -> new key


class _Fake:
    def __init__(self, source_id, day, **fields):
        self.source_id = source_id
        self._day, self._fields = day, fields

    def fetch(self):
        return [{
            "obs_id": f"{self.source_id}-2026060{self._day}-spot-0",
            "timestamp_utc": f"2026-06-0{self._day}T10:00Z",
            **self._fields,
        }]


def _rows(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_run_all_drops_rescraped_nonsensor_post(tmp_path, monkeypatch):
    obs = tmp_path / "obs.jsonl"
    monkeypatch.setattr(ingest, "OBS_PATH", obs)
    monkeypatch.setattr(ingest, "HEALTH_PATH", tmp_path / "health.json")
    fields = dict(spot_name="Wreck Alley", observed_secchi_ft=25,
                  source="dive-shop-diveviz", source_url="http://x/post-51",
                  raw_excerpt="20-30' viz at wreck alley")
    monkeypatch.setattr(ingest, "SCRAPERS", [_Fake("dive-shop-diveviz", 1, **fields)])
    ingest.run_all()
    monkeypatch.setattr(ingest, "SCRAPERS", [_Fake("dive-shop-diveviz", 2, **fields)])  # same post, new date
    fresh = ingest.run_all()
    assert fresh == []                 # the re-scrape was dropped
    assert len(_rows(obs)) == 1        # only the first remains


def test_run_all_keeps_sensor_same_value_different_day(tmp_path, monkeypatch):
    obs = tmp_path / "obs.jsonl"
    monkeypatch.setattr(ingest, "OBS_PATH", obs)
    monkeypatch.setattr(ingest, "HEALTH_PATH", tmp_path / "health.json")
    fields = dict(spot_name="46232", observed_sst_f=62.4,
                  source="ndbc-buoy", source_url="http://ndbc/46232")
    monkeypatch.setattr(ingest, "SCRAPERS", [_Fake("ndbc-buoy", 1, **fields)])
    ingest.run_all()
    monkeypatch.setattr(ingest, "SCRAPERS", [_Fake("ndbc-buoy", 2, **fields)])  # same value, new day
    ingest.run_all()
    assert len(_rows(obs)) == 2        # buoys: identical value on 2 days = 2 real obs, NOT deduped
