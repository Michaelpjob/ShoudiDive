"""Live bait at the barges, from Everingham Bros Bait Co's own bait report
(baitbarge.com/bait-report, a published Google Sheet): what each barge is
serving, its note (scoop limits), and the time it was posted. Three barges:
San Diego Bay, Mission Bay, Dana Point. One request per build; shown with
Everingham's name and a link.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import sources

TZ = ZoneInfo("America/Los_Angeles")

URL = "https://baitbarge.com/bait-report"
SHEET = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vSdG-EL9GxlkGH9N26rWvrbzHmXuIeNz1fsI9xGgmdNrqY70tlkIhl-Voh5mMEogdHUh21mKW88dXys"
         "/pub?gid=1941792441&single=true&output=csv")
# Sheet column headings -> the report's barge names (functions/api/reports/_lib.js BARGES).
BARGES = {"san diego bait barge": "San Diego Bay", "mission bay bait barge": "Mission Bay", "dana point bait barge": "Dana Point"}


def _as_of(text: str, year: int) -> str | None:
    """'10/15 - 8:45a' -> '2026-10-15T08:45' (local)."""
    m = re.search(r"(\d{1,2})/(\d{1,2})\D+(\d{1,2}):(\d{2})\s*([ap])", text or "", re.I)
    if not m:
        return None
    h = int(m.group(3)) % 12 + (12 if m.group(5).lower() == "p" else 0)
    return f"{year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}T{h:02d}:{m.group(4)}"


def parse_sheet(text: str, year: int) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    cols = {}
    for r in rows:
        for j, c in enumerate(r):
            name = BARGES.get(c.strip().lower())
            if name:
                cols[j] = name
        if cols:
            break
    out = []
    for j, name in cols.items():
        serving, notes, as_of, started = [], [], None, False
        for r in rows:
            cell = r[j].strip() if j < len(r) else ""
            if not cell:
                continue
            low = cell.lower()
            if low.startswith("currently serving"):
                started = True
                continue
            if low.startswith("as of"):
                as_of = _as_of(r[j + 1] if j + 1 < len(r) else "", year) or _as_of(cell, year)
                break
            if not started:
                continue
            if cell.startswith("**"):
                notes.append(cell.strip("* ").capitalize())
            else:
                serving.append(cell)
        out.append({"barge": name, "serving": serving, "notes": notes, "as_of": as_of})
    return out


def fetch(now: datetime | None = None) -> dict:
    label = "Everingham Bros bait report (San Diego Bay, Mission Bay, Dana Point)"

    def run():
        now_ = now or sources.utcnow()
        barges = parse_sheet(sources._get(SHEET).text, now_.year)
        if not barges or not any(b["serving"] for b in barges):
            raise RuntimeError("no barges found in the sheet")
        newest = max((b["as_of"] for b in barges if b["as_of"]), default=None)
        t = datetime.fromisoformat(newest).replace(tzinfo=TZ) if newest else now_
        return sources.source("bait_barges", label, URL, ok=True, time=t, data={"barges": barges, "url": URL}, now=now)
    return sources._guard("bait_barges", label, URL, run)
