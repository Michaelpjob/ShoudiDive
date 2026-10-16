// Read a fishing report's words into report fields: which fish (and how many,
// when the text says), where (GPS if given, else the Ocean Report zone the
// place names point to), how (bait, method), water temperature and the day.
// Same rules as pipeline/reports/catch_text.py; used by the page's
// "say it in a sentence" box, the email intake and the text-message intake.
// A numbered spot counts only as "the 302" or "302 spot": a bare number is a
// fish count. Port names alone ("out of Oceanside") are not places fished.

// Species: form name (functions/api/reports/_lib.js SPECIES) -> pattern.
export const SPECIES_RX = [
  ["bluefin", /blue\s?fin/i],
  ["yellowfin", /yellow\s?fin/i],
  ["skipjack", /skip\s?jacks?/i],
  ["dorado", /\bdorado\b|\bmahi(?:[\s-]?mahi)?\b/i],
  ["yellowtail", /yellow\s?tails?\b|\byellows\b/i],
  ["wahoo", /\bwahoo\b/i],
  ["striped marlin", /\bmarlin\b/i],
  ["white seabass", /white\s?sea\s?bass|\bwsb\b/i],
  ["calico bass", /\bcalico(?:\s?bass)?\b|kelp\s?bass/i],
  ["sand bass", /sand\s?bass/i],
  ["barracuda", /barracuda/i],
  ["bonito", /\bbonito\b/i],
  ["rockfish", /rock\s?fish|vermilion|\bboccaccio\b|\bcopper\s?rock/i],
  ["lingcod", /ling\s?cod/i],
  ["sheephead", /sheep\s?head/i],
  ["halibut", /halibut/i],
  ["whitefish", /white\s?fish/i],
  ["sculpin", /sculpin/i],
  ["mako", /\bmakos?\b/i],
  ["lobster", /lobsters?/i],
];

export const METHOD_RX = [
  ["fly-line", /fly[\s-]?lin(?:e|ed|es|ing)/i],
  ["surface iron", /surface\s+(?:iron|jig)/i],
  ["yo-yo iron", /yo[\s-]?yo/i],
  ["flat-fall", /flat[\s-]?fall/i],
  ["kite", /\bkites?\b/i],
  ["slow-troll", /slow[\s-]?troll/i],
  ["trolling", /\btroll(?:ing|ed|ers?)?\b/i],
  ["dropper loop", /dropper(?:\s+loop)?/i],
  ["sinker rig", /sinker\s+rig|sliding\s+sinker|rubber\s?band\s+sinker/i],
  ["popper", /\bpoppers?\b|top\s?water/i],
  ["swimbait", /swim\s?baits?|\bplastics?\b/i],
  ["bottom fishing", /bottom\s+fish(?:ing)?/i],
  ["spear", /\bspear(?:ed|fishing|gun)?\b|\bfreedive/i],
  ["jig", /\bjigs?\b|\bjigging\b/i],
];
export const METHODS = METHOD_RX.map((m) => m[0]);

const BAIT_RX = [["sardine", /sardines?/i], ["anchovy", /anchov(?:y|ies)/i], ["mackerel", /mackerel|\bmacks?\b/i], ["squid", /\bsquid\b/i]];
const SIGHT_RX = [
  ["kelp paddy", /\bpadd(?:y|ies)\b/i], ["birds working", /\bbirds\b/i], ["foamers or breezers", /foamers?|breezers?|\bboil(?:s|ing)?\b/i],
  ["porpoise", /porpoise|dolphins?/i], ["whales", /\bwhales?\b/i], ["bait ball", /bait\s?balls?/i],
  ["color or temperature break", /temp(?:erature)?\s+break|color\s+break/i], ["current line", /current\s+line/i], ["red tide", /red\s+tide/i],
];

// Zones in pipeline/reports/zones.py order: [short name, center, place patterns].
export const ZONES = [
  ["La Jolla to IB", [32.74, -117.28], [/la\s*jolla/i, /point\s*loma|pt\.?\s*loma/i, /imperial\s*beach|\bib\s+(?:pipe|kelp)/i, /whistler\s*buoy/i, /mission\s+beach\s+kelp/i]],
  ["Del Mar to Oceanside", [33.05, -117.33], [/del\s*mar/i, /carlsbad/i, /leucadia/i, /encinitas/i, /off\s+oceanside|oceanside\s+(?:kelp|red\s+buoy|pier)/i, /box\s+canyon/i]],
  ["Dana Point coast", [33.40, -117.66], [/san\s*onofre/i, /barn\s+kelp/i, /off\s+dana\s*point|dana\s*point\s+(?:kelp|headland)/i, /san\s+clemente\s+(?:pier|coast|kelp)/i, /salt\s+creek/i]],
  ["Coronados", [32.41, -117.26], [/coronado(?:s|\s+islands?)\b/i, /middle\s+grounds/i, /south\s+kelp\s+ridge|\bskr\b/i]],
  ["Catalina", [33.38, -118.42], [/catalina(?!\s+channel)/i, /farnsworth/i, /avalon(?!\s+bank)/i, /\bisthmus\b/i]],
  ["San Clemente Is.", [32.90, -118.50], [/san\s*clemente\s*island|\bclemente\s+island|\bsci\b/i, /pyramid\s+(?:head|cove)/i, /china\s+point/i, /desperation\s+reef/i]],
  ["9 Mile / 43", [32.66, -117.72], [/(?<!south\s)(?<!south)\b(?:9|nine)\s*mile/i, /\bthe\s+(?:178|182|43)\b|\b(?:178|182|43)\s*(?:spot|fathom|bank|knuckle)\b/i, /\bthe\s+corner\b/i]],
  ["267 / 209-312", [33.12, -117.82], [/\bthe\s+(?:267|209|312|181|289)\b|\b(?:267|209|312|181|289)\s*(?:spot|fathom|bank|knuckle)\b/i]],
  ["14 Mile / Avalon Bank", [33.36, -118.08], [/\b(?:14|fourteen)\s*mile/i, /avalon\s+bank/i, /\bthe\s+(?:152|277)\b|\b(?:152|277)\s*(?:spot|fathom|bank|knuckle)\b/i, /\bthe\s+slide\b/i]],
  ["Tanner / Cortes", [32.60, -119.20], [/tanner/i, /cort[ez]s/i, /cherry\s+bank/i]],
  ["South 9 / 302 / 425", [32.33, -117.55], [/south\s*(?:9|nine)/i, /\bthe\s+(?:226|302|425|371|230)\b|\b(?:226|302|425|371|230)\s*(?:spot|fathom|bank|knuckle)\b/i, /upper\s+hidden|hidden\s+bank/i, /coronado\s+canyon/i]],
  ["Rockpile / Upper 500", [31.85, -117.45], [/rock\s*pile/i, /salsipuedes/i, /upper\s*500/i, /\b1010\b/i, /todos\s+santos/i, /off\s+ensenada/i]],
];

// "12 yellowtail", "5 nice yellowtail", "3 big fat yellowfin": a number, up to
// two words, then the species. Fixed patterns only (no RegExp built at runtime).
function countFor(text, rx) {
  const t = String(text || ""), numRx = /\b(\d{1,4})\s+/g;
  let sum = 0, m;
  while ((m = numRx.exec(t))) {
    let rest = t.slice(numRx.lastIndex), hit = false;
    for (let k = 0; k <= 2 && !hit; k++) {
      const s = rx.exec(rest);
      if (s && s.index === 0) hit = true;
      else {
        const w = /^[a-z-]+\s+/i.exec(rest);
        if (!w) break;
        rest = rest.slice(w[0].length);
      }
    }
    const n = parseInt(m[1], 10);
    if (hit && !/^(19|20)\d\d$/.test(m[1]) && n < 2000) sum += n;
  }
  return sum || null;
}

// GPS as anglers write it: "32 52 x 117 17", "33 29.271 x 117 48.153",
// "32°52.1'N 117°16.8'W", or decimal "32.87, -117.28". West is assumed.
export function parseGps(text) {
  const t = String(text || "");
  let m = /\b(3[0-4])(?:\s*[°º]\s*|\s+)(\d{1,2}(?:\.\d+)?)\s*'?\s*N?\s*(?:x|,|\/|by)?\s*(1[12]\d)(?:\s*[°º]\s*|\s+)(\d{1,2}(?:\.\d+)?)\s*'?\s*W?\b/i.exec(t);
  if (m) return [+m[1] + +m[2] / 60, -(+m[3] + +m[4] / 60)];
  m = /\b(3[0-4]\.\d{2,})\s*,?\s*(-?1[12]\d\.\d{2,})\b/.exec(t);
  if (m) return [+m[1], -Math.abs(+m[2])];
  return null;
}

// The day the report is about, from words in it; else the day it was sent.
export function reportDate(text, sentMs, tz = "America/Los_Angeles") {
  const day = (ms) => new Date(ms).toLocaleDateString("en-CA", { timeZone: tz });
  const t = String(text || "");
  if (/\byesterday\b/i.test(t)) return day(sentMs - 86400000);
  const m = /\b(1[0-2]|0?[1-9])\/([12]\d|3[01]|0?[1-9])(?:\/(\d{2,4}))?\b/.exec(t);
  if (m) {
    const today = day(sentMs), y = m[3] ? (m[3].length === 2 ? "20" + m[3] : m[3]) : today.slice(0, 4);
    const d = `${y}-${m[1].padStart(2, "0")}-${m[2].padStart(2, "0")}`;
    if (d <= today) return d;
  }
  return day(sentMs);
}

export function extract(text, sentMs = Date.now()) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  const species = SPECIES_RX.filter(([, rx]) => rx.test(t)).map(([name, rx]) => ({ name, count: countFor(t, rx) }));
  const zones = ZONES.map((z, i) => (z[2].some((rx) => rx.test(t)) ? i : -1)).filter((i) => i >= 0);
  const water = [...t.matchAll(/\b(5[5-9]|6\d|7\d|8[0-5])(?:\.\d)?\s*(?:°|º|degrees?|deg\b)/gi)].map((m) => +m[1]);
  const gps = parseGps(t);
  return {
    species,
    zones,
    gps,
    bait: BAIT_RX.filter(([, rx]) => rx.test(t)).map((b) => b[0]),
    methods: METHOD_RX.filter(([, rx]) => rx.test(t)).map((x) => x[0]),
    sightings: SIGHT_RX.filter(([, rx]) => rx.test(t)).map((x) => x[0]),
    water_temp_f: water.length ? water[0] : null,
    date: reportDate(t, sentMs),
  };
}

// A submission body (functions/api/reports/_lib.js validate) from free text.
// Location: GPS when given, else the first named zone's center flagged as
// zone-level; with neither there is no report to place.
export function toSubmission(text, sentMs = Date.now()) {
  const x = extract(text, sentMs);
  const body = { date: x.date, notes: "", water_temp_f: x.water_temp_f ?? "" };
  if (x.gps) { body.lat = x.gps[0]; body.lng = x.gps[1]; }
  else if (x.zones.length) { const c = ZONES[x.zones[0]][1]; body.lat = c[0]; body.lng = c[1]; body.loc = "zone"; }
  if (x.species.length) {
    body.type = "catch";
    body.species = x.species.slice(0, 5).map((s) => s.name);
    body.counts = Object.fromEntries(x.species.filter((s) => s.count).map((s) => [s.name, s.count]));
    if (x.bait[0]) body.bait = x.bait[0];
    if (x.methods[0]) body.method = x.methods[0];
  } else if (x.sightings.length) {
    body.type = "sighting";
    body.sighting = x.sightings[0];
  }
  return { body, parsed: x };
}
