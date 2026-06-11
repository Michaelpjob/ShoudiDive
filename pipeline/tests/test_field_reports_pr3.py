"""Field Reports PR-3 — the recent-observations publisher."""
from __future__ import annotations

from pipeline.validation.publish_field_reports import _kind, _what_value, collect


def test_kind_classification():
    assert _kind({"source": "cdip-buoy"}) == "buoy"
    assert _kind({"source": "ndbc-buoy"}) == "buoy"
    assert _kind({"source": "cencoos"}) == "turbidity"
    assert _kind({"source": "rcca-mpa-baseline"}) == "turbidity"
    assert _kind({"source": "dive-shop-justgetwet"}) == "dive_report"


def test_what_value_prefers_visibility():
    what, val, unit = _what_value({"observed_secchi_ft": 12.0, "observed_sst_f": 64.0})
    assert what == "Visibility" and val == 12.0 and unit == "ft"


def test_what_value_falls_back_to_temp():
    what, val, unit = _what_value({"observed_secchi_ft": None, "observed_sst_f": 64.0})
    assert what == "Water temp" and val == 64.0


def test_what_value_none_when_empty():
    _, val, _ = _what_value(
        {"observed_secchi_ft": None, "observed_sst_f": None, "observed_swell_ft": None}
    )
    assert val is None


def test_collect_filters_by_bbox_and_publishes_ui_shape():
    bbox = {"lat_min": 31.8, "lat_max": 42.0, "lng_min": -128.5, "lng_max": -116.8}
    pts = collect(bbox, recent_days=30)
    for p in pts[:5]:
        for k in ("id", "lat", "lng", "kind", "what", "value", "when", "source"):
            assert k in p, f"published point missing {k}"
        assert bbox["lat_min"] <= p["lat"] <= bbox["lat_max"]
        assert p["value"] is not None
