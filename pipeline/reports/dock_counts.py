"""Dock counts from landings that publish their own, outside the Sportfishing
Report network (whose terms forbid reuse): Dana Wharf (Dana Point) and Davey's
Locker (Newport Beach). Counts are facts; the report shows them with the
landing's name and a link. One request per landing per build.

  Dana Wharf: the latest day's boats (boat, trip, anglers, counts) under a
  "Dana Wharf fishing report, October 15th, 2026" heading, plus 30 days of
  landing totals.
  Davey's Locker: one row per trip (date, boat, passengers, trip type, counts).
"""
from __future__ import annotations

import html as _html
import re
from datetime import date, datetime

from . import sources

LANDINGS = [
    {"id": "dock_danawharf", "landing": "Dana Wharf", "port": "Dana Point", "url": "https://danawharf.com/fish-counts/"},
    {"id": "dock_daveyslocker", "landing": "Davey's Locker", "port": "Newport Beach", "url": "https://daveyslocker.com/fish-count/"},
]

# Landing names -> the Ocean Report's species names (functions/api/reports/_lib.js SPECIES).
NAMES = {
    "yellowfin tuna": "yellowfin", "yellowfin": "yellowfin", "bluefin tuna": "bluefin", "bluefin": "bluefin",
    "skipjack tuna": "skipjack", "skipjack": "skipjack", "dorado": "dorado", "mahi mahi": "dorado", "yellowtail": "yellowtail",
    "wahoo": "wahoo", "striped marlin": "striped marlin", "white sea bass": "white seabass", "white seabass": "white seabass",
    "calico bass": "calico bass", "sand bass": "sand bass", "halibut": "halibut", "sheephead": "sheephead", "bonito": "bonito",
    "barracuda": "barracuda", "rockfish": "rockfish", "vermilion rockfish": "rockfish", "vermillion rockfish": "rockfish",
    "red rockfish": "rockfish", "ling cod": "lingcod", "lingcod": "lingcod", "whitefish": "whitefish", "sculpin": "sculpin",
    "spiny lobster": "lobster", "lobster": "lobster", "mako shark": "mako", "mako": "mako",
}
_FISH = re.compile(r"(\d{1,5})\s+([A-Za-z][A-Za-z' -]*?)(?=\s*(?:[,.;]|\d|$))")
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def parse_fish(text: str) -> tuple[dict, dict]:
    """'86 Yellowtail, 186 Yellowfin Tuna, 305 Bass Released' -> (kept, released)."""
    kept: dict[str, int] = {}
    released: dict[str, int] = {}
    for m in _FISH.finditer(str(text or "")):
        n, name = int(m.group(1)), " ".join(m.group(2).split()).lower()
        rel = name.endswith(" released")
        if rel:
            name = name[: -len(" released")]
        key = NAMES.get(name, name)
        bucket = released if rel else kept
        bucket[key] = bucket.get(key, 0) + n
    return kept, released


def _tables(page: str) -> list[list[list[str]]]:
    out = []
    for t in re.findall(r"<table.*?</table>", page, re.S | re.I):
        rows = []
        for r in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S | re.I):
            cells = [" ".join(_html.unescape(re.sub(r"<[^>]+>", " ", c)).split()) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S | re.I)]
            if cells:
                rows.append(cells)
        out.append(rows)
    return out


def _int(s: str) -> int | None:
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else None


def parse_dana(page: str) -> dict:
    m = re.search(r"fishing report,\s*(" + _MONTHS + r")\s+(\d{1,2})(?:st|nd|rd|th)?,\s*(\d{4})", _html.unescape(re.sub(r"<[^>]+>", " ", page)), re.I)
    day = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat() if m else None
    trips, days = [], []
    for rows in _tables(page):
        head = [c.lower() for c in rows[0]] if rows else []
        if head[:2] == ["boat", "trip"] and day:
            for r in rows[1:]:
                if len(r) >= 4:
                    kept, rel = parse_fish(r[3])
                    trips.append({"date": day, "boat": r[0].title(), "trip": r[1].lower(), "anglers": _int(r[2]), "catch": kept, "released": rel})
        elif head[:1] == ["date"]:
            for r in rows[1:]:
                try:
                    d = datetime.strptime(r[0], "%b %d, %Y").date().isoformat()
                except ValueError:
                    continue
                kept, rel = parse_fish(r[2] if len(r) > 2 else "")
                days.append({"date": d, "boats": _int(r[1]), "anglers": _int(r[1].split(",")[-1]) if "," in r[1] else None,
                             "catch": kept, "released": rel})
    return {"trips": trips, "days": days}


def parse_daveys(page: str) -> dict:
    trips = []
    for rows in _tables(page):
        head = [c.lower() for c in rows[0]] if rows else []
        if head[:2] != ["date", "boat name"]:
            continue
        for r in rows[1:]:
            if len(r) < 5:
                continue
            try:
                d = datetime.strptime(r[0], "%m/%d/%y").date().isoformat()
            except ValueError:
                continue
            kept, rel = parse_fish(r[4])
            trips.append({"date": d, "boat": r[1], "trip": r[3].lower(), "anglers": _int(r[2]), "catch": kept, "released": rel})
    return {"trips": trips}


PARSERS = {"dock_danawharf": parse_dana, "dock_daveyslocker": parse_daveys}


def fetch(now: datetime | None = None) -> list[dict]:
    out = []
    for L in LANDINGS:
        def run(L=L):
            page = sources._get(L["url"]).text
            data = PARSERS[L["id"]](page)
            if not data.get("trips") and not data.get("days"):
                raise RuntimeError("no counts found on the page")
            newest = max([t["date"] for t in data.get("trips", [])] + [d["date"] for d in data.get("days", [])])
            data.update({"landing": L["landing"], "port": L["port"], "url": L["url"]})
            return sources.source(L["id"], f"{L['landing']} fish counts", L["url"], ok=True,
                                  time=sources.parse_iso(newest + "T23:00:00Z"), data=data, now=now)
        out.append(sources._guard(L["id"], f"{L['landing']} fish counts", L["url"], run))
    return out


def trips_since(dock: list[dict], since: date) -> list[dict]:
    """All trips from every landing on or after `since`, with landing and port."""
    out = []
    for d in dock or []:
        for t in d.get("trips", []):
            if t["date"] >= since.isoformat():
                out.append({**t, "landing": d["landing"], "port": d["port"], "url": d["url"]})
    return out
