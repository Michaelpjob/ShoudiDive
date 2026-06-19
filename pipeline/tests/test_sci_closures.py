"""Parser + assembly tests for pipeline/fetch_sci_closures.py.

Network-free: inline HTML fixtures mirror the real scisland.org day-page
markup (verified live 2026-06-18), and build_closures is exercised with a
monkeypatched per-day fetch. Guards the parse contract that the whole
feature rests on: zone polygons, per-day safety-zone status (row color),
SOAR operation windows with PT->UTC conversion, and the published window.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest  # noqa: F401  (monkeypatch fixture)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fetch_sci_closures as sci  # noqa: E402


# A faithful (trimmed) closure-day page: published-window + "Updated" strings,
# two areaGEO pushes (one safety zone, one SOAR), the safety-zone status rows
# (row color = status), and a SOAR ops row with a 1200->1600 PT window.
CLOSURE_DAY_HTML = """
<html><body>
<script>
var begin_date = new Date('03 Jun 2026');
var end_date = new Date('01 Jul 2026');
var mapLabels = {"SZALFA":{"label":"A","offset":[0,0]},"SOAR S1":{"label":"S1","offset":[0,0]}};
areaGEO.push({"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[-118.59778,33.03417],[-118.61694,33.08222],[-118.51056,33.04667],[-118.59778,33.03417]]]},"properties":{"NAME":"SZALFA"},"centerpt":[-118.5,33.05]}]});
areaGEO.push({"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[-119.04972,32.99444],[-118.93389,32.88278],[-119.08972,32.76306],[-119.04972,32.99444]]]},"properties":{"NAME":"SOAR S1"},"centerpt":[-119.066665,32.87875]}]});
areaGEO.push({"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[-118.36,33.2]},"properties":{"NAME":"spot"}}]});
</script>
<h2>SCI Safety Zone Exclusion Schedule for 18-Jun-2026</h2>
<table>
<tr style='background-color: lightgreen;'><td id='SZALFA' onclick='toggleDisp("SZALFA");'> A</td><td colspan='4' class='bordered'>Available - no scheduled operations</td></tr>
<tr style='background-color: red;'><td id='SZGOLF' onclick='toggleDisp("SZGOLF");'> G</td><td colspan='4' class='bordered'>Always restricted</td></tr>
</table>
<h2>SCI Hazardous Operations Area Schedule for 18-Jun-2026</h2>
<table>
<tr><td id='SOAR S1' class='bordered' rowspan='1' onclick='toggleDisp("SOAR S1");'>SOAR S1</td>
<td class='bordered'>18-JUN-26 1200</td><td class='bordered'>18-JUN-26 1600</td><td class='bordered'>FLR:7000</td></tr>
</table>
<p>Updated Jun 3, 2026, 15:22</p>
</body></html>
"""

# An all-open day: same zones/geometry, but the ops table says no ops.
OPEN_DAY_HTML = CLOSURE_DAY_HTML.replace(
    "<h2>SCI Hazardous Operations Area Schedule for 18-Jun-2026</h2>",
    "<h2>SCI Hazardous Operations Area Schedule for 19-Jun-2026</h2>",
).replace(
    "<tr><td id='SOAR S1' class='bordered' rowspan='1' onclick='toggleDisp(\"SOAR S1\");'>SOAR S1</td>\n"
    "<td class='bordered'>18-JUN-26 1200</td><td class='bordered'>18-JUN-26 1600</td><td class='bordered'>FLR:7000</td></tr>",
    "<tr><td colspan='4'>No operations scheduled today</td></tr>",
).replace("Exclusion Schedule for 18-Jun-2026", "Exclusion Schedule for 19-Jun-2026")


def test_parse_geometry_keeps_polygons_skips_points():
    geo = sci.parse_geometry(CLOSURE_DAY_HTML)
    assert set(geo) == {"SZALFA", "SOAR S1"}  # Point "spot" dropped
    assert geo["SOAR S1"]["geometry"]["type"] == "Polygon"
    assert geo["SOAR S1"]["centroid"] == [-119.066665, 32.87875]


def test_parse_safety_zone_status_from_row_color():
    st = sci.parse_safety_zone_status(CLOSURE_DAY_HTML)
    assert st["SZALFA"] == "open"        # lightgreen
    assert st["SZGOLF"] == "restricted"  # red


def test_parse_operations_window_pt_to_utc():
    ops = sci.parse_operations(CLOSURE_DAY_HTML)
    assert list(ops) == ["SOAR S1"]
    w = ops["SOAR S1"][0]
    assert w["start_local"] == "2026-06-18 12:00 PT"
    assert w["start_utc"] == "2026-06-18T19:00Z"  # PDT -7h
    assert w["end_utc"] == "2026-06-18T23:00Z"
    assert w["altitude"] == "FLR:7000"


def test_parse_operations_empty_on_open_day():
    assert sci.parse_operations(OPEN_DAY_HTML) == {}


def test_parse_page_date_and_window():
    p = sci.parse_closure_page(CLOSURE_DAY_HTML)
    assert p["date"] == "2026-06-18"
    assert p["window"] == {
        "start": "2026-06-03", "end": "2026-07-01",
        "source_updated": "Jun 3, 2026, 15:22",
    }
    assert p["labels"]["SZALFA"] == "A"


def test_parse_unrecognized_color_degrades_to_scheduled():
    html = CLOSURE_DAY_HTML.replace("background-color: lightgreen;", "background-color: orange;")
    assert sci.parse_safety_zone_status(html)["SZALFA"] == "scheduled"


def test_build_closures_assembles_forecast(monkeypatch):
    # Day 0 = closure day; days 1..6 = open. Verify union of geometry, per-date
    # status, and that an ops area reads restricted only on the closure day.
    closure = sci.parse_closure_page(CLOSURE_DAY_HTML)
    open_pages = {date(2026, 6, 19) + __import__("datetime").timedelta(days=i): None for i in range(6)}

    def fake_fetch(target):
        if target == date(2026, 6, 18):
            return closure
        # build a parsed "open" page stamped with this date
        p = sci.parse_closure_page(OPEN_DAY_HTML)
        p["date"] = target.isoformat()
        return p

    monkeypatch.setattr(sci, "_fetch_day", fake_fetch)
    fc = sci.build_closures(today=date(2026, 6, 18))

    assert fc["type"] == "FeatureCollection"
    assert len(fc["dates"]) == 7 and fc["dates"][0] == "2026-06-18"
    ids = {f["properties"]["id"] for f in fc["features"]}
    assert {"SZALFA", "SOAR S1"} <= ids
    soar = next(f for f in fc["features"] if f["properties"]["id"] == "SOAR S1")
    sbd = soar["properties"]["statusByDate"]
    assert sbd["2026-06-18"]["status"] == "restricted"
    assert sbd["2026-06-18"]["windows"][0]["altitude"] == "FLR:7000"
    assert sbd["2026-06-19"]["status"] == "open"
    assert soar["properties"]["kind"] == "operations_area"
    # published metadata propagated
    assert fc["published_window"]["end"] == "2026-07-01"
    assert fc["source"] == "scisland.org"


def test_build_closures_skips_wrong_date_pages(monkeypatch):
    # A page whose rendered date != requested (month rollover) must be dropped.
    monkeypatch.setattr(sci, "_fetch_day", lambda target: None)
    fc = sci.build_closures(today=date(2026, 6, 18))
    assert fc["features"] == [] and len(fc["dates"]) == 7
