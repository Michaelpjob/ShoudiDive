"""Per-boat daily fish counts from the landings' public count pages.

sportfishingreport.com publishes each landing's counts by day at
  /landings/<slug>.php?month=M&year=YYYY&day=D
as one table: Boat | Trip Type | Anglers | Fish Count ("83 Bluefin Tuna, 50
Calico Bass, 165 Calico Bass Released"). Its robots.txt allows crawling; we
fetch one page per landing-day, one request per second, and cache the raw
HTML under $FLEET_DATA_DIR/counts/ so re-runs never re-fetch. Counts are
facts (what the dock posted), stored with the source URL per row.

    python catch.py --start 2025-06-01 --end 2025-10-31  -> counts.parquet
"""
import argparse
import datetime as dt
import os
import re
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import DATA_DIR

BASE = "https://www.sportfishingreport.com/landings/{slug}.php?month={m}&year={y}&day={d}"
UA = "Mozilla/5.0 (compatible; ShouldIDive fleet research; +https://shouldidive.com)"
LANDING_SLUGS = {
    "H&M Landing": "h&m-landing",
    "Fisherman's Landing": "fishermans-landing",
    "Point Loma Sportfishing": "point-loma-sportfishing",
    "Seaforth Sportfishing": "seaforth-sportfishing",
    "Oceanside Sea Center": "oceanside-sea-center",
    "Dana Wharf Sportfishing": "dana-wharf-sportfishing",
    "Davey's Locker": "daveys-locker",
    "Newport Landing": "newport-landing",
    "Long Beach Sportfishing": "long-beach-sportfishing",
    "Pierpoint Landing": "pierpoint-landing",
    "22nd Street Landing": "22nd-street-landing",
    "Redondo Sportfishing": "redondo-beach-sportfishing",
    "Marina del Rey Sportfishing": "marina-del-rey-sportfishing",
    "Channel Islands Sportfishing": "channel-islands-sportfishing",
    "Hook's Landing": "hooks-landing",
    "Ventura Sportfishing": "ventura-harbor-sportfishing-llc",
}
OUT = os.path.join(DATA_DIR, "counts.parquet")
SPECIES_RE = re.compile(r"(\d+)\s+([A-Za-z][A-Za-z' .\-]+?)(?:\s*\((?:up to|to)[^)]*\))?(?=,|$|\s+Released)")


def cache_path(slug, day):
    return os.path.join(DATA_DIR, "counts", slug.replace("&", "and"), f"{day.isoformat()}.html")


def fetch(slug, day, session, min_interval=1.0, _last=[0.0]):
    p = cache_path(slug, day)
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    wait = min_interval - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    url = BASE.format(slug=slug, m=day.month, y=day.year, d=day.day)
    r = session.get(url, headers={"User-Agent": UA}, timeout=60)
    _last[0] = time.time()
    r.raise_for_status()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(r.text)
    return r.text


def parse_fish(text):
    """'83 Bluefin Tuna, 50 Calico Bass, 165 Calico Bass Released' -> dict of kept, dict of released."""
    kept, rel = {}, {}
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        released = part.endswith("Released") or " Released" in part
        core = part.replace("Released", "").strip()
        core = re.sub(r"\s*\([^)]*\)", "", core)
        m = re.match(r"(\d+)\s+(.+)", core)
        if not m:
            continue
        n, sp = int(m.group(1)), m.group(2).strip()
        (rel if released else kept)[sp] = (rel if released else kept).get(sp, 0) + n
    return kept, rel


def parse_day(html, landing, day):
    s = BeautifulSoup(html, "html.parser")
    head = s.find(string=re.compile(r"Fish Counts for"))
    if head is None:
        return []
    tab = head.find_parent("table")
    if tab is None:
        return []
    rows = []
    for tr in tab.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) != 4 or cells[0] in ("Boat",) or not cells[2].strip().isdigit():
            continue
        boat, trip, anglers, fish = cells
        kept, rel = parse_fish(fish)
        rows.append({"landing": landing, "date": day.isoformat(), "boat": boat.strip(),
                     "trip_type": trip.strip(), "anglers": int(anglers),
                     "fish_kept": int(sum(kept.values())), "fish_released": int(sum(rel.values())),
                     "kept": kept, "released": rel, "raw": fish})
    return rows


def run(start, end, landings=None):
    landings = landings or list(LANDING_SLUGS)
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    session = requests.Session()
    rows = []
    n_req = 0
    for landing in landings:
        slug = LANDING_SLUGS[landing]
        day = d0
        while day <= d1:
            try:
                html = fetch(slug, day, session)
                n_req += 1
                rows.extend(parse_day(html, landing, day))
            except Exception as exc:  # noqa: BLE001
                print(f"  {landing} {day}: {exc}", flush=True)
            day += dt.timedelta(days=1)
        print(f"{landing}: {sum(1 for r in rows if r['landing'] == landing)} boat-days", flush=True)
    df = pd.DataFrame(rows)
    if len(df):
        df["kept"] = df["kept"].map(lambda d: str(d))
        df["released"] = df["released"].map(lambda d: str(d))
        # Merge into the existing file: a 14-day nightly scrape must never
        # overwrite the season already collected.
        if os.path.exists(OUT):
            old = pd.read_parquet(OUT)
            df = pd.concat([old, df], ignore_index=True).drop_duplicates(
                ["landing", "date", "boat", "trip_type", "anglers"], keep="last").sort_values(["date", "landing", "boat"])
        df.to_parquet(OUT, index=False)
    print(f"{len(df)} boat-day count rows (merged) -> {OUT}")
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--landing", action="append")
    a = ap.parse_args()
    run(a.start, a.end, a.landing)
