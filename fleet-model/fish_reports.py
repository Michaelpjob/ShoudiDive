"""The landings' written fish reports, crawled and parsed for species and the
spots they name. Fills the place where AIS cannot see: the report says
"yellowfin and dorado on the 302 and the 425" even when the boat was past the
shore receivers' range or in the July-October 2026 archive gap.

    python fleet-model/fish_reports.py --since 2025-06-01      # crawl (cached, resumable)
    python fleet-model/fish_reports.py --since 2025-06-01 --parse-only

Source: sportfishingreport.com /fish_reports/saltwater.php?page=N (22 cards a
page, newest first) and each report page. robots.txt allows crawling; one
request per second, every page cached under $FLEET_DATA_DIR/fish_reports/ so
a re-run only fetches what is new. Long-range (Mexico) reports are skipped:
their grounds are outside this map.

Parsing (spot gazetteer and wording rules from the Pelagic Lab prototype,
BD Outdoors chart coordinates): a numbered spot counts only when written
"the 302" or "302 spot/bank/knuckle", since a bare number is a fish count.
Output: $FLEET_DATA_DIR/fish_reports.json, one row per SoCal report with its
date, landing/author, species mentioned (and counts quoted in the text) and
the spots named.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import threading
import time

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from config import DATA_DIR, LANDINGS  # noqa: E402

HOST = "https://www.sportfishingreport.com"
LIST_URL = HOST + "/fish_reports/saltwater.php?page={page}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; ShouldIDive fleet research; +https://shouldidive.com)"}
CACHE = os.path.join(DATA_DIR, "fish_reports")
OUT = os.path.join(DATA_DIR, "fish_reports.json")

SOCAL = set(LANDINGS) | {
    "Santa Barbara Landing", "Seaforth Staff", "Pacific Dawn", "Pacific Queen", "Pacific Voyager", "Ocean Odyssey",
    "Grande", "Tribute", "Voyager", "Constitution", "Tomahawk", "Poseidon", "Pegasus", "Legend", "Old Glory",
    "Liberty", "Excalibur", "Producer", "Horizon", "Islander", "Fortune", "Polaris Supreme", "Mustang", "Highliner",
    "Aztec", "Relentless", "Thunderbird", "Freedom", "Toronado", "Eldorado", "Pacific Islander", "New Lo-An", "Condor",
    "Outrider", "Chief", "Pride", "Malihini", "Premier", "San Diego", "Daily Double", "New Seaforth", "Dolphin",
    "El Gato Dos", "Cortez", "Apollo", "Sea Watch", "Mission Belle", "Point Loma", "Little G", "Ranger 85",
    "Vendetta 2", "Top Gun 80", "Tradition", "Patriot", "Nautilus", "Sum Fun", "Clemente", "Fury", "Aggressor",
    "Western Pride", "Enterprise", "Victory", "El Patron", "Native Sun", "Monte Carlo", "Truline", "Amigo",
    "Electra", "Chubasco II", "Southern Cal", "Sea Star", "Blue Horizon", "Spitfire", "Betty-O", "Aloha Spirit",
    "Speed Twin", "Coroloma", "Estella", "New Hustler", "Californian", "Island Spirit", "Pacific Eagle",
}

SPECIES_RE = {
    "dorado": r"\bdorado\b|\bmahi|dolphin\s?fish",
    "yellowtail": r"yellow\s?tail",
    "bluefin": r"blue\s?fin",
    "yellowfin": r"yellow\s?fin",
    "marlin": r"\bmarlin\b",
    "wahoo": r"\bwahoo\b",
    "skipjack": r"skip\s?jack",
    "seabass": r"white\s?sea\s?bass",
    "mako": r"\bmako\b",
}
COUNT_RE = {k: re.compile(r"(\d{1,4})\s+(?:" + v + r")", re.I) for k, v in SPECIES_RE.items()}
MENTION_RE = {k: re.compile(v, re.I) for k, v in SPECIES_RE.items()}

# Chart coordinates (BD Outdoors SoCal chart). Numbered spots are fathom
# soundings the fleet uses as names.
SPOTS = {
    "43": (32.6555, -117.9736), "81": (32.7195, -118.4061), "101": (32.25, -117.25), "125": (33.4833, -118.7139),
    "152": (33.2467, -118.1805), "157": (32.3805, -118.4667), "170": (33.7389, -119.2667), "172": (33.5033, -118.8417),
    "175": (33.6142, -118.9519), "178": (32.7, -117.4886), "181": (32.9139, -117.8833), "182": (32.6972, -117.7139),
    "209": (33.1056, -117.8722), "213": (31.8033, -117.8633), "226": (32.5055, -117.6333), "230": (32.3569, -117.707),
    "267": (33.2967, -117.8192), "270": (33.6833, -118.5806), "277": (33.2, -118.0889), "286": (33.5806, -118.6),
    "289": (32.9333, -118.0972), "302": (32.4472, -117.5833), "307": (33.7917, -118.5333), "312": (33.0236, -117.8083),
    "371": (32.27, -117.5467), "378": (31.9, -117.8333), "381": (32.6833, -118.6083), "390": (32.0833, -117.8133),
    "421": (32.1333, -117.8667), "425": (32.25, -117.3867), "439": (32.4972, -117.4), "474": (33.1639, -118.9167),
    "475": (32.0667, -117.1167), "499": (33.275, -118.7417), "711": (33.1333, -119.05),
}
NAMED = [  # display name, regex, (lat, lon), approximate?
    ("Cortes Bank", r"cort[ez]s", (32.4403, -119.1305), False),
    ("Tanner Bank", r"tanner", (32.7, -119.1333), False),
    ("60 Mile Bank", r"60\s*mile|sixty\s*mile", (32.0667, -118.2472), False),
    ("Hidden Bank", r"hidden\s*bank", (31.9, -117.5), False),
    ("Butterfly Bank", r"butterfly", (32.3667, -118.2972), False),
    ("Mackerel Bank", r"mackerel\s*bank", (33.0367, -118.3933), False),
    ("9 Mile Bank", r"\b9\s*mile|nine\s*mile", (32.6333, -117.4333), False),
    ("14 Mile Bank", r"\b14\s*mile|fourteen\s*mile", (33.4, -117.9972), False),
    ("Avalon Bank", r"avalon\s*bank", (33.4117, -118.225), False),
    ("Osborn Bank", r"osborn", (33.36, -119.0417), False),
    ("Kidney Bank", r"kidney", (33.5744, -119.0142), False),
    ("Farnsworth Bank", r"farnsworth", (33.3422, -118.5172), False),
    ("The Mushroom", r"mushroom", (32.0667, -118.5), False),
    ("The Corner", r"\bthe\s+corner\b", (32.6267, -117.8233), False),
    ("The Slide", r"\bthe\s+slide\b", (33.2833, -118.2445), False),
    ("The Boot", r"\bthe\s+boot\b", (33.7306, -118.7056), False),
    ("Upper Finger", r"upper\s*finger", (32.1133, -117.0733), False),
    ("Upper 500", r"upper\s*500", (31.7667, -117.5), False),
    ("1010 Trench", r"1010", (31.7639, -117.7333), False),
    ("San Salvador Knoll", r"salvador", (32.3167, -117.9), False),
    ("Northeast Bank", r"northeast\s*bank|\bne\s*bank", (32.3467, -119.64), False),
    ("Potato Bank", r"potato", (33.25, -119.8267), False),
    ("Cherry Banks", r"cherry\s*bank", (32.873, -119.4214), False),
    ("Coronado Islands", r"coronado", (32.42, -117.25), True),
    ("Catalina West End", r"west\s*end(?!\s*of\s*san\s*clemente)", (33.48, -118.60), True),
    ("Catalina East End", r"east\s*end", (33.30, -118.30), True),
    ("San Clemente Island", r"san\s*clemente|clemente\s*island", (32.90, -118.50), True),
    ("Pyramid Head", r"pyramid", (32.80, -118.35), True),
    ("China Point", r"china\s*point", (32.80, -118.42), True),
    ("Rockpile", r"rock\s*pile", None, True),
    ("La Jolla", r"la\s*jolla", (32.85, -117.30), True),
    ("Santa Barbara Island", r"santa\s*barbara\s*island", (33.47, -119.03), True),
    ("San Nicolas Island", r"san\s*nic", (33.25, -119.5), True),
]
GAZ = [(f"The {n}", re.compile(rf"\bthe\s+{n}\b|\b{n}\s*(?:spot|fathom|bank|knuckle)"), SPOTS[n], False) for n in SPOTS]
GAZ += [(name, re.compile(rx), ll, approx) for name, rx, ll, approx in NAMED]


class Fetcher:
    """Cached GET with a shared rate cap. The site answers slowly (~6 s a
    page), so a few requests run in flight, but never more than one STARTS per
    `min_interval` seconds."""
    def __init__(self, min_interval=1.0):
        self.s = requests.Session()
        self.min = min_interval
        self.next_at = 0.0
        self.lock = threading.Lock()
        self.n = 0

    def _slot(self):
        with self.lock:
            now = time.time()
            at = max(now, self.next_at)
            self.next_at = at + self.min
        if at > now:
            time.sleep(at - now)

    def get(self, url, name, max_age_h=None):
        p = os.path.join(CACHE, name)
        if os.path.exists(p) and (max_age_h is None or time.time() - os.path.getmtime(p) < max_age_h * 3600):
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read()
        for attempt in range(4):
            self._slot()
            try:
                r = self.s.get(url, headers=UA, timeout=90)
                r.raise_for_status()
                break
            except Exception:  # noqa: BLE001
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        with self.lock:
            self.n += 1
        os.makedirs(CACHE, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(r.text)
        return r.text


def parse_list(html):
    s = BeautifulSoup(html, "html.parser")
    out = []
    for card in s.select(".report-card"):
        a = card.select_one(".report-card-top a")
        if not a:
            continue
        bottom = card.select_one(".report-card-bottom")
        lines = [t.strip() for t in bottom.get_text("\n").split("\n") if t.strip()] if bottom else []
        date = None
        for ln in lines:
            if re.fullmatch(r"\d{1,2}-\d{1,2}-\d{4}", ln):
                m, d, y = ln.split("-")
                date = dt.date(int(y), int(m), int(d))
        author = lines[0] if lines else None
        landing = lines[1] if len(lines) > 2 else author
        out.append({"title": a.get_text(strip=True), "href": a.get("href", ""), "date": date,
                    "landing": landing, "author": author, "long_range": "longrangesportfishing" in a.get("href", "")})
    return out


def report_text(html):
    s = BeautifulSoup(html, "html.parser")
    title = s.select_one(".report_title_data")
    body = s.select_one(".report_descript_data")
    if body is None:
        return ""
    text = (title.get_text(" ", strip=True) + ". " if title else "") + body.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)[:8000]


def extract(text):
    sp = {}
    for k, rx in MENTION_RE.items():
        if rx.search(text):
            n = sum(int(m.group(1)) for m in COUNT_RE[k].finditer(text) if int(m.group(1)) < 2000)
            sp[k] = n
    low = text.lower()
    spots = [{"name": name, "ll": ll, "approx": approx} for name, rx, ll, approx in GAZ if rx.search(low)]
    return sp, spots


def _write(rows, since, today):
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"crawled": today, "since": since.isoformat(), "reports": rows}, fh)


def crawl(since, max_pages=900, workers=4, log=print):
    f = Fetcher()
    today = dt.date.today().isoformat()
    cards, page, done = [], 1, False
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        while not done and page <= max_pages:
            batch_pages = list(range(page, min(page + 8, max_pages + 1)))
            htmls = list(ex.map(lambda pg: f.get(LIST_URL.format(page=pg), f"list_{pg}.html",
                                                 max_age_h=12 if pg <= 30 else None), batch_pages))
            for pg, html in zip(batch_pages, htmls):
                b = parse_list(html)
                if not b or all(c["date"] and c["date"] < since for c in b):
                    done = True
                cards.extend(b)
            page += len(batch_pages)
            log(f"  list to page {page - 1}: {cards[-1]['date'] if cards else None} ({f.n} requests)")
    socal = [c for c in cards if c["date"] and c["date"] >= since and not c["long_range"]
             and (c["landing"] in SOCAL or c["author"] in SOCAL)]
    uniq, seen = [], set()
    for c in socal:
        if c["href"] not in seen:
            seen.add(c["href"])
            uniq.append(c)
    log(f"{len(cards)} cards to {cards[-1]['date'] if cards else None}; {len(uniq)} SoCal reports since {since}")

    def one(c):
        rid = re.search(r"/fish_reports/(\d+)/", c["href"])
        name = f"r{rid.group(1)}.html" if rid else re.sub(r"\W+", "_", c["href"])[-80:] + ".html"
        html = f.get(HOST + c["href"] if c["href"].startswith("/") else c["href"], name)
        text = report_text(html)
        sp, spots = extract(text)
        return {"date": c["date"].isoformat(), "landing": c["landing"], "author": c["author"],
                "title": c["title"], "url": HOST + c["href"], "species": sp, "spots": spots, "snippet": text[:400]}

    rows = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, c) for c in uniq]          # newest first
        for i, fu in enumerate(futs):
            try:
                rows.append(fu.result())
            except Exception as exc:  # noqa: BLE001
                log(f"  failed: {exc}")
            if i % 200 == 199:
                _write(rows, since, today)
                log(f"  {i + 1}/{len(uniq)} reports, back to {rows[-1]['date']} ({f.n} requests)")
    _write(rows, since, today)
    log(f"wrote {OUT}: {len(rows)} reports, {sum(1 for r in rows if r['species'])} name a target species, "
        f"{sum(1 for r in rows if r['spots'])} name a spot, {f.n} requests")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-06-01")
    ap.add_argument("--max-pages", type=int, default=900)
    a = ap.parse_args()
    crawl(dt.date.fromisoformat(a.since), a.max_pages, log=lambda m: print(m, flush=True))
