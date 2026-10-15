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

from . import nws_text, physics, render, season, sites, sources
from . import thresholds as T
from . import verdicts as V

TZ = ZoneInfo("America/Los_Angeles")
SCHEMA = "shoudidive.report/v1"
NEARSHORE_BUOYS = {"46254", "46232", "46258", "46225", "46253"}
POST_MAX_DAYS = 3  # dive-shop posts older than this are history, not conditions
SOURCE_KIND = {
    "alerts": "alerts", "cwf_sgx": "cwf", "cwf_lox": "cwf", "off_nhc": "off", "srf_sgx": "srf",
    "rain": "rain", "manifest": "manifest", "observations": "observations", "enso": "enso",
    "tides": "tides", "viz_column": "viz_column",
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
    "Fish: where they are and what the fleet is catching. That section comes in the next phase.",
    "Bait. No public feed measures it.",
    "Current at dive depth. HF radar measures the surface only and is blank within about 1.5 miles of shore.",
    "Beach water-quality notices. The county feed is not connected yet.",
    "An observed thermocline profile. The SCCOOS Del Mar mooring is not connected yet; the depth shown is our heuristic.",
    "Temperature breaks and color edges. They join the report in the next phase.",
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
    return S


def _usable(s: dict | None) -> dict | None:
    """The source's data if it loaded and is fresh enough to act on."""
    if not s or not s.get("ok"):
        return None
    kind = SOURCE_KIND.get(s["id"]) or s["id"].split("_")[0]
    limit = T.MAX_AGE_H.get(kind)
    if limit is not None and s.get("age_h") is not None and s["age_h"] > limit:
        return None
    return s.get("data")


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

    # --- Can I go: shore ---------------------------------------------------
    shore = []
    for site in sites.SHORE_SITES:
        entry = {k: site[k] for k in ("id", "name", "short", "note", "faces_deg", "depth_m")}
        entry["days"] = {}
        for i, key in enumerate(day_keys):
            srcmap = {"alerts": "alerts", "srf": "srf_sgx", "rain": "rain",
                      "marine": (marine.get(site["marine_zone"]) or {}).get("source")}
            d = V.shore_day(site, srf_period=period(surf, site["surf_zone"], i), alerts=alerts_data,
                            marine_period=period(marine, site["marine_zone"], i), rain=rain,
                            window=windows[i], src=srcmap)
            d["label"] = T.LABELS[d["level"]]
            d["why"] = render.why(d)
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
            d["label"] = T.LABELS[d["level"]]
            d["why"] = render.why(d)
            entry["days"][key] = d
        boat.append(entry)

    inshore = next(b for b in boat if b["id"] == "inshore-sd")
    boat_dive = {
        "name": "Boat dive, San Diego inshore",
        "days": {k: {"level": inshore["days"][k]["level"], "label": inshore["days"][k]["label"],
                     "why": "Surface conditions follow San Diego inshore. " + inshore["days"][k]["why"]} for k in day_keys},
        "current": T.CURRENT_TEXT,
    }

    headlines = []
    for a in alerts_data or []:
        headlines.append({"event": a["event"], "area": a.get("area"), "zones": a.get("zones"),
                          "from": clock(sources.parse_iso(a["onset"])) + " " + day_label(sources.parse_iso(a["onset"]).astimezone(TZ)) if a.get("onset") else None,
                          "until": clock(sources.parse_iso(a["ends"])) + " " + day_label(sources.parse_iso(a["ends"]).astimezone(TZ)) if a.get("ends") else None,
                          "headline": a.get("headline")})

    # --- The water -----------------------------------------------------------
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
                    for s in S.values()],
        "method": {"doc": "Thresholds and evidence: ShoudiDive reports research, 2026-10-15.",
                   "tags": {"Rule": "an official product or regulation", "Observed": "a measurement (buoy, gauge, satellite)",
                            "Reported": "a dive shop or angler post", "Modeled": "our calculation, with its method stated"}},
    }
    facts["bottom_line"] = render.bottom_line(facts)

    generated = list(facts["bottom_line"]) + NOT_COVERED + [T.CURRENT_TEXT, T.FREEDIVE_TEXT]
    for group in (shore, boat):
        for e in group:
            for d in e["days"].values():
                generated.append(d["why"])
                generated += [f["rule"] for f in d["factors"]]
    render.check_text(generated)
    return facts


def write(facts: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(facts, indent=1, ensure_ascii=False, allow_nan=False)
    day_file = out_dir / f"{facts['date']}.json"
    day_file.write_text(body, encoding="utf-8")
    (out_dir / "latest.json").write_text(body, encoding="utf-8")
    dates = sorted((p.stem for p in out_dir.glob("????-??-??.json")), reverse=True)
    (out_dir / "index.json").write_text(json.dumps({"dates": dates, "latest": facts["date"]}, indent=1), encoding="utf-8")
    return day_file


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(sources.ROOT / "public" / "data" / "reports"))
    args = ap.parse_args(argv)
    now = sources.utcnow()
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
