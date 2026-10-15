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


# --- Phase 1b: evidence, views, fishing --------------------------------------

from reports import evidence, fish, views  # noqa: E402


def test_srf_afternoon_merged_period_covers_today_and_friday():
    z = nws_text.split_zones(_read("srf_sgx_2026-10-15_pm.txt"))
    periods = nws_text.parse_srf_zone(z["CAZ043"])["periods"]
    assert periods[0]["name"] == "THIS AFTERNOON THROUGH FRIDAY"
    assert nws_text.pick_day_period(periods, 0, 3) is periods[0]
    assert nws_text.pick_day_period(periods, 1, 4) is periods[0]
    assert nws_text.pick_day_period(periods, 2, 5)["name"] == "SATURDAY"


def test_afternoon_cwf_falls_back_to_tonight_for_today_only():
    pl = [{"name": "TONIGHT"}, {"name": "FRI"}, {"name": "FRI NIGHT"}]
    assert nws_text.pick_day_period(pl, 0, 3) == {"name": "TONIGHT", "fallback": True}
    assert nws_text.pick_day_period(pl, 1, 4)["name"] == "FRI"


def test_dap_ascii_parser_reads_scalars_and_arrays():
    text = ("Dataset {\n Float32 metaShoreNormal;\n} x;\n---------------------------------------------\n"
            "metaShoreNormal, 294.54\nmetaWaterDepth, 10.0\nwaveTime[3]\n1791763200, 1791774000, 1791784800\n\n"
            "waveHs[3]\n1.38624, 1.271684, -999.99\n")
    d = evidence.parse_dap_ascii(text)
    assert d["metaShoreNormal"] == [294.54] and d["waveTime"][2] == 1791784800 and d["waveHs"][2] == -999.99


def test_mop_window_uses_rayleigh_set_height():
    mop = {"series": [{"t": "2026-10-15T15:00Z", "hs_m": 0.8, "tp_s": 14.3, "dp_deg": 253.0},
                      {"t": "2026-10-15T18:00Z", "hs_m": 1.0, "tp_s": 15.4, "dp_deg": 254.0}]}
    w = evidence.mop_window(mop, datetime(2026, 10, 15, 12, tzinfo=timezone.utc), datetime(2026, 10, 16, 2, tzinfo=timezone.utc))
    assert w["hs_ft"] == 3.3 and w["sets_ft"] == round(3.28084 * 1.27, 1) and len(w["steps"]) == 2


def test_glider_thermocline_finds_the_sharpest_drop():
    prof = [(d, 22.5) for d in range(0, 30)] + [(d, 22.5 - 0.3 * (d - 30)) for d in range(30, 60)] + [(d, 13.5) for d in range(60, 120)]
    th = sources.thermocline_from_profile(prof)
    assert 28 <= th["thermocline_m"] <= 60 and abs(th["surface_c"] - 22.5) < 0.01
    assert th["t_9m_c"] == 22.5


def test_hunting_vis_thresholds():
    post = {"source": "Shop", "site": "La Jolla", "date_label": "Oct 14", "vis": "0-10 ft"}
    assert views.hunting_vis([post])["level"] == "marginal"
    assert views.hunting_vis([{**post, "vis": "15-25 ft"}])["level"] == "within"
    assert views.hunting_vis([{**post, "vis": "0-2 ft"}])["level"] == "outside"
    assert views.hunting_vis([])["level"] == "unknown"


def test_fleet_now_drops_stale_positions_and_docked_boats():
    now = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)
    data = {"boats": [
        {"name": "FRESH", "lat": 32.40, "lon": -117.30, "sog": 0.5, "t": "2026-10-15T19:30:00Z"},
        {"name": "OLD", "lat": 32.40, "lon": -117.30, "sog": 0.5, "t": "2026-10-14T19:30:00Z"},
        {"name": "DOCKED", "lat": 32.7243, "lon": -117.2268, "sog": 0, "t": "2026-10-15T19:55:00Z"}]}
    out = fish.fleet_now(data, {"H&M Landing": [32.7243, -117.2268, "San Diego"]}, now)
    assert [b["name"] for b in out["at_sea"]] == ["Fresh"]
    assert out["at_sea"][0]["spot"] == "Coronado Islands"


def test_break_step_on_a_synthetic_front():
    import numpy as np
    a = np.full((60, 60), 20.0, dtype="float32")
    a[:, 30:] = 21.0  # a 1 degC step, warm to the east
    g = fish.Grid(a, [-118.0, 32.0, -117.4, 32.6])
    sm, mag, gx, gy = fish.sst_gradient(g)
    lat, lng = g.latlng(30, 28)
    b = fish.nearest_break(g, sm, mag, gx, gy, lat, lng)
    assert b is not None and b["warm_side"] == "E" and b["step_f"] > 1.0


# --- Ocean Report: zones and trends ------------------------------------------

from reports import story, trends, zones  # noqa: E402


def test_ndbc_history_daily_means():
    rows = trends.parse_ndbc_history(
        "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP\n"
        "2026 10 15 16 50 340  5.0  6.0   1.0    14   6.8 234 1016.0  21.0  21.0\n"
        "2026 10 15 16 20 340  7.0  8.0   2.0    MM   6.8 234 1016.0  21.0  22.0\n"
        "2026 10 14 16 20 340  3.0  4.0    MM    MM   6.8 234 1016.0  21.0  20.0\n")
    assert [r["date"] for r in rows] == ["2026-10-14", "2026-10-15"]
    d15 = rows[1]
    assert d15["temp_f"] == round(21.5 * 9 / 5 + 32, 1) and d15["wave_max_ft"] == round(2.0 * 3.28084, 1)
    assert d15["wind_max_kt"] == round(7.0 * 1.94384)


def test_zone_lookup_and_gps_format():
    assert zones.ZONES[zones.zone_of(32.40, -117.26)][0] == "Coronado Islands"
    assert zones.zone_of(30.0, -125.0) is None
    assert zones.ddm(32.5333, -117.35) == "32 32 x 117 21"


def test_archive_replaces_today_and_keeps_order(tmp_path):
    p = tmp_path / "trends.json"
    a = trends.update_archive(p, "2026-10-15", {"zones": {"x": 1}})
    p.write_text(json.dumps(a))
    a = trends.update_archive(p, "2026-10-14", {"zones": {"x": 0}})
    p.write_text(json.dumps(a))
    a = trends.update_archive(p, "2026-10-15", {"zones": {"x": 2}})
    assert [d["date"] for d in a["days"]] == ["2026-10-14", "2026-10-15"] and a["days"][1]["zones"]["x"] == 2


def test_highlights_say_what_moved_without_verdicts():
    card = lambda name, t0, t1: {"name": name, "short": name, "group": "offshore", "temp_f": t1, "temp_change_f": round(t1 - t0, 1),
                                 "temp_series": [{"date": "2026-10-07", "temp_f": t0}, {"date": "2026-10-13", "temp_f": t1}],
                                 "fleet": {"last3_hours": 0, "last_year_hours": 12.0}}
    facts = {"zones": [card("A", 74.0, 72.0), card("B", 73.0, 71.5)], "trends": {}, "weather": {}, "dive_sites": [], "fishing": {}}
    hl = story.highlights(facts)
    assert hl["fishing"][0].startswith("Water cooled in every zone since Oct 7")
    for lines in hl.values():
        render.check_text(lines)
        assert not any("within our thresholds" in x or "outside our thresholds" in x for x in lines)


# --- Predicted visibility -------------------------------------------------------

from reports import visibility  # noqa: E402


def test_vis_calibration_from_seed_removes_the_high_bias():
    cal = visibility.calibration(date(2026, 10, 15), [], [])
    assert cal["applied"] and cal["n"] >= visibility.MIN_PAIRS
    assert 8 <= cal["bias_ft"] <= 11  # the measured Aug-Oct offset
    assert 0.4 <= cal["scale"] <= 0.6 and cal["band_lo_x"] < 1 < cal["band_hi_x"]
    out = visibility.stated({"p50": 20.0, "p10": 16.0, "p90": 25.0}, cal)
    assert out["adjusted"] and out["ft"] == round(20.0 * cal["scale"]) and out["lo_ft"] <= out["ft"] <= out["hi_ft"]
    assert visibility.stated({"p50": 6.0}, cal)["ft"] >= 2  # proportional: murky cells never read zero


def test_vis_archive_pairs_join_on_date():
    days = [{"date": "2026-10-20", "vis_pred": {"la-jolla-ref": {"p50": 18.0}}}]
    posts = [{"date": "2026-10-20", "lo_ft": 5.0, "hi_ft": 10.0}]
    pr = visibility.archive_pairs(days, posts)
    assert pr and pr[0]["p50"] == 18.0 and pr[0]["obs_hi"] == 10.0


def test_vis_not_adjusted_without_enough_pairs(monkeypatch, tmp_path):
    monkeypatch.setattr(visibility, "SEED", tmp_path / "none.json")
    cal = visibility.calibration(date(2026, 10, 15), [], [])
    assert not cal["applied"]
    assert visibility.stated({"p50": 20.0, "p10": 16.0, "p90": 25.0}, cal)["adjusted"] is False


# --- Season against last year --------------------------------------------------

from reports import season_curves  # noqa: E402

STDMET_HIST = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC   mi    ft
2025 10 06 00 00 999 99.0 99.0  1.20 12.50  7.10 270 9999.0 999.0  20.0 999.0 99.0 99.00
2025 10 06 12 00 999 99.0 99.0  1.30 12.50  7.10 270 9999.0 999.0  21.0 999.0 99.0 99.00
2025 10 07 00 00 999 99.0 99.0  1.20 12.50  7.10 270 9999.0 999.0 999.0 999.0 99.0 99.00
2025 10 07 12 00 999 99.0 99.0  1.20 12.50  7.10 270 9999.0 999.0  MM   999.0 99.0 99.00
"""


def test_stdmet_daily_means_skip_missing_codes():
    d = season_curves.parse_stdmet(STDMET_HIST)
    assert d == {"2025-10-06": 20.5}


def test_weekly_needs_enough_days_and_keeps_the_iso_year():
    daily = {f"2025-10-{k:02d}": 20.0 for k in range(6, 13)}       # ISO week 41, Mon-Sun
    daily.update({"2025-10-13": 21.0, "2025-10-14": 21.0})          # week 42, two days only
    w = season_curves.weekly(daily, 2025)
    assert w == {41: 68.0}
    assert season_curves.weekly(daily, 2026) == {}
    assert season_curves.week_monday(2026, 41) == "2026-10-05"


def test_buoy_season_reuses_cached_past_years(monkeypatch):
    calls = []
    monkeypatch.setattr(season_curves, "_year_daily", lambda st, y: calls.append(("y", st, y)) or {})
    monkeypatch.setattr(season_curves, "_this_year_daily", lambda st, y, t: calls.append(("t", st, y)) or {})
    now = datetime(2026, 10, 15, 18, tzinfo=timezone.utc)
    past = {"last": {"41": 68.0}, "range": {"41": [63.0, 70.0, 10]}}
    cache = {"buoys": [{"id": st, "year": 2026, "range_years": [2015, 2024], "past": past,
                        "this_fetched": "2026-10-14T18:00Z", "this_daily": {"2026-10-05": 21.0}} for st, _ in season_curves.TREND_BUOYS]}
    recent = {"46232": [{"date": f"2026-10-{k:02d}", "temp_f": 70.0} for k in range(6, 12)]}
    out = season_curves.buoy_season(date(2026, 10, 15), cache, recent, now=now)
    assert calls == []  # nothing refetched: past is cached, this year is under 3 days old
    b = next(x for x in out["buoys"] if x["id"] == "46232")
    wk41 = next(w for w in b["weeks"] if w["w"] == "2026-10-05")
    assert wk41["last"] == 68.0 and wk41["lo"] == 63.0 and wk41["hi"] == 70.0 and wk41["this"] is not None
    assert out["range_label"] == "2015-2024"


def test_fleet_season_gaps_are_not_zeros():
    stops = [[0, "2025-10-06", 32.66, -117.72, 120, 0, "h", "t1", 0],
             [0, "2025-10-07", 32.66, -117.72, 60, 0, "h", "t2", 0],
             [0, "2025-10-08", 32.74, -117.28, 30, 0, "h", "t3", 1],     # nearshore band: not counted
             [0, "2026-10-05", 32.66, -117.72, 90, 0, "h", "t4", 0]]      # one day heard: week not covered
    fs = season_curves.fleet_season({"stops": stops, "boats": [{}]}, date(2026, 10, 15))
    z = next(r for r in fs["zones"] if r["name"].startswith("9 Mile"))
    assert z["last"][40] == 3.0 and z["this"][40] is None
    assert season_curves.recent_vs_last_year(fs, z["id"], date(2026, 10, 15)) is None


# --- South Coast Divers prose and per-area visibility ------------------------------

from validation.ingest import southcoastdivers as scd  # noqa: E402

SCD_POST = ("Good morning Divers!!! 10/15/2026, Thursday\n\nCurrent diving conditions in Laguna Beach. This morning the "
            "live cams show a decrease in the surf. IMHO, surf is still running 1-4 feet and the viz still looks low at "
            "around 5-7\nfeet. But there are signs it is getting better.\n\nWe need to wait a day to see if the viz gets "
            "back to 10-15 feet. Rich Parker")


def test_scd_reads_the_conditions_paragraph_not_the_plans():
    v = scd.extract_viz(SCD_POST)
    assert (v["lo_ft"], v["hi_ft"]) == (5.0, 7.0) and "5-7" in v["sentence"]


def test_scd_skips_surf_bounds_weekend_and_second_hand():
    def one(sentence):
        return scd.extract_viz("Good morning Divers!!! 10/06/2026, Tuesday\n\nCurrent diving conditions in Laguna Beach. " + sentence)
    v = one("I have to make a good 'Guess-timate' of 2-4+ feet of surf and viz is about 10 feet today.")
    assert (v["lo_ft"], v["hi_ft"]) == (10.0, 10.0)
    assert one("Surf is running 2-5 feet and the viz is less than 10 feet, it changes quickly.") is None
    assert one("Viz is of course at 5 feet & less throughout the area, so stay dry.") is None
    assert one("Over the w/e, surf was 1-3 feet and the viz was 15-20 feet at most of the coves.") is None
    assert one("I read Luis' dive report from his free dive at Crescent Bay, 20+ feet of viz and 1-3 on surf.") is None
    assert one("When surf is low like this, usually the viz is 15+ feet, but not today at all.") is None
    v = one("But the good news is the viz is good 10-15+ feet, could even be better today.")
    assert (v["lo_ft"], v["hi_ft"]) == (10.0, 15.0)


def test_scd_post_time_is_pacific():
    assert scd.post_time_utc("20261015101156-RichP.txt").isoformat() == "2026-10-15T17:11:56+00:00"


def test_vis_areas_fit_separately_and_short_areas_pool(monkeypatch, tmp_path):
    seed = tmp_path / "seed.json"
    pairs = [{"date": f"2026-10-{d:02d}", "source": "dive-shop-justgetwet", "spot": "La Jolla", "obs_lo": 10.0, "obs_hi": 10.0, "p50": 20.0}
             for d in range(1, 16)]
    pairs += [{"date": f"2026-10-{d:02d}", "source": "dive-shop-southcoastdivers", "spot": "Laguna", "obs_lo": 5.0, "obs_hi": 5.0, "p50": 25.0}
              for d in range(1, 6)]
    seed.write_text(json.dumps({"pairs": pairs}), encoding="utf-8")
    monkeypatch.setattr(visibility, "SEED", seed)
    c = visibility.calibrations(date(2026, 10, 15), [], {})
    lj, lag = c["areas"]["la-jolla"], c["areas"]["laguna"]
    assert lj["applied"] and not lj["pooled"] and lj["scale"] == 0.5 and lj["mae_persist_ft"] == 0.0
    assert lag["pooled"] and lag["own_n"] == 5 and lag["scale"] == c["pooled"]["scale"] < 0.5
    assert visibility.area_of(33.54, -117.80)["id"] == "laguna" and visibility.area_of(32.72, -117.25)["id"] == "la-jolla"


def test_vis_field_reports_join_the_nearest_area():
    reps = [{"id": "abcdef12-x", "type": "dive", "date": "2026-10-14", "lat": 32.86, "lng": -117.26, "vis_lo_ft": 12, "vis_hi_ft": 18},
            {"id": "x", "type": "dive", "date": "2026-10-14", "lat": 32.40, "lng": -117.26, "vis_lo_ft": 40, "vis_hi_ft": 50},
            {"id": "y", "type": "catch", "date": "2026-10-14", "lat": 32.86, "lng": -117.26}]
    f = visibility.field_posts(reps)
    assert list(f) == ["la-jolla"] and f["la-jolla"][0]["source"] == "field-report" and f["la-jolla"][0]["hi_ft"] == 18.0
    days = [{"date": "2026-10-14", "vis_pred": {"la-jolla-ref": {"p50": 30.0}}}]
    pr = visibility.archive_pairs(days, f["la-jolla"], visibility.AREAS[0])
    assert pr and pr[0]["source"] == "field-report" and pr[0]["p50"] == 30.0
