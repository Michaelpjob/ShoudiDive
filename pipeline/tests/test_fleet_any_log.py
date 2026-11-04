"""Thinning + index for the every-vessel AIS log (fleet-model/any_log.py)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "fleet-model"))
import any_log  # noqa: E402
from any_log import AnyLog  # noqa: E402

T0 = any_log.epoch("2026-11-04T12:00:00")


def rows(root, day="2026-11-04", shard="56"):
    with open(os.path.join(root, day, shard + ".csv"), encoding="utf-8") as f:
        return [line.rstrip("\n").split(",") for line in f]


def test_under_way_keeps_one_point_per_two_minutes(tmp_path):
    log = AnyLog(str(tmp_path), now=T0)
    kept = [log.position(366123456, T0 + s, 32.7 + s * 1e-5, -117.2, sog=8.0, cog=270, heading=268)
            for s in range(0, 600, 10)]     # a report every 10 s for 10 min
    assert sum(kept) == 5                    # t = 0, 120, 240, 360, 480
    log.close()
    r = rows(str(tmp_path))
    assert [int(x[1]) - T0 for x in r] == [0, 120, 240, 360, 480]
    assert r[0][0] == "366123456" and r[0][4] == "8" and r[0][6] == "268"


def test_stopped_hull_gets_a_half_hour_heartbeat(tmp_path):
    log = AnyLog(str(tmp_path), now=T0)
    kept = [log.position(366123456, T0 + s, 32.7, -117.2, sog=0.0) for s in range(0, 3700, 30)]
    assert sum(kept) == 3                    # t = 0, 1800, 3600


def test_drift_writes_once_it_has_moved(tmp_path):
    log = AnyLog(str(tmp_path), now=T0)
    log.position(366123456, T0, 32.7, -117.2, sog=0.4)
    assert not log.position(366123456, T0 + 300, 32.7005, -117.2, sog=0.4)   # ~55 m
    assert log.position(366123456, T0 + 600, 32.7035, -117.2, sog=0.4)       # ~390 m


def test_sentinels_and_bad_positions(tmp_path):
    log = AnyLog(str(tmp_path), now=T0)
    assert not log.position(366123456, T0, 91.0, 181.0, sog=0)
    assert not log.position(366123456, None, 32.7, -117.2)
    assert log.position(366123456, T0, 32.7, -117.2, sog=102.3, cog=360, heading=511)
    log.close()
    assert rows(str(tmp_path))[0][4:] == ["", "", ""]
    v = json.load(open(tmp_path / "vessels.json"))["v"]["366123456"]
    assert v["s"] is None and v["c"] is None and v["h"] is None


def test_index_carries_identity_and_latest_fix(tmp_path):
    log = AnyLog(str(tmp_path), now=T0)
    log.position(366123456, T0, 32.7, -117.2, sog=0, name="SEA WOLF")
    log.static(366123456, "ShipStaticData",
               {"Name": "SEA WOLF@@@", "Type": 37, "CallSign": "WDA1234", "Destination": "AVALON",
                "Dimension": {"A": 10, "B": 5, "C": 2, "D": 2}}, T0)
    log.static(338000001, "StaticDataReport",
               {"PartNumber": True, "ReportA": {"Valid": False, "Name": ""},
                "ReportB": {"Valid": True, "ShipType": 30, "CallSign": "", "Dimension": {"A": 7, "B": 4}}}, T0)
    log.position(366123456, T0 + 60, 32.71, -117.21, sog=6.5, cog=200)   # not written (thinned) ...
    log.close()
    idx = json.load(open(tmp_path / "vessels.json"))
    v = idx["v"]["366123456"]
    assert (v["n"], v["ty"], v["cs"], v["d"], v["len"]) == ("SEA WOLF", 37, "WDA1234", "AVALON", 15)
    assert v["t"] == T0 + 60 and v["la"] == 32.71 and v["s"] == 6.5   # ... but the index has the newest fix
    assert idx["v"]["338000001"]["ty"] == 30 and "n" not in idx["v"]["338000001"]
    assert idx["since"] == "2026-11-04"


def test_shards_by_last_two_digits_and_reloads_index(tmp_path):
    log = AnyLog(str(tmp_path), now=T0)
    log.position(366123401, T0, 32.7, -117.2, sog=0, name="A")
    log.position(366123499, T0, 32.8, -117.3, sog=0, name="B")
    log.close()
    assert os.path.exists(tmp_path / "2026-11-04" / "01.csv")
    assert os.path.exists(tmp_path / "2026-11-04" / "99.csv")
    again = AnyLog(str(tmp_path), now=T0 + 3600)   # the next CI chunk
    assert again.idx["v"]["366123499"]["n"] == "B" and again.idx["since"] == "2026-11-04"


def test_flush_prunes_old_days_and_silent_hulls(tmp_path):
    old = T0 - 20 * 86400
    log = AnyLog(str(tmp_path), now=old)
    log.position(366000001, old, 32.7, -117.2, sog=0, name="GONE")
    log.position(366000002, T0, 32.7, -117.2, sog=0, name="HERE")
    log.flush(force=True, now=T0)
    days = sorted(d for d in os.listdir(tmp_path) if d[:2] == "20")
    assert days == ["2026-11-04"]
    v = json.load(open(tmp_path / "vessels.json"))["v"]
    assert "366000001" not in v and "366000002" in v
