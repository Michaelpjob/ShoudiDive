"""What is open, from fixed rules. Each entry quotes the rule and its source
page; verified against the CDFW pages on 2026-10-15. Regulations change, so
the report always links the source and says to check it."""
from __future__ import annotations

from datetime import date, datetime, timedelta

CDFW_LOBSTER = "https://wildlife.ca.gov/Conservation/Marine/Invertebrates/Lobster"
CDFW_GROUNDFISH = "https://wildlife.ca.gov/Fishing/Ocean/Regulations/Groundfish-Summary"
CDFW_MPA_MAP = "https://wildlife.ca.gov/Conservation/Marine/MPAs"
VERIFIED_ON = "2026-10-15"


def _first_wednesday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(2 - d.weekday()) % 7)


def lobster_season(season_start_year: int) -> tuple[datetime, datetime]:
    """Opens 6:00 p.m. on the Friday before the first Wednesday in October;
    closes 11:59:59 p.m. on the first Wednesday after March 15 (CDFW)."""
    first_wed = _first_wednesday(season_start_year, 10)
    opens = datetime.combine(first_wed - timedelta(days=5), datetime.min.time()).replace(hour=18)
    d = date(season_start_year + 1, 3, 16)
    close_day = d + timedelta(days=(2 - d.weekday()) % 7)
    closes = datetime.combine(close_day, datetime.min.time()).replace(hour=23, minute=59, second=59)
    return opens, closes


def _day(d: date | datetime) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def lobster_status(today: date) -> dict:
    for start_year in (today.year - 1, today.year):
        opens, closes = lobster_season(start_year)
        if opens.date() <= today <= closes.date():
            return {
                "key": "lobster", "label": "Spiny lobster", "open": True,
                "text": (f"Open {_day(opens)} (6 p.m.) to {_day(closes)}. "
                         "7 per person, 3 1/4 in carapace, divers by hand only, report card required."),
                "source": CDFW_LOBSTER,
            }
    opens, _ = lobster_season(today.year)
    return {"key": "lobster", "label": "Spiny lobster", "open": False,
            "text": f"Closed. Next season opens {_day(opens)} at 6 p.m.", "source": CDFW_LOBSTER}


# Southern Management Area (Point Conception to the border), boat-based anglers.
# Divers and shore-based anglers are exempt from the season and depth limits
# on the RCG complex (CDFW groundfish summary, footnote 2).
_SMA = [
    ((1, 1), (3, 31), "Closed to boat-based anglers (all groundfish)."),
    ((4, 1), (6, 30), "Open at all depths for boat-based anglers."),
    ((7, 1), (9, 30), "Boat-based anglers: inside 50 fathoms only."),
    ((10, 1), (12, 31), "Boat-based anglers: nearshore rockfish, cabezon and greenlings closed; "
                        "shelf rockfish and lingcod seaward of 50 fathoms only."),
]


def groundfish_status(today: date) -> dict:
    for (m0, d0), (m1, d1), text in _SMA:
        if date(today.year, m0, d0) <= today <= date(today.year, m1, d1):
            return {"key": "groundfish", "label": "Rockfish and lingcod (Southern Management Area)",
                    "open": None, "text": text + " Divers and shore anglers are exempt from these season and depth limits.",
                    "source": CDFW_GROUNDFISH}
    return {"key": "groundfish", "label": "Rockfish and lingcod", "open": None,
            "text": "See the CDFW groundfish summary.", "source": CDFW_GROUNDFISH}


def season_items(today: date) -> list[dict]:
    items = [lobster_status(today), groundfish_status(today)]
    for it in items:
        it["verified_on"] = VERIFIED_ON
    return items
