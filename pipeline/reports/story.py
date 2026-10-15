"""What's going on: highlights, weather, dive sites. Plain statements of what
the data shows and how it moved; no ratings, no go/no-go. Every number comes
from the facts already assembled (zones, trends, sources)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import sources
from .render import join

# Which shop's visibility posts speak for a site's waters.
SITE_VIS_SOURCE = {"la-jolla-cove": "dive-shop-justgetwet", "la-jolla-shores": "dive-shop-justgetwet",
                   "laguna": "dive-shop-southcoastdivers"}
SHOP_NAMES = {"dive-shop-justgetwet": "Just Get Wet (La Jolla)", "dive-shop-southcoastdivers": "South Coast Divers (Laguna)",
              "dive-shop-diveviz": "DiveViz", "dive-shop-beachcitiescuba": "Beach Cities Scuba"}
ZONE_SHORT = {"PZZ740": "San Diego coastal waters", "PZZ745": "San Diego offshore waters",
              "PZZ655": "LA inner waters and Catalina", "PZZ650": "East Santa Barbara Channel",
              "PZZ676": "outer waters to San Clemente Island", "PZZ673": "western Santa Barbara Channel",
              "CAZ043": "San Diego County beaches", "CAZ552": "Orange County beaches"}

OUTLOOK_ZONES = ["La Jolla / Point Loma / Imperial Beach", "Coronado Islands", "9 Mile Bank / 178 / 182 / 43",
                 "South 9 / 226-302 / 425 / 371 / Upper Hidden", "Catalina Channel / 14 Mile / Avalon Bank",
                 "San Clemente Island", "Tanner / Cortes / Cherry"]


def _d(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}"


def weather(synopsis: dict, headlines: list[dict], cards: list[dict], buoys: list[dict]) -> dict:
    by_name = {c["name"]: c for c in cards}
    grid = []
    for name in OUTLOOK_ZONES:
        c = by_name.get(name)
        if c and c.get("outlook"):
            grid.append({"zone": c.get("short", name), "days": c["outlook"]})
    winds = [{"name": b["name"], "kt": b["wind"]["kt"], "gust_kt": b["wind"].get("gust_kt"),
              "observed": b["observed"]} for b in buoys if b.get("status") == "ok" and b.get("wind")]
    for h in headlines:
        h["where"] = join([ZONE_SHORT[z] for z in h.get("zones") or [] if z in ZONE_SHORT]) or h.get("area")
    return {"synopsis": [{"office": "San Diego (NWS)", "text": synopsis.get("cwf_sgx")},
                         {"office": "Los Angeles / Channel Islands (NWS)", "text": synopsis.get("cwf_lox")}],
            "headlines": headlines, "outlook": grid, "wind_now": winds}


def _daily_mop(series: list[dict], tz, days: list[date]) -> list[dict]:
    out = []
    for d in days:
        pts = [p for p in series if sources.parse_iso(p["t"]).astimezone(tz).date() == d
               and 5 <= sources.parse_iso(p["t"]).astimezone(tz).hour <= 19]
        if pts:
            big = max(pts, key=lambda p: p["hs_m"])
            hs = big["hs_m"] * 3.28084
            out.append({"date": d.isoformat(), "label": f"{d:%a}", "hs_ft": round(hs, 1), "sets_ft": round(hs * 1.27, 1),
                        "tp_s": round(big["tp_s"]), "dp_deg": round(big["dp_deg"])})
    return out


def dive_sites(S: dict, shore_sites: list[dict], tz, today: date, vis: dict, buoy_hist: dict, surge: list[dict]) -> list[dict]:
    days = [today + timedelta(days=k) for k in range(4)]
    out = []
    for site in shore_sites:
        mop = (S.get(f"mop_{site['mop']}") or {}).get("data") or {}
        vis_key = SITE_VIS_SOURCE.get(site["id"])
        vis_key = vis_key if vis_key in vis else None
        hist = (buoy_hist.get(site["buoy"]) or {}).get("days", [])
        sg = next((x for x in surge if x["site"].split(" ")[0] == site["short"].split(" ")[0]), None)
        out.append({
            "name": site["name"], "short": site["short"], "note": site["note"], "buoy": site["buoy"],
            "lat": site["lat"], "lng": site["lng"],
            "surf": _daily_mop(mop.get("series", []), tz, days),
            "vis": vis.get(vis_key, [])[-14:] if vis_key else [],
            "vis_source": SHOP_NAMES.get(vis_key, vis_key) if vis_key else None,
            "temp_series": [{"date": r["date"], "temp_f": r.get("temp_f")} for r in hist[-21:] if r.get("temp_f") is not None],
            "surge": sg,
        })
    return out


def _where(h: dict) -> str:
    names = [ZONE_SHORT[z] for z in h.get("zones") or [] if z in ZONE_SHORT]
    return f" ({join(names)})" if names else ""


def _rng(p: dict) -> str:
    return f"{p['lo_ft']:.0f} ft" if p["lo_ft"] == p["hi_ft"] else f"{p['lo_ft']:.0f}-{p['hi_ft']:.0f} ft"


def _ft(x: float) -> str:
    return "under 1 ft" if x < 1 else f"{x:.0f} ft"


def _fmt_change(x: float) -> str:
    return f"{'+' if x > 0 else ''}{x:.1f}°F"


def highlights(facts: dict) -> dict[str, list[str]]:
    cards = facts.get("zones", [])
    tr = facts.get("trends", {})
    out_fish, out_dive, out_spear = [], [], []

    # Water temperature over the week, across zones
    ch = [(c["short"], c["temp_change_f"], c["temp_f"]) for c in cards if c.get("temp_change_f") is not None]
    if ch:
        first = cards[0]["temp_series"][0]["date"] if cards[0].get("temp_series") else None
        cooled = [x for x in ch if x[1] <= -0.5]
        warmed = [x for x in ch if x[1] >= 0.5]
        lo = min(x[1] for x in ch)
        hi = max(x[1] for x in ch)
        if len(cooled) == len(ch):
            line = f"Water cooled in every zone since {_d(first)}: {abs(hi):.1f}-{abs(lo):.1f}°F down on the satellite map."
        elif len(warmed) == len(ch):
            line = f"Water warmed in every zone since {_d(first)}: {lo:.1f}-{hi:.1f}°F up on the satellite map."
        else:
            line = (f"Since {_d(first)}, water warmed in {len(warmed)} zones and cooled in {len(cooled)}; "
                    f"the biggest move was {max(ch, key=lambda x: abs(x[1]))[0]} ({_fmt_change(max(ch, key=lambda x: abs(x[1]))[1])}).")
        warm = max(ch, key=lambda x: x[2])
        cool = min(ch, key=lambda x: x[2])
        line += f" Warmest: {warm[0]} at {warm[2]:.1f}°F; coolest: {cool[0]} at {cool[2]:.1f}°F."
        out_fish.append(line)

    # Breaks
    br = [c for c in cards if c.get("break") and c["group"] != "inshore"]
    if br:
        top = max(br, key=lambda c: c["break"]["step_f"])
        b = top["break"]
        line = f"Strongest offshore break: {top['short']}, {b['step_f']}°F over {b['over_nm']} nm at {b['gps']}, warm side {b['warm_side']}."
        s = [x["c_per_km"] for x in top.get("break_series", []) if x.get("c_per_km") is not None]
        if len(s) >= 3:
            line += " It has " + ("sharpened" if s[-1] > s[-3] * 1.15 else "softened" if s[-1] < s[-3] * 0.85 else "held steady") + " over the last three map days."
        out_fish.append(line)

    # Paddies
    core = [c["short"] for c in cards if (c.get("paddy") or {}).get("word") == "core"]
    if core:
        out_fish.append("The paddy drift model's core regions sit in " + join(core) + ".")

    # Fleet: now vs last year
    fz = [(c["short"], c["fleet"]) for c in cards if c.get("fleet")]
    act = sorted([x for x in fz if x[1]["last3_hours"] > 0], key=lambda x: -x[1]["last3_hours"])
    ly = sorted([x for x in fz if x[1]["last_year_hours"] > 0], key=lambda x: -x[1]["last_year_hours"])
    fl = ""
    if act:
        fl = f"AIS-tracked boats stopped longest in {act[0][0]} over the last three days ({act[0][1]['last3_hours']} h)."
    else:
        fl = "AIS heard no offshore stops in the last three days (most boats under 65 ft never broadcast)."
    if ly:
        fl += " The same weeks last year, the fleet's stops concentrated at " + join([f"{n} ({v['last_year_hours']:.0f} h)" for n, v in ly[:3]]) + "."
    out_fish.append(fl)

    # Weather headline
    heads = [h for h in facts.get("weather", {}).get("headlines", []) if h.get("event")]
    marine = [h for h in heads if any(w in h["event"] for w in ("Craft", "Gale", "Storm", "Fog", "Marine", "Hazardous Seas"))]
    if marine:
        out_fish.append("NWS: " + "; ".join(h["event"] + _where(h) + (f" until {h['until']}" if h.get("until") else "")
                                            for h in marine[:3]) + ".")
    peak = None
    for row in facts.get("weather", {}).get("outlook", []):
        for d in row["days"]:
            if d.get("wind_max_kt") is not None and (peak is None or d["wind_max_kt"] > peak[1]["wind_max_kt"]):
                peak = (row["zone"], d)
    if peak:
        out_fish.append(f"Our model's windiest daytime spot in the next days: {peak[0]}, {peak[1]['wind_dir']} {peak[1]['wind_max_kt']} kt on {peak[1]['label']}.")

    # El Nino
    en = tr.get("enso") or []
    if len(en) >= 5:
        out_fish.append(f"El Niño: weekly Niño-3.4 anomaly {en[-1]['nino34']:+.1f}°C, from {en[-5]['nino34']:+.1f}°C four weeks earlier.")

    # Divers: predicted visibility first, then the posts, water, surf, column
    sites = facts.get("dive_sites", [])
    cal = (facts.get("visibility") or {}).get("calibration") or {}
    preds = [(s["short"], s["vis_pred"]) for s in sites if s.get("vis_pred")]
    if preds:
        line = "Predicted visibility today: " + "; ".join(
            f"{n} about {p['ft']} ft" + (f" ({p['lo_ft']}-{p['hi_ft']})" if p.get("lo_ft") is not None else "") for n, p in preds) + "."
        if cal.get("applied"):
            line += (f" That is our model's number scaled by {cal['scale']:.2f}, the ratio {cal['n']} La Jolla shop reports "
                     f"have landed at since {_d(cal['since'])}.")
        out_dive.append(line)
    for s in sites:
        v = s.get("vis") or []
        if len(v) >= 2:
            last = v[-1]
            week = [p for p in v if (date.fromisoformat(last["date"]) - date.fromisoformat(p["date"])).days >= 6]
            prev = week[-1] if week else v[0]
            out_dive.append(f"{s['vis_source']} visibility posts: {_rng(last)} on {_d(last['date'])}, "
                            f"against {_rng(prev)} on {_d(prev['date'])}.")
            break
    near = next((b for b in tr.get("buoys", []) if b["id"] == "46254"), None)
    if near and len(near["days"]) >= 8:
        ds = [d for d in near["days"] if d.get("temp_f") is not None]
        if len(ds) >= 8:
            out_dive.append(f"Water at the La Jolla nearshore buoy: {ds[-1]['temp_f']}°F, {_fmt_change(ds[-1]['temp_f'] - ds[-8]['temp_f'])} over a week "
                            f"and {_fmt_change(ds[-1]['temp_f'] - ds[0]['temp_f'])} since {_d(ds[0]['date'])}.")
    surf_line = []
    for s in sites:
        if s.get("surf"):
            seq = ", ".join(d["label"] + " " + _ft(d["sets_ft"]) for d in s["surf"][:3])
            surf_line.append(f"{s['short']} {seq}")
    if surf_line:
        out_dive.append("Set height at the entries (CDIP model): " + "; ".join(surf_line[:3]) + ".")
    gs = [g for g in (tr.get("glider") or []) if g.get("mixed_layer_ft") is not None]
    if len(gs) >= 2:
        g0, g1 = gs[0], gs[-1]
        out_dive.append(f"Offshore water column (Spray gliders, nearest each day): the warm top layer reached {g1['mixed_layer_ft']} ft "
                        f"on {_d(g1['date'])}, against {g0['mixed_layer_ft']} ft on {_d(g0['date'])}; {g1['t60_f']}°F at 60 ft.")
    out_dive += [x for x in out_fish if x.startswith("NWS:")]

    # Spearfishing: visibility, water column, what's around
    out_spear = [x for x in out_dive if not x.startswith("Set height")]
    inshore = (facts.get("fishing") or {}).get("inshore", [])
    wsb = next((r for r in inshore if r["key"] == "wsb"), None)
    yt = next((r for r in inshore if r["key"] == "yellowtail"), None)
    for r in (wsb, yt):
        if r and r.get("today"):
            out_spear.append(f"{r['label']}: {r['today']}")
    return {"fishing": out_fish, "diving": out_dive, "spearfishing": out_spear}


def generated_at_label(now: datetime, tz) -> str:
    loc = now.astimezone(tz)
    return f"{loc:%b} {loc.day}, {loc.hour % 12 or 12}:{loc.minute:02d} {'AM' if loc.hour < 12 else 'PM'} {loc.tzname()}"
