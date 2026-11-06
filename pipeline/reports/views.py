"""The three readers' views: fishing, casual diving, spearfishing.

Each view's lead lines are built only from values already in the facts, the
same rule as render.py; render.check_text() runs over all of them.
"""
from __future__ import annotations

import re

from .render import join


def _vis_max_ft(text: str | None) -> float | None:
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text or "")]
    return max(nums) if nums else None


def hunting_vis(posts: list[dict]) -> dict:
    """The latest visibility post against what spearfishing needs: 15 ft or
    more to hunt pelagics (yellowtail, white seabass), 10 ft for reef fish."""
    if not posts:
        return {"level": "unknown", "text": "No dive-shop visibility report in the last three days, so hunting visibility is unknown."}
    p = posts[0]
    top = _vis_max_ft(p["vis"])
    if top is None:
        return {"level": "unknown", "text": f"{p['source']} did not give a visibility number on {p['date_label']}."}
    if top >= 15:
        lv, verdict = "within", "enough to hunt pelagics (15 ft or more)"
    elif top >= 10:
        lv, verdict = "marginal", "workable for reef fish (10 ft), short of the 15 ft pelagic hunting needs"
    else:
        lv, verdict = "outside", "below what spearfishing needs (10 ft for reef fish, 15 ft for pelagics)"
    return {"level": lv, "text": f"{p['source']} reported {p['vis']} at {p['site']} on {p['date_label']}: {verdict}.",
            "source_url": p.get("url")}


def suit_guide(surface_f: float | None, observed: dict | None) -> dict:
    """Exposure protection sized for the bottom temperature, not the surface
    (dive-industry guide: 66F and up 3-5 mm; 50-65F 7 mm or a semi-dry)."""
    bottom = observed.get("t60_f") if observed else None
    ref = bottom if bottom is not None else surface_f
    if ref is None:
        return {"text": "No water temperature reading, so no suit guide today.", "tag": "Guide"}
    suit = ("3-5 mm for most divers" if ref >= 66 else "5-7 mm" if ref >= 60 else
            "7 mm or a semi-dry; a drysuit if you run cold" if ref >= 50 else "a drysuit")
    where = (f"{bottom:.0f}°F at 60 ft in the nearest glider profile" if bottom is not None
             else f"{surface_f:.0f}°F at the surface")
    return {"text": f"Suit: {suit}, sized for {where}. Kelp-bed bottoms can run colder when internal tides push cold water in.",
            "tag": "Guide", "ref_f": round(ref, 1)}


def _sets(site: dict, key: str) -> float:
    f = next((x for x in site["days"][key]["factors"] if x["key"] == "surf"), None)
    m = re.search(r"sets about ([\d.]+) ft", (f or {}).get("value", ""))
    return float(m.group(1)) if m else 99.0


def best_bet(shore: list[dict], key: str) -> dict:
    order = {"within": 0, "marginal": 1, "outside": 2, "unknown": 3}
    ranked = sorted(shore, key=lambda s: (order[s["days"][key]["level"]], _sets(s, key)))
    if not ranked or ranked[0]["days"][key]["level"] in ("outside", "unknown"):
        return {"site": None, "text": "No shore entry is within or marginal on our thresholds today."}
    top = ranked[0]
    s = _sets(top, key)
    extra = f", with sets around {s:.0f} ft at the entry in the CDIP model" if s < 99 else ""
    return {"site": top["short"], "level": top["days"][key]["level"],
            "text": f"Best bet from shore: {top['short']} ({top['days'][key]['label'].lower()}{extra})."}


def _thermo_line(obs: dict | None) -> str:
    if not obs:
        return ""
    return (f"A glider {obs['nm_from_sd']} nm from San Diego measured {obs['surface_f']:.0f}°F at the surface"
            + (f" and {obs['t100_f']:.0f}°F at 100 ft" if obs.get("t100_f") is not None else "")
            + f", with the sharpest drop near {obs['thermocline_ft']} ft ({obs['time']}).")


def view_lines(facts: dict) -> dict[str, list[str]]:
    shore_line, boat_line = facts["bottom_line"][0], facts["bottom_line"][1]
    obs = facts["water"].get("thermocline", {}).get("observed")
    fishing = facts.get("fishing") or {}
    divers = facts["divers"]
    therm = _thermo_line(obs)

    diving = [shore_line]
    bb = divers.get("best_bet")
    if bb:
        line = bb["text"]
        if divers.get("posts"):
            p = divers["posts"][0]
            line += f" {p['source']} reported {p['vis']} at {p['site']} on {p['date_label']}."
        diving.append(line)
    diving.append(" ".join(x for x in (divers["suit"]["text"], therm) if x))

    spear = [shore_line.replace("Shore diving today", "Entries today", 1), divers["hunting_vis"]["text"]]
    wsb = next((r for r in fishing.get("inshore", []) if r["key"] == "wsb"), None)
    open_now = [s["label"] for s in divers.get("season", []) if s.get("open")]
    third = therm
    if wsb and wsb.get("today"):
        third = (third + " White seabass: " + wsb["today"]).strip()
    if open_now:
        third += " Open now: " + join(open_now).lower() + " (by hand only)."
    if third.strip():
        spear.append(third.strip())

    fish_lines = [boat_line]
    spots = [s for s in fishing.get("spots", []) if s.get("sst_f") is not None]
    if spots:
        lo, hi = min(s["sst_f"] for s in spots), max(s["sst_f"] for s in spots)
        brk = max((s for s in spots if s.get("break")), key=lambda s: s["break"]["step_f"], default=None)
        sst = fishing.get("sst_field") or {}
        line = f"Water at the named spots runs {lo:.0f}-{hi:.0f}°F on the {sst.get('date_label', 'latest')} satellite map"
        if brk:
            b = brk["break"]
            line += (f"; the strongest break is near {brk['name']}, {b['step_f']}°F over {b['over_nm']} nm"
                     + (f", warm side to the {b['warm_side']}" if b.get("warm_side") else ""))
        fish_lines.append(line + ".")
    met = [s for s in fishing.get("species", []) if s["status"] == "profile met"]
    if met:
        fish_lines.append("Season and water both match today for " + join([s["label"].split(" (")[0].lower() for s in met])
                          + ". That says the water is right, not that fish are there.")
    fr, fn = fishing.get("fleet_recent"), fishing.get("fleet_now")
    if fn and fn.get("at_sea"):
        stopped = [b for b in fn["at_sea"] if b["stopped"]]
        line = f"AIS shows {len(fn['at_sea'])} boats at sea in the last 3 hours" + (f", {len(stopped)} of them stopped" if stopped else "") + "."
        if fr and fr.get("spots"):
            line += f" Over the last three days the fleet stopped longest at {fr['spots'][0]['spot']}."
        fish_lines.append(line)
    return {"fishing": fish_lines, "diving": diving, "spearfishing": spear}
