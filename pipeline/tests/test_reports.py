"""Tests for pipeline/reports (the Morning Report facts layer).

Network-free. The fixtures in tests/fixtures/reports/ are the real NWS, NHC
and NDBC products from the morning of 2026-10-15 (a Beach Hazards Statement
with a 15-second south swell, a Small Craft Advisory and Gale Watch in the LA
inner waters, and a Santa Ana pattern building for Friday), so the end-to-end
test pins verdicts against a day whose conditions we checked by hand.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reports import build_report, nws_text, physics, render, season, sources, verdicts  # noqa: E402
from reports import thresholds as T  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "reports"
NOW = datetime(2026, 10, 15, 17, 40, tzinfo=timezone.utc)  # 10:40 AM PDT


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# --- Parsers -----------------------------------------------------------------

def test_cwf_sgx_zones_and_wave_trains():
    z = nws_text.split_zones(_read("cwf_sgx_2026-10-15.txt"))
    assert {"PZZ700", "PZZ740", "PZZ745"} <= set(z)
    p740 = nws_text.parse_marine_zone(z["PZZ740"])
    assert p740["headlines"] == []
    today = nws_text.pick_day_period(p740["periods"], 0, 3)
    assert today["name"] == "TODAY"
    assert today["seas_lo_ft"] == 3 and today["seas_hi_ft"] == 4
    assert today["trains"] == [{"dir": "NW", "ft": 2, "s": 4}, {"dir": "W", "ft": 1, "s": 13}, {"dir": "S", "ft": 3, "s": 15}]
    fri = nws_text.pick_day_period(p740["periods"], 1, 4)
    assert fri["name"] == "FRI"
    assert fri["wind"]["directions"] == ["NE"]
    assert fri["wind"]["sustained_hi_kt"] == 15 and fri["wind"]["gust_kt"] == 20


def test_cwf_lox_headlines_and_subarea_wind_takes_the_max():
    z = nws_text.split_zones(_read("cwf_lox_2026-10-15.txt"))
    p655 = nws_text.parse_marine_zone(z["PZZ655"])
    assert "SMALL CRAFT ADVISORY IN EFFECT THROUGH LATE TONIGHT" in p655["headlines"]
    assert any(h.startswith("GALE WATCH") for h in p655["headlines"])
    fri = nws_text.pick_day_period(p655["periods"], 1, 4)
    # "From Point Mugu to Santa Monica, NE wind 20 to 30 kt with gusts to 35 kt ... Otherwise, NE wind 10 to 15 kt"
    assert fri["wind"]["sustained_hi_kt"] == 30 and fri["wind"]["gust_kt"] == 35


def test_nhc_pmz009_period_seas():
    z = nws_text.split_zones(_read("off_pz7_2026-10-15.txt"))
    p = nws_text.parse_marine_zone(z["PMZ009"])
    today = nws_text.pick_day_period(p["periods"], 0, 3)
    assert today["seas_hi_ft"] == 5 and today["period_s"] == 14 and today["trains"] == []


def test_srf_county_block():
    z = nws_text.split_zones(_read("srf_sgx_2026-10-15.txt"))
    s = nws_text.parse_srf_zone(z["CAZ043"])
    assert "BEACH HAZARDS STATEMENT THROUGH 2 PM THIS AFTERNOON" in s["headlines"]
    today, fri = s["periods"]
    assert today["rip_risk"] == "High" and today["sets_ft"] == 6 and today["swell_from_deg"] == 200
    assert today["surf_text"] == "3 to 5 feet, sets to 6 feet"
    assert today["water_temp_lo_f"] == 69 and today["water_temp_hi_f"] == 72
    assert fri["name"] == "FRIDAY" and fri["rip_risk"] == "Moderate" and fri["sets_ft"] == 5
    assert nws_text.pick_day_period(s["periods"], 1, 4)["name"] == "FRIDAY"


def test_ugc_expansion():
    assert nws_text._expand_ugc("PZZ750-775") == ["PZZ750", "PZZ775"]
    assert nws_text._expand_ugc("CAZ043>045") == ["CAZ043", "CAZ044", "CAZ045"]


def test_issued_line_to_utc():
    assert sources.parse_issued("201 AM PDT Thu Oct 15 2026") == datetime(2026, 10, 15, 9, 1, tzinfo=timezone.utc)
    assert sources.parse_issued("919 AM PDT Thu Oct 15 2026") == datetime(2026, 10, 15, 16, 19, tzinfo=timezone.utc)


def test_ndbc_fills_wave_fields_from_the_previous_row():
    rec = sources.parse_ndbc_txt(_read("ndbc_46086.txt"))
    assert rec["wvht_m"] is not None and rec["wtmp_c"] is not None and rec["wspd_ms"] is not None


# --- Verdict rules -----------------------------------------------------------

WIN = (NOW, NOW + timedelta(hours=8))
SITE = {"faces_deg": 250, "land_zone": "CAZ043", "beach_entry": True}


def _srf(rip="Low", sets=2, swell=250):
    return {"rip_risk": rip, "sets_ft": sets, "swell_from_deg": swell, "thunder": "None expected"}


def _marine(dirs=("NW",), hi=10, gust=None, trains=(), seas=3, period=None, fog=None):
    return {"wind": {"directions": list(dirs), "sustained_hi_kt": hi, "gust_kt": gust},
            "seas_lo_ft": seas, "seas_hi_ft": seas, "trains": [dict(t) for t in trains], "period_s": period,
            "fog_vis_nm": fog, "dense_fog": False}


def _shore(**kw):
    args = dict(srf_period=_srf(), alerts=[], marine_period=_marine(), rain={"inches": 0.0}, window=WIN, src={})
    args.update(kw)
    return verdicts.shore_day(SITE, **args)


def test_shore_calm_day_is_within():
    assert _shore()["level"] == "within"


def test_shore_high_rip_is_outside_facing_but_marginal_in_the_lee():
    assert _shore(srf_period=_srf(rip="High"))["level"] == "outside"
    lee = _shore(srf_period=_srf(rip="High", swell=70))
    assert lee["exposure"] == "lee" and lee["level"] == "marginal"


def test_shore_long_period_raises_only_where_the_entry_faces_the_swell():
    trains = [{"dir": "S", "ft": 3, "s": 15}]
    facing = _shore(srf_period=_srf(sets=2, swell=240), marine_period=_marine(trains=trains))
    assert facing["level"] == "marginal"  # 2 ft would be within; 15 s raises one level
    oblique = _shore(srf_period=_srf(sets=2, swell=170), marine_period=_marine(trains=trains))
    assert oblique["exposure"] == "oblique" and oblique["level"] == "within"


def test_shore_missing_surf_forecast_is_not_enough_data():
    assert _shore(srf_period=None)["level"] == "unknown"


def test_shore_outside_wins_over_missing_data():
    alert = {"event": "High Surf Advisory", "zones": ["CAZ043"], "onset": None, "ends": None}
    assert _shore(srf_period=None, alerts=[alert])["level"] == "outside"


def test_shore_rain_in_72h_is_outside_and_missing_gauge_does_not_block():
    assert _shore(rain={"inches": 0.35})["level"] == "outside"
    assert _shore(rain=None)["level"] == "within"


def test_alert_outside_the_window_does_not_count():
    later = {"event": "High Surf Advisory", "zones": ["CAZ043"],
             "onset": (NOW + timedelta(hours=20)).isoformat(), "ends": (NOW + timedelta(hours=30)).isoformat()}
    assert _shore(alerts=[later])["level"] == "within"


RANGE = {"kind": "offshore", "office": "SGX", "alert_zones": ["PZZ745"], "crossing": False}


def _boat(rng=RANGE, **kw):
    args = dict(period=_marine(), block_headlines=[], alerts=[], window=WIN, src={})
    args.update(kw)
    return verdicts.boat_day(rng, **args)


def test_boat_nws_steepness_rule():
    assert _boat(period=_marine(trains=[{"dir": "NW", "ft": 6, "s": 6}], seas=6))["level"] == "outside"
    assert _boat(period=_marine(trains=[{"dir": "NW", "ft": 3, "s": 4}]))["level"] == "marginal"
    assert _boat(period=_marine(trains=[{"dir": "NW", "ft": 2, "s": 4}, {"dir": "S", "ft": 3, "s": 15}]))["level"] == "within"


def test_boat_advisory_and_watch():
    sca = {"event": "Small Craft Advisory", "zones": ["PZZ745"], "onset": None, "ends": None}
    assert _boat(alerts=[sca])["level"] == "outside"
    watch = {"event": "Gale Watch", "zones": ["PZZ745"], "onset": None, "ends": None}
    assert _boat(alerts=[watch])["level"] == "marginal"
    assert _boat(rng={**RANGE, "crossing": True}, alerts=[watch])["level"] == "outside"


def test_boat_santa_ana_and_fog():
    sa = _boat(period=_marine(dirs=("NE",), hi=15, gust=20))
    assert sa["level"] == "outside"
    inshore = _boat(rng={**RANGE, "kind": "inshore"}, period=_marine(dirs=("NE",), hi=15, gust=20))
    assert inshore["level"] == "marginal"
    assert _boat(period=_marine(fog=1.0))["level"] == "outside"


def test_boat_alerts_feed_down_is_not_enough_data():
    assert _boat(alerts=None)["level"] == "unknown"


def test_merge_route_keeps_the_worst_and_names_the_zone():
    own = _boat()
    crossed = _boat(period=_marine(dirs=("NE",), hi=15, gust=20))
    m = verdicts.merge_route([("PMZ009", own), ("PZZ740", crossed)])
    assert m["level"] == "outside"
    sa = next(f for f in m["factors"] if f["key"] == "santa_ana")
    assert "San Diego inshore waters" in sa["value"]


# --- Physics, season, posts --------------------------------------------------

def test_orbital_velocity_matches_linear_theory_table():
    assert abs(physics.bottom_orbital_velocity(1.0, 12, 10) - 0.45) < 0.02
    assert abs(physics.bottom_orbital_velocity(1.0, 8, 20) - 0.20) < 0.02
    assert physics.bottom_orbital_velocity(1.0, 16, 10) > physics.bottom_orbital_velocity(1.0, 8, 10)


def test_sun_times_san_diego_solstice():
    rise, set_ = physics.sun_times(date(2026, 6, 21), 32.7157, -117.1611)
    # NOAA: about 5:41 AM and 8:00 PM PDT (12:41 and 03:00 UTC)
    assert abs((rise - datetime(2026, 6, 21, 12, 41, tzinfo=timezone.utc)).total_seconds()) < 240
    assert abs((set_ - datetime(2026, 6, 22, 3, 0, tzinfo=timezone.utc)).total_seconds()) < 240 or \
        abs((set_ - datetime(2026, 6, 21, 3, 0, tzinfo=timezone.utc)).total_seconds()) < 240


def test_moon_phase_reference_dates():
    assert physics.moon(datetime(2024, 1, 11, 12, tzinfo=timezone.utc))["illuminated"] < 0.03
    assert physics.moon(datetime(2024, 1, 25, 18, tzinfo=timezone.utc))["illuminated"] > 0.97


def test_lobster_season_rule():
    o, c = season.lobster_season(2026)
    assert (o.date(), o.hour) == (date(2026, 10, 2), 18) and c.date() == date(2027, 3, 17)
    o25, _ = season.lobster_season(2025)
    assert o25.date() == date(2025, 9, 26)
    assert season.lobster_status(date(2026, 10, 15))["open"] is True
    assert season.lobster_status(date(2026, 9, 1))["open"] is False


def test_groundfish_quarter():
    assert "nearshore rockfish" in season.groundfish_status(date(2026, 10, 15))["text"]
    assert "exempt" in season.groundfish_status(date(2026, 10, 15))["text"]


def test_posts_dated_by_slug_or_first_seen():
    rows = []
    for day in range(1, 20):
        rows.append({"source": "dive-shop-example", "spot_name": "Wreck Alley", "timestamp_utc": f"2026-10-{day:02d}T01:00Z",
                     "source_url": "https://example.com/blogs/report-51", "raw_excerpt": "Reports of 20-30' viz",
                     "observed_secchi_ft": 25.0})
    rows.append({"source": "dive-shop-justgetwet", "spot_name": "La Jolla", "timestamp_utc": "2026-10-15T00:56Z",
                 "source_url": "https://justgetwet.com/blogs/dive-reports-and-conditions/october-14th",
                 "raw_excerpt": "Vis: 0-10 ft Swell: 2-5+ ft", "observed_secchi_ft": 5.0})
    out = sources.summarize_posts(rows, NOW - timedelta(days=5), NOW)
    by_src = {p["source"]: p for p in out["posts"]}
    assert by_src["dive-shop-justgetwet"]["post_date"] == "2026-10-14"
    assert by_src["dive-shop-justgetwet"]["vis_text"] == "0-10 ft"
    # Undated page re-read daily since Oct 1: dated by first sight, and flagged.
    assert by_src["dive-shop-example"]["post_date"] == "2026-10-01"
    assert "dive-shop-example" in out["stale_sources"]


def test_banned_words_are_caught():
    try:
        render.check_text(["Conditions look safe today."])
    except ValueError:
        pass
    else:
        raise AssertionError("'safe' slipped through")
    render.check_text(["Within our thresholds."])


# --- End to end on the 2026-10-15 products ----------------------------------

def _fixture_sources() -> dict:
    def ok(sid, data, t):
        return sources.source(sid, sid, None, ok=True, time=t, data=data, now=NOW)
    S = {
        "alerts": ok("alerts", sources.normalize_alerts(json.loads(_read("alerts_2026-10-15.json"))), NOW),
        "cwf_sgx": ok("cwf_sgx", {"text": _read("cwf_sgx_2026-10-15.txt"), "issued_line": "201 AM PDT Thu Oct 15 2026"},
                      datetime(2026, 10, 15, 9, 1, tzinfo=timezone.utc)),
        "cwf_lox": ok("cwf_lox", {"text": _read("cwf_lox_2026-10-15.txt"), "issued_line": "704 AM PDT Thu Oct 15 2026"},
                      datetime(2026, 10, 15, 14, 4, tzinfo=timezone.utc)),
        "off_nhc": ok("off_nhc", {"text": _read("off_pz7_2026-10-15.txt"), "issued_line": "919 AM PDT Thu Oct 15 2026"},
                      datetime(2026, 10, 15, 16, 19, tzinfo=timezone.utc)),
        "srf_sgx": ok("srf_sgx", {"text": _read("srf_sgx_2026-10-15.txt"), "issued_line": "140 AM PDT Thu Oct 15 2026"},
                      datetime(2026, 10, 15, 8, 40, tzinfo=timezone.utc)),
        "rain": ok("rain", {"inches": 0.0}, NOW),
    }
    for b in ("46254", "46232", "46225", "46086", "46047"):
        std = sources.parse_ndbc_txt(_read(f"ndbc_{b}.txt"))
        spec = sources.parse_ndbc_spec(_read(f"ndbc_{b}.spec"))
        S[f"buoy_{b}"] = ok(f"buoy_{b}", sources.buoy_record(std, spec), std["time"])
    for missing in ("enso", "manifest", "tides", "viz_column", "observations", "buoy_46258", "buoy_46253"):
        S[missing] = sources.source(missing, missing, None, ok=False, error="not in fixture", now=NOW)
    return S


def _level(items, item_id, day):
    return next(i for i in items if i["id"] == item_id)["days"][day]["level"]


def test_end_to_end_2026_10_15():
    facts = build_report.assemble(_fixture_sources(), NOW)
    json.dumps(facts, allow_nan=False)
    shore, boat = facts["can_i_go"]["shore"], facts["can_i_go"]["boat"]
    assert facts["date"] == "2026-10-15" and facts["days"][1]["label"] == "Friday"
    # Beach Hazards Statement + high rip risk + 15 s south swell.
    assert _level(shore, "point-loma-shore", "today") == "outside"
    assert _level(shore, "laguna", "today") == "outside"
    assert _level(shore, "la-jolla-cove", "today") == "marginal"  # in the lee of a 200-degree swell
    # Boats: Catalina under a Small Craft Advisory today and a Gale Watch Friday.
    assert _level(boat, "catalina", "today") == "outside"
    assert _level(boat, "catalina", "tomorrow") == "outside"
    assert _level(boat, "inshore-sd", "today") == "within"
    # Friday's NE gusts to 20 kt (Santa Ana pattern) put the Coronados run outside.
    assert _level(boat, "coronados", "tomorrow") == "outside"
    assert len(facts["bottom_line"]) >= 2
    # Missing local inputs degrade to statuses, never to invented values.
    statuses = {s["id"]: s["status"] for s in facts["sources"]}
    assert statuses["manifest"] == "missing" and facts["water"]["sst_field"] is None


def test_every_threshold_level_has_a_label():
    assert set(T.LABELS) == {"within", "marginal", "outside", "unknown"}
