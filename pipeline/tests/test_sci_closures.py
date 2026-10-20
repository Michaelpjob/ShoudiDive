"""Parser + assembly tests for pipeline/fetch_sci_closures.py.

Network-free: the inline fixture mirrors the real scisland.org
assets/js/data.js (verified live 2026-10-19): a regeneration-stamp comment,
then `const data = {dates, events, points, areas}` with per-day safety-zone
event indexes, the full-day `sched` list, "AREA, start, end, alt" event
strings in local Pacific time, and [lat, lng] string polygons. Guards the
contract the whole layer rests on: zone polygons, per-day status (open /
scheduled / restricted / unknown), PT->UTC windows, and the published window.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fetch_sci_closures as sci  # noqa: E402

_SQUARE = [["33.00", "-118.60"], ["33.05", "-118.60"], ["33.05", "-118.55"], ["33.00", "-118.55"]]

DATA = {
    "dates": {
        # Day 0: zone A clear, zone B has a 3 h op, SOAR S1 has a window.
        "19-Oct-26": {"sz": {"A": [], "B": [1], "C": [], "D": [], "E": [], "F": [], "W": [0]},
                      "sched": [0, 1, 2]},
        # Day 1: zone C closed for a full 24 h.
        "20-Oct-26": {"sz": {"A": [], "B": [], "C": [3], "D": [], "E": [], "F": [], "W": []},
                      "sched": [3]},
        # Day 2 intentionally absent -> unknown.
        # A typo'd year the real file carries; must not stretch the window.
        "18-Nov-20": {"sz": {}, "sched": []},
        "18-Nov-26": {"sz": {}, "sched": []},
    },
    "events": [
        "W, 19-Oct-26 0000, 19-Oct-26 2400, 0:0",
        "TA-2, 19-Oct-26 1300, 19-Oct-26 1600, 0:5000",
        "SOAR S1, 19-Oct-26 0800, 19-Oct-26 1100, -90000:50000",
        "TAR-14, 20-Oct-26 0000, 21-Oct-26 0000, 0:0",
    ],
    "points": {k: _SQUARE for k in ("A", "B", "C", "D", "E", "F", "G", "W", "SOAR S1", "TA-2", "TAR-14")},
    "areas": [],
}
DATA_JS = "//2026-Oct-19 20:49:18\r\n\r\n\tconst data= " + json.dumps(DATA) + ";\r\n"
TODAY = date(2026, 10, 19)


def _feature(fc, fid):
    return next(f for f in fc["features"] if f["properties"]["id"] == fid)


def test_parse_data_js_object_and_stamp():
    data, stamp = sci.parse_data_js(DATA_JS)
    assert set(data) == {"dates", "events", "points", "areas"}
    assert stamp == "Oct 19, 2026, 20:49"


def test_parse_data_js_rejects_html():
    try:
        sci.parse_data_js("<html>Access Denied</html>")
    except ValueError:
        return
    raise AssertionError("expected ValueError for a non-data.js body")


def test_parse_event_pt_to_utc_and_altitude():
    w = sci.parse_event("SOAR S1, 19-Oct-26 0800, 19-Oct-26 1100, -90000:50000")
    assert w["area"] == "SOAR S1"
    assert w["start_local"] == "2026-10-19 08:00 PT"
    assert w["start_utc"] == "2026-10-19T15:00Z"   # PDT -7h
    assert w["end_utc"] == "2026-10-19T18:00Z"
    assert w["hours"] == 3
    assert w["altitude"] == "FLOOR:50K"


def test_parse_event_2400_rolls_to_next_day():
    w = sci.parse_event("W, 19-Oct-26 0000, 19-Oct-26 2400, 0:0")
    assert w["end_local"] == "2026-10-20 00:00 PT"
    assert w["hours"] == 24
    assert w["altitude"] == "SFC:SFC"


def test_friendly_altitude_matches_site():
    assert sci.friendly_altitude("0:5000") == "SFC:5000"
    assert sci.friendly_altitude("0:30000") == "SFC:30K"
    assert sci.friendly_altitude("-90000:8000") == "FLOOR:8000"


def test_build_closures_statuses():
    data, stamp = sci.parse_data_js(DATA_JS)
    fc = sci.build_closures(data, stamp, TODAY)
    assert fc["type"] == "FeatureCollection"
    assert fc["dates"][0] == "2026-10-19" and len(fc["dates"]) == 7

    a = _feature(fc, "SZALFA")["properties"]["statusByDate"]
    b = _feature(fc, "SZBRAVO")["properties"]["statusByDate"]
    c = _feature(fc, "SZCHARLIE")["properties"]["statusByDate"]
    g = _feature(fc, "SZGOLF")["properties"]["statusByDate"]
    w = _feature(fc, "SZWILCOVE")["properties"]
    assert a["2026-10-19"]["status"] == "open"
    assert b["2026-10-19"]["status"] == "scheduled"           # 3 h op
    assert b["2026-10-19"]["windows"][0]["area"] == "TA-2"
    assert c["2026-10-20"]["status"] == "restricted"          # 24 h op
    assert a["2026-10-21"]["status"] == "unknown"             # date not published
    assert g["2026-10-21"]["status"] == "restricted"          # standing restriction
    assert w["label"] == "W/C" and w["statusByDate"]["2026-10-19"]["status"] == "restricted"


def test_build_closures_ops_areas_only_when_scheduled():
    data, stamp = sci.parse_data_js(DATA_JS)
    fc = sci.build_closures(data, stamp, TODAY)
    ops = {f["properties"]["id"] for f in fc["features"]
           if f["properties"]["kind"] == "operations_area"}
    assert ops == {"SOAR S1", "TA-2", "TAR-14"}
    soar = _feature(fc, "SOAR S1")["properties"]["statusByDate"]
    assert soar["2026-10-19"]["status"] == "scheduled"
    assert soar["2026-10-20"]["status"] == "open"
    assert soar["2026-10-21"]["status"] == "unknown"
    assert _feature(fc, "TAR-14")["properties"]["statusByDate"]["2026-10-20"]["status"] == "restricted"


def test_build_closures_geometry_and_metadata():
    data, stamp = sci.parse_data_js(DATA_JS)
    fc = sci.build_closures(data, stamp, TODAY)
    geom = _feature(fc, "SZALFA")["geometry"]
    ring = geom["coordinates"][0]
    assert geom["type"] == "Polygon"
    assert ring[0] == [-118.60, 33.00] and ring[0] == ring[-1]   # [lng, lat], closed
    assert _feature(fc, "SZALFA")["properties"]["centroid"] == [-118.575, 33.025]
    assert fc["published_window"] == {"start": "2026-10-19", "end": "2026-11-18"}
    assert fc["source_updated"] == "Oct 19, 2026, 20:49"
    assert fc["source"] == "scisland.org"
