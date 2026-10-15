"""Threshold tables -> verdicts, one factor at a time.

Every factor records its level, the value it saw, the rule it applied, the
source it came from, and an evidence tag (Rule / Observed / Modeled). A
verdict is the worst factor; a required factor with no data makes it
"unknown" (shown as "Not enough data") unless another factor is already
outside. Nothing here reads the network: inputs arrive parsed.
"""
from __future__ import annotations

from datetime import datetime

from . import nws_text
from . import thresholds as T


def factor(key: str, label: str, level: str, value: str, rule: str, source: str | None,
           tag: str = "Rule", required: bool = True) -> dict:
    return {"key": key, "label": label, "level": level, "value": value, "rule": rule,
            "source": source, "tag": tag, "required": required}


def angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def exposure(faces_deg: float, swell_from_deg: float | None) -> str | None:
    if swell_from_deg is None:
        return None
    d = angle_diff(faces_deg, swell_from_deg)
    if d <= T.EXPOSURE_INTO_DEG:
        return "into"
    if d <= T.EXPOSURE_OBLIQUE_DEG:
        return "oblique"
    return "lee"


EXPOSURE_WORDS = {"into": "faces the swell", "oblique": "takes the swell at an angle", "lee": "in the swell's lee"}


def overlaps(alert: dict, start: datetime, end: datetime) -> bool:
    from .sources import parse_iso
    on = parse_iso(alert.get("onset"))
    ends = parse_iso(alert.get("ends"))
    if on and on >= end:
        return False
    if ends and ends <= start:
        return False
    return True


def _alerts_for(alerts: list[dict], zones: list[str], start: datetime, end: datetime) -> list[dict]:
    return [a for a in alerts if set(a.get("zones") or []) & set(zones) and overlaps(a, start, end)]


# --- Shore dive --------------------------------------------------------------

def shore_day(site: dict, *, srf_period: dict | None, alerts: list[dict] | None,
              marine_period: dict | None, rain: dict | None, window: tuple[datetime, datetime],
              src: dict[str, str]) -> dict:
    factors = []
    swell_from = srf_period.get("swell_from_deg") if srf_period else None
    if swell_from is None and marine_period and marine_period.get("trains"):
        big = max(marine_period["trains"], key=lambda t: t["ft"])
        swell_from = nws_text.COMPASS_DEG.get(big["dir"])
    exp = exposure(site["faces_deg"], swell_from)

    # 1. NWS beach headlines
    if alerts is None:
        factors.append(factor("headlines", "NWS beach headlines", "unknown", "alerts feed unavailable",
                              "Any High Surf Advisory or Warning is outside; a Beach Hazards or Rip Current Statement is outside at a site facing the swell, marginal in its lee.",
                              src.get("alerts")))
    else:
        hits = _alerts_for(alerts, [site["land_zone"]], *window)
        lv, names = "within", []
        for a in hits:
            ev = a.get("event") or ""
            if ev in T.BEACH_OUTSIDE_EVENTS:
                lv = T.worst([lv, "outside"])
                names.append(ev)
            elif ev in T.BEACH_EXPOSURE_EVENTS:
                lv = T.worst([lv, "marginal" if exp == "lee" else "outside"])
                names.append(ev)
        factors.append(factor("headlines", "NWS beach headlines", lv, ", ".join(names) or "none in effect",
                              "Any High Surf Advisory or Warning is outside; a Beach Hazards or Rip Current Statement is outside at a site facing the swell, marginal in its lee.",
                              src.get("alerts")))

    # 2. Rip current risk (beach entries)
    if site.get("beach_entry"):
        rip = srf_period.get("rip_risk") if srf_period else None
        if rip is None:
            factors.append(factor("rip", "Rip current risk", "unknown", "no surf zone forecast",
                                  "Low is within, moderate marginal, high outside (NWS Surf Zone Forecast).", src.get("srf")))
        else:
            lv = {"Low": "within", "Moderate": "marginal", "High": "outside"}[rip]
            note = f"{rip} (county forecast)"
            if exp == "lee":
                lv = T.lower_one(lv)
                note += "; lowered one level because the entry is in the swell's lee"
            factors.append(factor("rip", "Rip current risk", lv, note,
                                  "Low is within, moderate marginal, high outside (NWS Surf Zone Forecast).", src.get("srf")))

    # 3. Surf at the site
    sets = srf_period.get("sets_ft") if srf_period else None
    if sets is None or exp is None:
        factors.append(factor("surf", "Surf at the entry", "unknown", "no surf height or swell direction",
                              "Estimated sets under 2.5 ft within, under 4.5 ft marginal, otherwise outside.", src.get("srf"), "Modeled"))
    else:
        est = sets * T.EXPOSURE_SCALE[exp]
        lv = "within" if est < T.SURF_FT[0] else "marginal" if est < T.SURF_FT[1] else "outside"
        long_p = max((t["s"] for t in (marine_period or {}).get("trains", []) if t["ft"] >= 2), default=None)
        note = (f"county sets to {sets} ft from {swell_from:.0f}°; the entry {EXPOSURE_WORDS[exp]}, "
                f"so about {est:.0f} ft here")
        if exp == "into" and long_p and long_p >= T.LONG_PERIOD_S:
            lv = T.raise_one(lv)
            note += f"; raised one level for {long_p}-second swell"
        factors.append(factor("surf", "Surf at the entry", lv, note,
                              "County sets scaled by how the entry faces the swell (1.0 facing, 0.6 at an angle, 0.35 in the lee); "
                              "under 2.5 ft within, under 4.5 ft marginal, otherwise outside; long-period swell (13 s+) raises it one level where the entry faces the swell.",
                              src.get("srf"), "Modeled"))

    # 4. Wind blowing into the entry
    if marine_period is None:
        factors.append(factor("onshore_wind", "Wind into the entry", "unknown", "no marine forecast",
                              "Wind from within 60° of the entry's facing: 15 kt marginal, 20 kt outside.", src.get("marine"),
                              required=False))
    else:
        w = marine_period["wind"]
        onshore = [d for d in w["directions"] if angle_diff(nws_text.COMPASS_DEG[d], site["faces_deg"]) <= T.ONSHORE_WITHIN_DEG]
        hi = w.get("sustained_hi_kt")
        lv = "within"
        if onshore and hi is not None:
            lv = "outside" if hi >= T.ONSHORE_WIND_KT[1] else "marginal" if hi >= T.ONSHORE_WIND_KT[0] else "within"
        val = f"{'/'.join(w['directions']) or 'variable'} up to {hi} kt" if hi is not None else "light"
        val += " (onshore here)" if onshore else " (not onshore here)"
        factors.append(factor("onshore_wind", "Wind into the entry", lv, val,
                              "Wind from within 60° of the entry's facing: 15 kt marginal, 20 kt outside.", src.get("marine"),
                              required=False))

    # 5. Thunder
    thunder = srf_period.get("thunder") if srf_period else None
    if thunder is not None:
        lv = "within" if thunder.lower().startswith("none") else "outside"
        factors.append(factor("thunder", "Thunderstorms", lv, thunder, "Any thunder is outside.", src.get("srf"), required=False))

    # 6. Rain in the last 72 hours (county water-contact guidance)
    if rain is None:
        factors.append(factor("rain", "Rain in the last 72 hours", "unknown", "rain gauge unavailable",
                              "San Diego County: avoid ocean contact for 72 hours after rain (0.2 in or more).", src.get("rain"),
                              "Observed", required=False))
    else:
        inches = rain["inches"]
        lv = "outside" if inches >= T.RAIN_ADVISORY_IN else "within"
        factors.append(factor("rain", "Rain in the last 72 hours", lv, f"{inches:.2f} in at the gauge",
                              "San Diego County: avoid ocean contact for 72 hours after rain (0.2 in or more).", src.get("rain"),
                              "Observed", required=False))

    return {"level": T.combine(factors), "exposure": exp, "swell_from_deg": swell_from, "factors": factors}


# --- Boat by range -----------------------------------------------------------

ZONE_NAMES = {"PZZ740": "San Diego inshore waters", "PZZ745": "San Diego offshore waters",
              "PZZ655": "inner waters", "PZZ676": "outer waters", "PMZ009": "Mexican waters"}


def merge_route(per_zone: list[tuple[str, dict]]) -> dict:
    """Combine a range's own zone with the zones its run crosses: factor by
    factor keep the worst, and say which zone it came from when it is not the
    range's own. Missing data in a crossed zone stays visible but optional."""
    (own_zone, own), *crossed = per_zone
    merged = {f["key"]: dict(f) for f in own["factors"]}
    for zcode, day in crossed:
        for f in day["factors"]:
            cur = merged.get(f["key"])
            if f["level"] == "unknown":
                continue
            if cur is None or cur["level"] == "unknown" or (
                    f["level"] in T.LEVELS and T.LEVELS.index(f["level"]) > T.LEVELS.index(cur["level"])):
                g = dict(f)
                g["value"] = f"{f['value']} ({ZONE_NAMES.get(zcode, zcode)}, on the way)"
                g["required"] = cur["required"] if cur else False
                merged[f["key"]] = g
    factors = list(merged.values())
    return {"level": T.combine(factors), "factors": factors, "zones": [z for z, _ in per_zone]}



def boat_day(rng: dict, *, period: dict | None, block_headlines: list[str] | None, alerts: list[dict] | None,
             window: tuple[datetime, datetime], src: dict[str, str]) -> dict:
    kind = rng["kind"]
    factors = []

    # 1. Headlines
    if alerts is None and rng["office"] != "NHC":
        factors.append(factor("headlines", "NWS marine headlines", "unknown", "alerts feed unavailable",
                              "Any advisory or warning in a zone crossed is outside; a gale or storm watch is outside for crossings, marginal otherwise.",
                              src.get("alerts")))
    else:
        lv, names = "within", []
        for a in _alerts_for(alerts or [], rng["alert_zones"], *window):
            ev = a.get("event") or ""
            if ev in T.MARINE_OUTSIDE_EVENTS:
                lv = T.worst([lv, "outside"])
                names.append(ev)
            elif ev in T.MARINE_WATCH_EVENTS:
                lv = T.worst([lv, "outside" if rng["crossing"] else "marginal"])
                names.append(ev)
        if rng["office"] == "NHC":
            for h in block_headlines or []:
                if any(w in h for w in T.MARINE_OUTSIDE_WORDS):
                    lv = T.worst([lv, "outside"])
                    names.append(h.title())
                elif any(w in h for w in T.MARINE_WATCH_WORDS):
                    lv = T.worst([lv, "marginal"])
                    names.append(h.title())
        factors.append(factor("headlines", "NWS marine headlines", lv, ", ".join(dict.fromkeys(names)) or "none in effect",
                              "Any advisory or warning in a zone crossed is outside; a gale or storm watch is outside for crossings, marginal otherwise.",
                              src.get("alerts")))

    if period is None:
        for key, label in (("wind", "Wind"), ("seas", "Seas"), ("steepness", "Wave steepness")):
            factors.append(factor(key, label, "unknown", "no forecast for this day", "", src.get("marine")))
        return {"level": T.combine(factors), "factors": factors}

    w = period["wind"]
    hi, gust = w.get("sustained_hi_kt"), w.get("gust_kt")
    within_kt, marginal_kt = T.WIND_KT[kind]
    if hi is None:
        factors.append(factor("wind", "Wind", "unknown", "no wind in forecast text", "", src.get("marine")))
    else:
        lv = "within" if hi <= within_kt else "marginal" if hi <= marginal_kt else "outside"
        if gust is not None and gust >= T.GUST_OUTSIDE_KT[kind]:
            lv = "outside"
        val = f"{'/'.join(w['directions']) or 'variable'} up to {hi} kt" + (f", gusts {gust} kt" if gust else "")
        factors.append(factor("wind", "Wind", lv, val,
                              f"Sustained {within_kt} kt or less within, up to {marginal_kt} kt marginal; gusts {T.GUST_OUTSIDE_KT[kind]} kt+ outside.",
                              src.get("marine")))

    seas = period.get("seas_hi_ft")
    s_within, s_marginal = T.SEAS_FT[kind]
    if seas is None:
        factors.append(factor("seas", "Seas", "unknown", "no seas in forecast text", "", src.get("marine")))
    else:
        lv = "within" if seas <= s_within else "marginal" if seas <= s_marginal else "outside"
        lo = period.get("seas_lo_ft")
        factors.append(factor("seas", "Seas", lv, f"{lo} to {seas} ft" if lo != seas else f"{seas} ft",
                              f"Combined seas {s_within} ft or less within, up to {s_marginal} ft marginal.", src.get("marine")))

    trains = period.get("trains") or []
    if not trains and period.get("period_s") and seas:
        trains = [{"dir": "", "ft": seas, "s": period["period_s"]}]
    if not trains:
        factors.append(factor("steepness", "Wave steepness", "unknown", "no wave detail", "", src.get("marine"), required=False))
    else:
        lv, worst_t = "within", None
        for t in trains:
            if (t["ft"] >= T.SCA_STEEP_MIN_FT and t["ft"] >= t["s"]) or t["ft"] >= T.SCA_ANY_PERIOD_FT:
                lv, worst_t = "outside", t
            elif t["s"] < 2 * t["ft"] and lv != "outside":
                lv, worst_t = "marginal", t
        desc = ", ".join(f"{t['dir']} {t['ft']} ft at {t['s']} s".strip() for t in trains)
        if worst_t and lv != "within":
            desc += f" (steepest: {worst_t['ft']} ft at {worst_t['s']} s)"
        factors.append(factor("steepness", "Wave steepness", lv, desc,
                              "NWS San Diego steepness rule: seas of 6-10 ft with height (ft) at or above period (s), or 11 ft+, is outside; "
                              "any train with period under twice its height is marginal (short, steep chop).",
                              src.get("marine")))

    sa_dirs = set(w["directions"]) & T.SANTA_ANA_DIRS
    if sa_dirs and ((gust or 0) >= T.SANTA_ANA_GUST_KT or (hi or 0) >= T.SANTA_ANA_SUSTAINED_KT):
        factors.append(factor("santa_ana", "Offshore (Santa Ana-pattern) wind", T.SANTA_ANA_LEVEL[kind],
                              f"{'/'.join(sorted(sa_dirs))} wind, {hi} kt" + (f" gusting {gust}" if gust else ""),
                              "East or northeast wind gusting 20 kt+ (or sustained 20 kt+): marginal inshore, outside beyond.",
                              src.get("marine"), required=False))

    if (period.get("fog_vis_nm") is not None and period["fog_vis_nm"] <= 1) or period.get("dense_fog"):
        factors.append(factor("fog", "Fog", T.FOG_LEVEL[kind], "visibility 1 nm or less forecast",
                              "Visibility 1 nm or less: marginal inshore, outside beyond.", src.get("marine"), required=False))

    return {"level": T.combine(factors), "factors": factors}
