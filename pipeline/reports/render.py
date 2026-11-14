"""Deterministic sentences from the facts. No number appears here that is not
already in the facts dict, and nothing here may say "safe": check_text()
enforces the banned-word list over everything we generate (agency headlines
quoted verbatim are exempt because they are not our words)."""
from __future__ import annotations

import re

from . import thresholds as T

_ORDER = {"outside": 0, "unknown": 1, "marginal": 2, "within": 3}


REASON = {
    "headlines": "NWS advisories", "rip": "rip current risk", "surf": "surf at the entry",
    "onshore_wind": "onshore wind", "thunder": "thunderstorms", "rain": "recent rain",
    "wind": "wind", "seas": "seas", "steepness": "steep short-period chop",
    "santa_ana": "Santa Ana-pattern offshore wind", "fog": "fog",
}


def join(names: list[str]) -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return "".join(names)
    sep = ", and " if len(names) > 2 and any(" and " in n for n in names) else " and "
    return ", ".join(names[:-1]) + sep + names[-1]


def why(day: dict) -> str:
    fs = day["factors"]
    level = day["level"]
    if level == "unknown":
        missing = [f"{f['label'].lower()} ({f['value']})" for f in fs if f["level"] == "unknown" and f.get("required", True)]
        return "Not rated: missing " + join(missing) + "."
    bad = sorted((f for f in fs if f["level"] in ("outside", "marginal")), key=lambda f: _ORDER[f["level"]])
    if not bad:
        return "Every factor we rate is within our thresholds."
    return " ".join(f"{f['label']}: {f['value']}." for f in bad[:3])


def _group(items: list[dict], day_key: str) -> dict[str, list[str]]:
    g: dict[str, list[str]] = {}
    for it in items:
        g.setdefault(it["days"][day_key]["level"], []).append(it["short"])
    return g


def _level_clauses(g: dict[str, list[str]]) -> list[str]:
    words = {"outside": "outside our thresholds at", "marginal": "marginal at",
             "within": "within our thresholds at", "unknown": "not rated (missing data) at"}
    return [f"{words[lv]} {join(g[lv])}" for lv in ("outside", "marginal", "within", "unknown") if g.get(lv)]


def bottom_line(facts: dict) -> list[str]:
    out = []
    shore = facts["can_i_go"]["shore"]
    srf_today = (facts["water"].get("surf") or {}).get("today") or {}
    s = "Shore diving today: " + "; ".join(_level_clauses(_group(shore, "today"))) + "."
    if srf_today.get("surf_text"):
        rip = f", with a {srf_today['rip_risk'].lower()} rip current risk" if srf_today.get("rip_risk") else ""
        sw = f" from {srf_today['swell_from_deg']}°" if srf_today.get("swell_from_deg") else ""
        s += f" The San Diego County surf forecast is {srf_today['surf_text'].lower()}{sw}{rip}."
    out.append(s)

    boat = facts["can_i_go"]["boat"]
    b = "By boat today: " + "; ".join(c.replace(" at ", " for ", 1) for c in _level_clauses(_group(boat, "today"))) + "."
    worse = [r for r in boat if _ORDER[r["days"]["tomorrow"]["level"]] < _ORDER[r["days"]["today"]["level"]]
             and r["days"]["tomorrow"]["level"] != "unknown"]
    if worse:
        reasons = []
        for r in worse:
            for f in r["days"]["tomorrow"]["factors"]:
                phrase = REASON.get(f["key"], f["label"])
                if f["level"] == r["days"]["tomorrow"]["level"] and phrase not in reasons:
                    reasons.append(phrase)
        tomorrow = facts["days"][1]["label"]
        b += (f" {tomorrow} gets worse for {join([r['short'] for r in worse])}"
              f" ({join(reasons[:3])}).")
    out.append(b)

    near = [x for x in facts["water"]["buoys"] if x.get("temp_f") is not None and x.get("nearshore")]
    temps = [x["temp_f"] for x in near]
    when = min((x["age_h"], x["observed"]) for x in near)[1] if near else None
    sst = facts["water"].get("sst_field") or {}
    w = ""
    if temps:
        lo, hi = min(temps), max(temps)
        span = f"{lo:.0f}-{hi:.0f}" if round(lo) != round(hi) else f"{lo:.0f}"
        w = f"Nearshore buoys read {span}°F as of {when}"
    if sst.get("date"):
        w += ("; " if w else "") + f"the latest satellite temperature map is from {sst['date_label']}"
    if w:
        out.append(w + ".")
    return out


def check_text(strings: list[str]) -> None:
    for s in strings:
        for word in T.BANNED_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", s, re.I):
                raise ValueError(f"banned word {word!r} in generated text: {s!r}")
