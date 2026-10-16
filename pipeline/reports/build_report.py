"""Build the daily report facts file.

    cd pipeline && python -m reports.build_report --out ../public/data/reports

Writes <date>.json (the day's report, permanent), latest.json (a copy) and
index.json (every date we have). gather() is the only part that touches the
network; assemble() is pure and is what the tests exercise on saved products.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import dock_counts, evidence, explain, fish, hot_bites, nws_text, physics, render, season, season_curves, sites, sources, story, trends, views, visibility, zones
from . import thresholds as T
from . import verdicts as V

TZ = ZoneInfo("America/Los_Angeles")
SCHEMA = "shoudidive.report/v2"
NEARSHORE_BUOYS = {"46254", "46232", "46258", "46225", "46253"}
POST_MAX_DAYS = 3  # dive-shop posts older than this are history, not conditions
SOURCE_KIND = {
    "alerts": "alerts", "cwf_sgx": "cwf", "cwf_lox": "cwf", "off_nhc": "off", "srf_sgx": "srf",
    "rain": "rain", "manifest": "manifest", "observations": "observations", "enso": "enso",
    "tides": "tides", "viz_column": "viz_column", "glider": "glider", "fleet_now": "fleet_now",
    "glider_series": "glider_series", "enso_weekly": "enso_weekly", "season_curves": "season_curves",
}
POST_SOURCES = {
    "dive-shop-justgetwet": "Just Get Wet (La Jolla dive shop)",
    "dive-shop-southcoastdivers": "South Coast Divers (Laguna)",
    "dive-shop-diveviz": "DiveViz",
    "dive-shop-beachcitiescuba": "Beach Cities Scuba",
    "forum-bdoutdoors": "BD Outdoors forum",
    "reddit-ca-divers": "Reddit (California divers)",
}
NOT_COVERED = [
    "Fish counts. No licensed source yet, so the fishing view shows where the water matches each species, not how many fish are there.",
    "Bait. No barge publishes a feed; the bait table shows what people on the water send in, and where the fleet stops is the other proxy.",
    "Current at dive depth. HF radar measures the surface only and is blank within about 1.5 miles of shore.",
    "Beach water-quality notices. San Diego County's beach status site has no public data feed; connecting it needs a feed from the county.",
    "The thermocline at the kelp. The observed profile comes from a glider offshore; the SCCOOS Del Mar kelp mooring did not answer when this was built.",
    "Boats without AIS (most under 65 ft), and Mexican waters beyond shore-receiver range.",
]


def clock(dt: datetime) -> str:
    loc = dt.astimezone(TZ)
    h = loc.hour % 12 or 12
    return f"{h}:{loc.minute:02d} {'AM' if loc.hour < 12 else 'PM'}"


def day_label(d: date | datetime) -> str:
    return f"{d:%b} {d.day}"


def gather(now: datetime) -> dict[str, dict]:
    today = now.astimezone(TZ).date()
    S: dict[str, dict] = {}
    S["alerts"] = sources.alerts(sites.ALERT_ZONES, now=now)
    S["cwf_sgx"] = sources.nws_product("CWF", "SGX", now=now)
    S["cwf_lox"] = sources.nws_product("CWF", "LOX", now=now)
    S["srf_sgx"] = sources.nws_product("SRF", "SGX", now=now)
    S["off_nhc"] = sources.nhc_offshore(now=now)
    for b in sites.BUOYS:
        S[f"buoy_{b['id']}"] = sources.buoy(b["id"], b["name"], now=now)
    for c in sites.COOPS_TEMP:
        S[f"coops_{c['id']}"] = sources.coops_water_temp(c["id"], c["name"], now=now)
    g = sites.RAIN_GAUGE
    S["rain"] = sources.rain_72h(g["station"], g["network"], g["name"], today, now=now)
    S["enso"] = sources.enso(now=now)
    S["manifest"] = sources.manifest(now=now)
    S["tides"] = sources.tides(now=now)
    S["viz_column"] = sources.viz_column(now=now)
    S["observations"] = sources.dive_observations(now=now)
    for mop_id in sites.MOP_POINTS:
        S[f"mop_{mop_id}"] = evidence.mop_site(mop_id, now=now)
    S["glider"] = sources.glider_thermocline(now=now)
    S["fleet_now"] = fish.fleet_now_fetch(now=now)
    for bid, name in trends.TREND_BUOYS:
        S[f"hist_{bid}"] = trends.buoy_history(bid, name, now=now)
    S["glider_series"] = trends.glider_series(now=now)
    S["enso_weekly"] = trends.enso_weekly(now=now)
    S["season_curves"] = season_source(today, now, S)
    S["field_reports"] = sources.field_reports(now=now)
    for d in dock_counts.fetch(now=now):
        S[d["id"]] = d
    return S


def season_source(today: date, now: datetime, S: dict) -> dict:
    """Weekly buoy water temperature this year, last year and the ten years
    before; past years come from the cached season.json after the first run."""
    label = "NDBC buoy history: this year, last year and the 10 years before"
    url = "https://www.ndbc.noaa.gov/historical_data.shtml"

    def run():
        try:
            cache = json.loads((sources.DATA / "reports" / "season.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = None
        recent = {bid: ((S.get(f"hist_{bid}") or {}).get("data") or {}).get("days", []) for bid, _ in trends.TREND_BUOYS}
        data = season_curves.buoy_season(today, cache, recent, now=now)
        if not any(w["this"] is not None for b in data["buoys"] for w in b["weeks"]):
            raise RuntimeError("no weekly means this year")
        return sources.source("season_curves", label, url, ok=True, time=now, data=data, now=now)
    return sources._guard("season_curves", label, url, run)


def _usable(s: dict | None) -> dict | None:
    """The source's data if it loaded and is fresh enough to act on."""
    if not s or not s.get("ok"):
        return None
    kind = SOURCE_KIND.get(s["id"]) or s["id"].split("_")[0]
    limit = T.MAX_AGE_H.get(kind)
    if limit is not None and s.get("age_h") is not None and s["age_h"] > limit:
        return None
    return s.get("data")


def _frame_sources(manifest: dict | None, now: datetime) -> list[dict]:
    """Our own hourly forecast frames, listed with their generation time."""
    if not manifest:
        return []
    out = []
    for key, label in (("wind5d", "ShoudiDive hourly wind frames (HRRR/GFS/ECMWF blend)"),
                       ("swell5d", "ShoudiDive hourly wave frames (NOAA WaveWatch III)")):
        t = sources.parse_iso((manifest.get("layers", {}).get(key) or {}).get("generated_at"))
        out.append({"id": "model_frames" if key == "wind5d" else "model_frames_waves", "label": label, "url": "/",
                    "time_utc": sources.iso(t), "age_h": round((now - t).total_seconds() / 3600, 1) if t else None,
                    "error": None, "status": "ok" if t else "missing"})
    return out


def _status(s: dict) -> str:
    if not s.get("ok"):
        return "missing"
    return "ok" if _usable(s) is not None else "stale"


def _window(d: date, now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time(5, 0), TZ).astimezone(timezone.utc)
    end = datetime.combine(d, time(19, 0), TZ).astimezone(timezone.utc)
    return max(start, now) if d == now.astimezone(TZ).date() else start, end


def _zones(S: dict, sid: str, parser) -> dict[str, dict]:
    data = _usable(S.get(sid))
    if not data:
        return {}
    return {z: parser(block) for z, block in nws_text.split_zones(data["text"]).items()}


def assemble(S: dict[str, dict], now: datetime) -> dict:
    local = now.astimezone(TZ)
    today = local.date()
    days = [today, today + timedelta(days=1)]
    day_keys = ["today", "tomorrow"]
    windows = [_window(d, now) for d in days]

    marine = {}
    for sid in ("cwf_sgx", "cwf_lox", "off_nhc"):
        for z, parsed in _zones(S, sid, nws_text.parse_marine_zone).items():
            marine[z] = {**parsed, "source": sid}
    surf = {z: {**p, "source": "srf_sgx"} for z, p in _zones(S, "srf_sgx", nws_text.parse_srf_zone).items()}
    alerts_data = _usable(S.get("alerts"))
    rain = _usable(S.get("rain"))

    def period(zone_map, zone, i):
        z = zone_map.get(zone)
        return nws_text.pick_day_period(z["periods"], i, days[i].weekday()) if z else None

    buoys = []
    for b in sites.BUOYS:
        s = S.get(f"buoy_{b['id']}")
        rec = _usable(s)
        if rec is None or (s and s.get("age_h") is not None and s["age_h"] > T.MAX_AGE_H["buoy"]):
            buoys.append({"id": b["id"], "name": b["name"], "status": "missing" if not (s and s.get("ok")) else "stale"})
            continue
        m2ft = 3.28084
        row = {"id": b["id"], "name": b["name"], "status": "ok", "observed": clock(sources.parse_iso(s["time_utc"])),
               "age_h": s["age_h"], "nearshore": b["id"] in NEARSHORE_BUOYS,
               "wave_ft": round(rec["wvht_m"] * m2ft, 1) if rec.get("wvht_m") is not None else None,
               "temp_f": round(rec["wtmp_c"] * 9 / 5 + 32, 1) if rec.get("wtmp_c") is not None else None}
        if rec.get("swh_m") is not None:
            row["swell"] = {"ft": round(rec["swh_m"] * m2ft, 1), "s": rec.get("swp_s"), "dir": rec.get("swd")}
        if rec.get("wwh_m") is not None:
            row["wind_waves"] = {"ft": round(rec["wwh_m"] * m2ft, 1), "s": rec.get("wwp_s"), "dir": rec.get("wwd")}
        if rec.get("wspd_ms") is not None:
            row["wind"] = {"kt": round(rec["wspd_ms"] * 1.94384), "gust_kt": round(rec["gst_ms"] * 1.94384) if rec.get("gst_ms") else None,
                           "from_deg": rec.get("wdir")}
        buoys.append(row)


    raw_blocks = {}
    for sid in ("cwf_sgx", "cwf_lox", "off_nhc"):
        data = _usable(S.get(sid))
        if data:
            raw_blocks[sid] = nws_text.split_zones(data["text"])
    synopsis = {sid: explain.synopsis_text(raw_blocks.get(sid, {}).get(z)) for sid, z in sites.SYNOPSIS_ZONES.items()}
    man_for_frames = _usable(S.get("manifest"))

    # --- Can I go: shore ---------------------------------------------------
    shore = []
    for site in sites.SHORE_SITES:
        entry = {k: site[k] for k in ("id", "name", "short", "note", "faces_deg", "depth_m")}
        entry["days"] = {}
        for i, key in enumerate(day_keys):
            srcmap = {"alerts": "alerts", "srf": "srf_sgx", "rain": "rain", "mop": f"mop_{site['mop']}",
                      "marine": (marine.get(site["marine_zone"]) or {}).get("source")}
            mop_data = _usable(S.get(f"mop_{site['mop']}"))
            mop_win = evidence.mop_window(mop_data, *windows[i]) if mop_data else None
            srf_p = period(surf, site["surf_zone"], i)
            d = V.shore_day(site, srf_period=srf_p, alerts=alerts_data,  # noqa: E501
                            marine_period=period(marine, site["marine_zone"], i), rain=rain,
                            window=windows[i], src=srcmap, mop=mop_win)
            d["label"] = T.LABELS[d["level"]]
            d["why"] = render.why(d)
            d["period"] = (srf_p or {}).get("name")
            d["period_fallback"] = bool((srf_p or {}).get("fallback"))
            d["evidence"] = explain.shore_blocks(site, d, mop_win=mop_win, srf_period=srf_p, buoy_rows=buoys,
                                                 alerts=alerts_data, window=windows[i], overlaps=V.overlaps, tz=TZ)
            entry["days"][key] = d
        shore.append(entry)

    # --- Can I go: boat ----------------------------------------------------
    boat = []
    for rng in sites.BOAT_RANGES:
        entry = {k: rng.get(k) for k in ("id", "name", "short", "zone", "note")}
        entry["route_zones"] = rng.get("route_zones", [])
        entry["days"] = {}
        for i, key in enumerate(day_keys):
            per_zone = []
            for zcode in [rng["zone"], *rng.get("route_zones", [])]:
                zone = marine.get(zcode)
                srcmap = {"alerts": "alerts", "marine": (zone or {}).get("source")}
                zr = rng if zcode == rng["zone"] else {**rng, "alert_zones": [zcode],
                                                       "office": "SGX" if zcode.startswith("PZZ7") else "LOX"}
                per_zone.append((zcode, V.boat_day(zr, period=period(marine, zcode, i),
                                                   block_headlines=(zone or {}).get("headlines"), alerts=alerts_data,
                                                   window=windows[i], src=srcmap)))
            d = V.merge_route(per_zone)
            own_p = period(marine, rng["zone"], i)
            d["period"] = (own_p or {}).get("name")
            d["period_fallback"] = bool((own_p or {}).get("fallback"))
            d["label"] = T.LABELS[d["level"]]
            d["why"] = render.why(d)
            rows = evidence.hourly_point(*rng["point"], days[i], range(5, 20), man_for_frames) if man_for_frames else []
            office_sid = (marine.get(rng["zone"]) or {}).get("source")
            if office_sid == "off_nhc":  # NHC's synopsis covers all of Mexico; the run is San Diego's
                office_sid = "cwf_sgx"
            d["evidence"] = explain.boat_blocks(rng, d, rows=rows, period=period(marine, rng["zone"], i),
                                                synopsis=synopsis.get(office_sid), buoy_rows=buoys, alerts=alerts_data,
                                                window=windows[i], overlaps=V.overlaps, synopsis_source=office_sid)
            entry["days"][key] = d
        boat.append(entry)

    inshore = next(b for b in boat if b["id"] == "inshore-sd")
    boat_dive = {
        "name": "Boat dive, San Diego inshore",
        "days": {k: {"level": inshore["days"][k]["level"], "label": inshore["days"][k]["label"],
                     "why": "Surface conditions follow San Diego inshore. " + inshore["days"][k]["why"],
                     "evidence": inshore["days"][k].get("evidence", [])} for k in day_keys},
        "current": T.CURRENT_TEXT,
    }

    headlines = []
    for a in alerts_data or []:
        headlines.append({"event": a["event"], "area": a.get("area"), "zones": a.get("zones"),
                          "from": clock(sources.parse_iso(a["onset"])) + " " + day_label(sources.parse_iso(a["onset"]).astimezone(TZ)) if a.get("onset") else None,
                          "until": clock(sources.parse_iso(a["ends"])) + " " + day_label(sources.parse_iso(a["ends"]).astimezone(TZ)) if a.get("ends") else None,
                          "headline": a.get("headline"), "description": a.get("description"),
                          "instruction": a.get("instruction")})

    # --- The water -----------------------------------------------------------
    piers = []
    for c in sites.COOPS_TEMP:
        s = S.get(f"coops_{c['id']}")
        rec = _usable(s)
        if rec and s["age_h"] is not None and s["age_h"] <= T.MAX_AGE_H["coops"]:
            piers.append({"name": c["name"], "temp_f": rec["temp_f"], "observed": clock(sources.parse_iso(s["time_utc"]))})

    surf_days = {}
    for i, key in enumerate(day_keys):
        p = period(surf, "CAZ043", i)
        surf_days[key] = {k: p.get(k) for k in ("rip_risk", "surf_text", "sets_ft", "swell_from_deg", "thunder",
                                                "water_temp_lo_f", "water_temp_hi_f")} if p else None
    surf_info = {**surf_days, "county": "San Diego County", "headlines": (surf.get("CAZ043") or {}).get("headlines", [])}

    marine_text = []
    for zone, name in (("PZZ740", "San Diego coastal waters, to 10 nm"), ("PZZ745", "San Diego waters 10-60 nm, incl. San Clemente Island"),
                       ("PMZ009", "Mexico: border to 30N within 60 nm (Coronados)"), ("PZZ655", "Inner waters incl. Catalina"),
                       ("PZZ676", "Outer waters, Santa Cruz Island to San Clemente Island")):
        z = marine.get(zone)
        if not z:
            marine_text.append({"zone": zone, "name": name, "status": "missing"})
            continue
        src = S[z["source"]]
        p = [period(marine, zone, i) for i in range(2)]
        marine_text.append({"zone": zone, "name": name, "status": "ok", "issued": src["data"].get("issued_line"),
                            "url": src.get("url"), "headlines": z["headlines"],
                            "today": p[0]["text"] if p[0] else None, "tomorrow": p[1]["text"] if p[1] else None})

    man = _usable(S.get("manifest")) or {}
    layers = man.get("layers") or {}
    sst_field = None
    try:
        d_sst = date.fromisoformat(layers["sst"]["windows"]["1d"]["dates"][-1])
        sst_field = {"date": d_sst.isoformat(), "date_label": day_label(d_sst), "age_days": (today - d_sst).days,
                     "source": layers["sst"].get("source")}
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    chl = None
    try:
        w1 = layers["chl"]["windows"]["1d"]
        d_chl = date.fromisoformat(w1["dates"][-1])
        chl = {"date": d_chl.isoformat(), "date_label": day_label(d_chl), "coverage_pct": round(100 * w1.get("coverage_frac", 0))}
    except (KeyError, IndexError, TypeError, ValueError):
        pass

    thermo = []
    vc = _usable(S.get("viz_column")) or {}
    for key in sites.THERMOCLINE_SPOTS:
        sp = (vc.get("spots") or {}).get(key)
        if sp:
            thermo.append({"name": sp.get("name", key), "cliff_ft": None if sp.get("no_cliff") else round(sp["cliff_ft"]),
                           "no_cliff": bool(sp.get("no_cliff")), "bottom_ft": round(sp.get("bottom_ft", 0))})

    tide_events = []
    td = _usable(S.get("tides")) or {}
    st = next((x for x in td.get("stations", []) if x.get("name") == sites.TIDE_STATION), None)
    for e in (st or {}).get("events", []):
        t_ = sources.parse_iso(e["t"])
        if t_ and t_.astimezone(TZ).date() == today:
            tide_events.append({"time": clock(t_), "type": "High" if e["type"] == "H" else "Low",
                                "ft": round(e["v_m"] * 3.28084, 1)})

    rise, set_ = physics.sun_times(today, *sites.SUN_LATLNG)
    moon = physics.moon(datetime.combine(today, time(12), TZ).astimezone(timezone.utc))
    enso = _usable(S.get("enso"))

    water = {
        "buoys": buoys, "piers": piers, "surf": surf_info, "marine_text": marine_text,
        "sst_field": sst_field, "chl": chl,
        "thermocline": {"spots": thermo, "tag": "Modeled",
                        "method": "ShoudiDive water-column heuristic (seasonal mixed layer shoaled by upwelling); "
                                  "not yet checked against an observed profile."},
        "tide": {"station": "La Jolla", "events": tide_events},
        "sun": {"rise": clock(rise) if rise else None, "set": clock(set_) if set_ else None},
        "moon": {"phase": moon["phase"], "illuminated_pct": round(moon["illuminated"] * 100)},
        "enso": {"status": enso.get("status"), "nino34_c": enso.get("nino34_c"), "synopsis": enso.get("synopsis"),
                 "issued": (S["enso"].get("time_utc") or "")[:10] or None} if enso else None,
    }

    # --- Divers' corner ------------------------------------------------------
    obs = _usable(S.get("observations")) or {}
    posts, seen_sites = [], set()
    for p in obs.get("posts", []):
        if p["stale_source"] or not (p.get("vis_text") or p.get("vis_ft") is not None):
            continue
        d_post = date.fromisoformat(p["post_date"])
        if (today - d_post).days > POST_MAX_DAYS or (p["source"], p["site"]) in seen_sites:
            continue
        seen_sites.add((p["source"], p["site"]))
        posts.append({"source": POST_SOURCES.get(p["source"], p["source"]), "site": p["site"],
                      "date": p["post_date"], "date_label": day_label(d_post), "days_ago": (today - d_post).days,
                      "vis": p["vis_text"] or f"{p['vis_ft']:.0f} ft", "url": p["url"], "excerpt": p["excerpt"]})
    posts = posts[:6]
    stale = [POST_SOURCES.get(s_, s_) for s_ in obs.get("stale_sources", [])]

    surge = []
    for ss in sites.SURGE_SITES:
        s = S.get(f"buoy_{ss['buoy']}")
        rec = _usable(s)
        if not rec or rec.get("swh_m") is None:
            continue
        trains = [(rec["swh_m"], rec["swp_s"])]
        if rec.get("wwh_m") and rec.get("wwp_s"):
            trains.append((rec["wwh_m"], rec["wwp_s"]))
        u = physics.combined_bottom_velocity([t for t in trains if t[0] and t[1]], ss["depth_m"])
        level = "strong" if u > 0.2 else "moderate" if u >= 0.1 else "light"
        surge.append({"site": ss["name"], "depth_ft": round(ss["depth_m"] * 3.28084), "buoy": ss["buoy"],
                      "u_m_s": round(u, 2), "u_ft_s": round(u * 3.28084, 1), "level": level})

    divers = {
        "posts": posts, "stale_sources": stale,
        "surge": {"sites": surge, "tag": "Modeled",
                  "method": "Linear wave theory on the nearest buoy's swell and wind waves; peak near-bottom water speed. "
                            f"Fine sand starts moving at about {physics.SAND_MOTION_M_S} m/s (0.5 ft/s)."},
        "rain": {"inches": rain["inches"], "gauge": sites.RAIN_GAUGE["name"],
                 "advisory": rain["inches"] >= T.RAIN_ADVISORY_IN} if rain else None,
        "water_quality": {"status": "not_connected", "url": "https://www.sdbeachinfo.com",
                          "text": "County beach water-quality notices are not connected yet. Check the county site before "
                                  "entering, especially near river and lagoon mouths and from Imperial Beach to the border."},
        "season": season.season_items(today),
        "freedive": T.FREEDIVE_TEXT,
    }

    facts = {
        "schema": SCHEMA,
        "date": today.isoformat(),
        "date_label": f"{local:%A}, {local:%B} {local.day}, {local.year}",
        "generated_utc": sources.iso(now),
        "generated_local": f"{clock(now)} {local.tzname()}",
        "days": [{"key": k, "date": d.isoformat(), "label": "Today" if k == "today" else f"{d:%A}"} for k, d in zip(day_keys, days)],
        "can_i_go": {"shore": shore, "boat": boat, "boat_dive": boat_dive, "freedive": T.FREEDIVE_TEXT,
                     "headlines": headlines, "levels": T.LABELS},
        "water": water,
        "divers": divers,
        "not_covered": NOT_COVERED,
        "sources": [{k: s.get(k) for k in ("id", "label", "url", "time_utc", "age_h", "error")} | {"status": _status(s)}
                    for s in S.values()] + _frame_sources(man_for_frames, now),
        "method": {"doc": "Thresholds and evidence: ShoudiDive reports research, 2026-10-15.",
                   "tags": {"Rule": "an official product or regulation", "Observed": "a measurement (buoy, gauge, satellite)",
                            "Reported": "a dive shop or angler post", "Modeled": "our calculation, with its method stated"}},
    }
    # --- Observed thermocline (nearest Spray glider) ---------------------------
    glider = _usable(S.get("glider"))
    thermo_ft = None
    if glider:
        def gf(c):
            return round(c * 9 / 5 + 32, 1) if c is not None else None
        g_time = sources.parse_iso(S["glider"]["time_utc"])
        thermo_ft = round(glider["thermocline_m"] * 3.28084)
        water["thermocline"]["observed"] = {
            "glider": glider["glider"], "lat": glider["lat"], "lng": glider["lng"],
            "nm_from_sd": round(glider["km_from_san_diego"] / 1.852),
            "time": f"{clock(g_time)} {day_label(g_time.astimezone(TZ))}", "surface_f": gf(glider["surface_c"]),
            "thermocline_ft": thermo_ft, "t30_f": gf(glider["t_9m_c"]), "t60_f": gf(glider["t_18m_c"]),
            "t100_f": gf(glider["t_30m_c"]),
            "drop_f_per_10ft": round(glider["max_drop_c_per_m"] * 9 / 5 * 3.048, 1),
            "url": S["glider"].get("url"), "tag": "Observed",
            "note": "A glider profile offshore, not at the kelp: internal tides move the thermocline 15-65 ft within hours at San Diego kelp moorings."}

    # --- Fishing ---------------------------------------------------------------
    near = [b["temp_f"] for b in buoys if b.get("status") == "ok" and b.get("nearshore") and b.get("temp_f") is not None]
    surface_f = sum(near) / len(near) if near else None
    man = _usable(S.get("manifest")) or {}
    paddies = fish.load_json(sources.DATA.parent / "paddies" / "data.json")
    fleet = fish.load_json(sources.DATA.parent / "fleet" / "data.json")
    sst_g = fish.load_grid("sst", man) if man else None
    chl_g = fish.load_grid("chl", man) if man else None
    spot_rows = fish.spot_conditions(sst_g, chl_g, paddies, fish.paddy_offset(paddies, today))
    facts["fishing"] = {
        "spots": spot_rows,
        "species": fish.species_picture(today.month, spot_rows) if sst_g else [],
        "fleet_recent": fish.fleet_recent(fleet, today),
        "fleet_now": fish.fleet_now(_usable(S.get("fleet_now")), (fleet or {}).get("landings"), now),
        "inshore": fish.inshore_calendar(today, surface_f, thermo_ft),
        "sst_field": sst_field, "chl": chl,
        "method": ("Profiles say where the water matches what each species needs, not how many fish are there. "
                   "Water temperature is the satellite map's; breaks are the strongest gradient within 10 nm of a spot, "
                   "read as the temperature change across 4.3 nm. A cue met at most in-range spots that day is "
                   "'everywhere' and singles nothing out."),
        "counts_note": ("No dock counts: the network that publishes SoCal landing counts now forbids automated "
                        "collection and republishing without a license."),
    }

    # --- Divers and spearfishers -------------------------------------------------
    facts["divers"]["suit"] = views.suit_guide(surface_f, water["thermocline"].get("observed"))
    facts["divers"]["best_bet"] = views.best_bet(shore, "today")
    facts["divers"]["hunting_vis"] = views.hunting_vis(facts["divers"]["posts"])
    facts["bottom_line"] = render.bottom_line(facts)
    facts["views"] = views.view_lines(facts)

    # --- Zones and trends: what is going on, and how it moved ------------------
    sst_hist = trends.sst_history()
    cards = zones.build(today, sst_hist, chl_g, paddies, fish.paddy_offset(paddies, today), fleet, man,
                        [today + timedelta(days=k) for k in range(3)])
    buoy_hist = {bid: _usable(S.get(f"hist_{bid}")) for bid, _ in trends.TREND_BUOYS}
    vis = trends.vis_series(today=today)
    climo, climo_meta = season_curves.load_climo(man) if man else (None, None)
    season_curves.zone_vs_normal(cards, climo, climo_meta)
    sea = _usable(S.get("season_curves"))
    fs = season_curves.fleet_season(fleet, today)
    for c in cards:
        c.setdefault("fleet", {})["season"] = season_curves.recent_vs_last_year(fs, c["id"], today)
    facts["season_curves"] = {
        "year": today.year, "last_year": today.year - 1, "range_label": (sea or {}).get("range_label"),
        "buoys": [{"id": b["id"], "name": b["name"], "weeks": b["weeks"]} for b in (sea or {}).get("buoys", [])],
        "fleet": {k: fs.get(k) for k in ("weeks", "groups", "zones", "gaps", "boats")} if fs else None,
    }
    if sea:
        facts["season_cache"] = sea
    facts["zones"] = cards
    facts["zone_groups"] = [{"key": k, "label": label} for k, label in zones.GROUPS]
    facts["trends"] = {
        "buoys": [{"id": bid, "name": name, "days": (buoy_hist[bid] or {}).get("days", [])[-45:]}
                  for bid, name in trends.TREND_BUOYS if buoy_hist.get(bid)],
        "glider": _usable(S.get("glider_series")) or [],
        "vis": vis,
        "enso": _usable(S.get("enso_weekly")) or [],
    }
    # Predicted visibility, less the model's measured recent bias
    vis_grids = visibility.load_grids(man) if man else None
    try:
        arch_days = json.loads((sources.DATA / "reports" / "trends.json").read_text(encoding="utf-8")).get("days", [])
    except (OSError, ValueError):
        arch_days = []
    field = visibility.field_posts(_usable(S.get("field_reports")) or [])
    cals = visibility.calibrations(today, arch_days, {a["id"]: vis.get(a["source"], []) for a in visibility.AREAS}, field)
    cal = cals["areas"]["la-jolla"]
    site_vis = {}
    if vis_grids:
        for site in sites.SHORE_SITES:
            area = visibility.area_of(site["lat"], site["lng"])
            site_vis[site["id"]] = visibility.stated(visibility.sample(vis_grids, site["lat"], site["lng"]), visibility.cal_at(site["lat"], site["lng"], cals["areas"]))
            if site_vis[site["id"]]:
                site_vis[site["id"]]["area"] = area["id"]
        for c in cards:
            if c["group"] == "inshore" or c["name"] == "Coronado Islands":
                m, _ = zones.zone_mean(vis_grids["p50"], tuple(c["center"]), c["radius_nm"])
                c["vis_pred"] = visibility.stated({"p50": m} if m is not None else None, visibility.cal_at(c["center"][0], c["center"][1], cals["areas"]))
    refs = {a["ref"]["id"]: visibility.sample(vis_grids, a["ref"]["lat"], a["ref"]["lng"]) for a in visibility.AREAS} if vis_grids else {}
    refs = {k: v for k, v in refs.items() if v}
    facts["visibility"] = {"calibration": cal, "areas": cals["areas"], "field_reports_used": sum(len(v) for v in field.values()),
                           "sites": site_vis, "valid_at": (vis_grids or {}).get("valid_at"),
                           "url": ((man.get("layers") or {}).get("viz") or {}).get("windows", {}).get("now", {}).get("url"),
                           "range_ft": ((man.get("layers") or {}).get("viz") or {}).get("range_ft")}
    facts["weather"] = story.weather(synopsis, headlines, cards, buoys)
    facts["dive_sites"] = story.dive_sites(S, sites.SHORE_SITES, TZ, today, vis, buoy_hist,
                                           facts["divers"]["surge"]["sites"])
    for ds, site in zip(facts["dive_sites"], sites.SHORE_SITES):
        ds["vis_pred"] = site_vis.get(site["id"])
    facts["bait"] = {"text": "The barges also post their own updates:",
                     "links": [{"name": "Everingham Bros bait report (San Diego Bay, Mission Bay, Dana Point)",
                                "url": "https://baitbarge.com/bait-report"}]}
    facts["map"] = zones.map_layers(paddies, fish.paddy_offset(paddies, today), fleet, today, man)
    dock = [x for x in (_usable(S.get(L["id"])) for L in dock_counts.LANDINGS) if x]
    hb = hot_bites.fleet_hot_bites(fleet, today, dock_counts.trips_since(dock, today - timedelta(days=hot_bites.DAYS - 1)))
    facts["hot_bites"] = {"days": hot_bites.DAYS, "fleet": hb["fleet"], "landed": hb["landed"],
                          "dock_week": hot_bites.dock_week(dock, today),
                          "dock_sources": [{"landing": d["landing"], "port": d["port"], "url": d["url"]} for d in dock]}
    facts["highlights"] = story.highlights(facts)
    facts["archive_snapshot"] = zones.snapshot(cards)
    if refs:
        facts["archive_snapshot"]["vis_pred"] = refs

    generated = list(facts["bottom_line"]) + NOT_COVERED + [T.CURRENT_TEXT, T.FREEDIVE_TEXT]
    for lines in facts["views"].values():
        generated += lines
    for lines in facts["highlights"].values():
        generated += lines
    generated += [facts["divers"]["suit"]["text"], facts["divers"]["hunting_vis"]["text"]]
    if facts["divers"]["best_bet"]:
        generated.append(facts["divers"]["best_bet"]["text"])
    for group in (shore, boat):
        for e in group:
            for d in e["days"].values():
                generated.append(d["why"])
                generated += [f["rule"] for f in d["factors"]]
    render.check_text(generated)
    return facts


def write(facts: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    snap = facts.pop("archive_snapshot", None)
    sea = facts.pop("season_cache", None)
    if sea is not None:
        (out_dir / "season.json").write_text(json.dumps(sea, separators=(",", ":")), encoding="utf-8")
    if snap is not None:
        arch = trends.update_archive(out_dir / "trends.json", facts["date"], snap)
        (out_dir / "trends.json").write_text(json.dumps(arch, separators=(",", ":")), encoding="utf-8")
        facts.setdefault("trends", {})["archive_days"] = len(arch["days"])
    body = json.dumps(facts, indent=1, ensure_ascii=False, allow_nan=False)
    day_file = out_dir / f"{facts['date']}.json"
    day_file.write_text(body, encoding="utf-8")
    (out_dir / "latest.json").write_text(body, encoding="utf-8")
    dates = sorted((p.stem for p in out_dir.glob("????-??-??.json")), reverse=True)
    (out_dir / "index.json").write_text(json.dumps({"dates": dates, "latest": facts["date"]}, indent=1), encoding="utf-8")
    return day_file


def due(out_dir: Path, now: datetime, min_interval_min: int, active_hours: tuple[int, int]) -> tuple[bool, str]:
    """Rebuild only inside the active local hours, and only when the last
    build is older than the interval or from an earlier day."""
    local = now.astimezone(TZ)
    if not active_hours[0] <= local.hour < active_hours[1]:
        return False, f"outside active hours ({local:%H:%M} local)"
    try:
        last = json.loads((out_dir / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True, "no previous report"
    if last.get("date") != local.date().isoformat():
        return True, "first build of the day"
    age_min = (now - sources.parse_iso(last.get("generated_utc"))).total_seconds() / 60
    if age_min >= min_interval_min:
        return True, f"last build {age_min:.0f} min ago"
    return False, f"last build only {age_min:.0f} min ago"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(sources.ROOT / "public" / "data" / "reports"))
    ap.add_argument("--min-interval-min", type=int, default=0,
                    help="skip when the last build is newer than this (0 = always build)")
    ap.add_argument("--active-hours", default="0-24", help="local hours to build in, e.g. 4-21")
    args = ap.parse_args(argv)
    now = sources.utcnow()
    if args.min_interval_min:
        h0, h1 = (int(x) for x in args.active_hours.split("-"))
        ok, reason = due(Path(args.out), now, args.min_interval_min, (h0, h1))
        print(("building: " if ok else "skipping: ") + reason)
        if not ok:
            return 0
    S = gather(now)
    facts = assemble(S, now)
    path = write(facts, Path(args.out))
    bad = [s for s in facts["sources"] if s["status"] != "ok"]
    print(f"wrote {path} ({len(facts['sources']) - len(bad)}/{len(facts['sources'])} sources ok)")
    for s in bad:
        print(f"  {s['status']}: {s['id']} {s.get('error') or ''}")
    for line in facts["bottom_line"]:
        print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
