"""Read a fishing report's words into what a reader scans for: which fish (and
how many, when the report says), where (the Ocean Report's zones), and how they
were caught (bait, method, what the fish were on).

Source-agnostic: the same rules read a landing's captain report, a published
report we are allowed to use, or the notes on a report sent in through the
page. Places map to zones by the names anglers use, never to a pin: "the 9
Mile" names the zone, not the spot. A numbered spot counts only as "the 302"
or "302 spot", because a bare number in a report is a fish count.
Adapted from Pelagic Lab's report reader (model/verify.py).
"""
from __future__ import annotations

import re

# key -> (display name, pattern). Slang that collides with other words is left out.
SPECIES = {
    "bluefin": ("Bluefin tuna", r"blue\s?fin"),
    "yellowfin": ("Yellowfin tuna", r"yellow\s?fin"),
    "skipjack": ("Skipjack", r"skip\s?jacks?"),
    "dorado": ("Dorado", r"\bdorado\b|\bmahi(?:[\s-]?mahi)?\b"),
    "yellowtail": ("Yellowtail", r"yellow\s?tails?\b|\byellows\b"),
    "wahoo": ("Wahoo", r"\bwahoo\b"),
    "marlin": ("Striped marlin", r"\bmarlin\b"),
    "white seabass": ("White seabass", r"white\s?sea\s?bass|\bwsb\b"),
    "calico": ("Calico bass", r"\bcalico(?:\s?bass)?\b|kelp\s?bass"),
    "sand bass": ("Sand bass", r"sand\s?bass"),
    "barracuda": ("Barracuda", r"barracuda"),
    "bonito": ("Bonito", r"\bbonito\b"),
    "rockfish": ("Rockfish", r"rock\s?fish|vermilion|\bboccaccio\b|\bcopper\s?rock"),
    "lingcod": ("Lingcod", r"ling\s?cod"),
    "sheephead": ("Sheephead", r"sheep\s?head"),
    "halibut": ("Halibut", r"halibut"),
    "whitefish": ("Ocean whitefish", r"white\s?fish"),
    "sculpin": ("Sculpin", r"sculpin"),
    "mako": ("Mako shark", r"\bmakos?\b"),
    "lobster": ("Spiny lobster", r"lobsters?"),
}
_SPECIES_RX = {k: re.compile(v, re.I) for k, (_, v) in SPECIES.items()}
# "45 yellowtail", "3 bluefin tuna", "12 legal calico": a number right before the name.
_COUNT_RX = {k: re.compile(r"\b(\d{1,4})\s+(?:[a-z-]+\s+){0,2}?(?:" + v + r")", re.I) for k, (_, v) in SPECIES.items()}
_YEARLIKE = re.compile(r"^(19|20)\d\d$")

BAIT = {
    "sardine": r"sardines?",
    "anchovy": r"anchov(?:y|ies)",
    "mackerel": r"mackerel|\bmacks?\b",
    "squid": r"\bsquid\b",
}
METHODS = {
    "fly-line": r"fly[\s-]?lin(?:e|ed|es|ing)",
    "surface iron": r"surface\s+(?:iron|jig)",
    "yo-yo iron": r"yo[\s-]?yo",
    "flat-fall": r"flat[\s-]?fall",
    "kite": r"\bkites?\b",
    "trolling": r"\btroll(?:ing|ed|ers?)?\b",
    "slow-troll": r"slow[\s-]?troll",
    "dropper loop": r"dropper(?:\s+loop)?",
    "sinker rig": r"sinker\s+rig|sliding\s+sinker|rubber\s?band\s+sinker",
    "popper": r"\bpoppers?\b|top\s?water",
    "swimbait": r"swim\s?baits?|\bplastics?\b",
    "bottom fishing": r"bottom\s+fish(?:ing)?|rock\s?cod(?:ding)?\s+trip",
    "spear": r"\bspear(?:ed|fishing|gun|o)?\b|\bfreedive",
    "jig": r"\bjigs?\b|\bjigging\b",
}
SIGNS = {
    "kelp paddy": r"\bpadd(?:y|ies)\b",
    "foamer or breezer": r"foamers?|breezers?|boil(?:s|ing)?\b",
    "porpoise": r"porpoise|dolphins?",
    "birds": r"\bbirds\b|\bbird\s+school",
    "meter marks": r"meter\s+marks?|sonar\s+marks?|marks?\s+on\s+the\s+(?:meter|sonar)",
    "temperature break": r"temp(?:erature)?\s+break|current\s+line|color\s+break",
    "kelp": r"\bkelp\b(?!\s+padd)",
}
_BAIT_RX = {k: re.compile(v, re.I) for k, v in BAIT.items()}
_METHOD_RX = {k: re.compile(v, re.I) for k, v in METHODS.items()}
_SIGN_RX = {k: re.compile(v, re.I) for k, v in SIGNS.items()}


def _num(spots: str) -> str:
    """Numbered spots as anglers write them: "the 302", "302 spot", "302 bank"."""
    return r"\bthe\s+(?:" + spots + r")\b|\b(?:" + spots + r")\s*(?:spot|fathom|bank|knuckle)\b"


# Zone index (zones.ZONES order) -> place patterns. Port names alone ("out of
# Oceanside") are where boats leave from, not where they fished.
ZONE_PLACES = {
    0: [r"la\s*jolla", r"point\s*loma|pt\.?\s*loma", r"imperial\s*beach|\bib\s+(?:pipe|kelp)", r"whistler\s*buoy", r"mission\s+beach\s+kelp"],
    1: [r"del\s*mar", r"carlsbad", r"leucadia", r"encinitas", r"off\s+oceanside|oceanside\s+(?:kelp|red\s+buoy|pier)", r"box\s+canyon"],
    2: [r"san\s*onofre", r"barn\s+kelp", r"off\s+dana\s*point|dana\s*point\s+(?:kelp|headland)", r"san\s+clemente\s+(?:pier|coast|kelp)", r"salt\s+creek"],
    3: [r"coronado(?:s|\s+islands?)\b", r"middle\s+grounds", r"south\s+kelp\s+ridge|\bskr\b", r"\bpukey\s+point\b"],
    4: [r"catalina(?!\s+channel)", r"farnsworth", r"avalon(?!\s+bank)", r"\bisthmus\b", r"\bfront\s*side\s+of\s+catalina|backside\s+of\s+catalina"],
    5: [r"san\s*clemente\s*island|\bclemente\s+island|\bsci\b", r"pyramid\s+(?:head|cove)", r"china\s+point", r"desperation\s+reef"],
    6: [r"(?<!south\s)(?<!south)\b(?:9|nine)\s*mile", _num("178|182|43"), r"\bthe\s+corner\b"],
    7: [_num("267|209|312|181|289")],
    8: [r"\b(?:14|fourteen)\s*mile", r"avalon\s+bank", _num("152|277"), r"\bthe\s+slide\b"],
    9: [r"tanner", r"cort[ez]s", r"cherry\s+bank"],
    10: [r"south\s*(?:9|nine)", _num("226|302|425|371|230"), r"upper\s+hidden|hidden\s+bank", r"coronado\s+canyon"],
    11: [r"rock\s*pile", r"salsipuedes", r"upper\s*500", r"\b1010\b", r"todos\s+santos", r"off\s+ensenada"],
}
_ZONE_RX = {z: [re.compile(p, re.I) for p in ps] for z, ps in ZONE_PLACES.items()}
# Places the fleet fishes outside the report's zones: named, not mapped.
ELSEWHERE = {
    "Santa Barbara Island": r"santa\s*barbara\s*island",
    "Channel Islands": r"anacapa|santa\s*cruz\s*island|santa\s*rosa\s*island|san\s*miguel\s*island",
    "San Nicolas Island": r"san\s*nic(?:olas)?\b",
    "60 Mile Bank": r"\b60\s*mile|sixty\s*mile",
    "Osborn Bank": r"osborn",
    "San Martin Island": r"san\s*mart[ií]n",
    "Colonet": r"colonet",
    "Guadalupe Island": r"guadalupe",
    "Alijos Rocks": r"alijos",
    "Cedros": r"cedros",
    "Revillagigedo and long range": r"socorro|clari[oó]n|revillagigedo|hurricane\s+bank|thetis|long[\s-]?range",
}
_ELSE_RX = {k: re.compile(v, re.I) for k, v in ELSEWHERE.items()}
_TEMP_RX = re.compile(r"\b(5[5-9]|6\d|7\d|8[0-5])(?:\.\d)?\s*(?:°|º|degrees?|deg\b)", re.I)


def extract(text: str) -> dict:
    """What the report says, as lists a page can show. Counts are summed only
    where the text puts a number before the fish."""
    t = " ".join(str(text or "").split())
    species = []
    for k, rx in _SPECIES_RX.items():
        if not rx.search(t):
            continue
        counts = [int(m.group(1)) for m in _COUNT_RX[k].finditer(t) if not _YEARLIKE.match(m.group(1))]
        species.append({"key": k, "name": SPECIES[k][0], "count": sum(c for c in counts if c < 2000) or None})
    zones = [z for z, rxs in _ZONE_RX.items() if any(r.search(t) for r in rxs)]
    return {
        "species": species,
        "zones": zones,
        "elsewhere": [k for k, rx in _ELSE_RX.items() if rx.search(t)],
        "bait": [k for k, rx in _BAIT_RX.items() if rx.search(t)],
        "methods": [k for k, rx in _METHOD_RX.items() if rx.search(t)],
        "signs": [k for k, rx in _SIGN_RX.items() if rx.search(t)],
        "water_f": sorted({float(m.group(1)) for m in _TEMP_RX.finditer(t)}),
    }
