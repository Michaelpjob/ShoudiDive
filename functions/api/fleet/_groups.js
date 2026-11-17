// Vessel groups for the Find box: what each hull the live logger hears is
// for. AIS carries a self-declared type code that owners set wrong often
// enough (a lifeguard boat coded "pleasure", a 5 m skiff coded "fishing",
// research ships coded "other") that the code comes last. In order, first
// match wins:
//   1. hand overrides                     (_vessel_groups.js OVERRIDES)
//   2. the fleet roster                   (_roster_lists.js FLEET)
//   3. hand-kept research / government    (_vessel_groups.js)
//   4. name rules                         (R/V, CG, FIREBOAT, BAYWATCH, US GOV ...)
//   5. US federal MMSI blocks and N-prefixed four-letter call signs
//   6. a landing's fleet list             (_roster_lists.js LOOKALIKES / FLEET_LIST)
//   7. AIS type and length
// Every result carries `why`, shown to the user, and `review` when the group
// is a guess worth a human look (the dev-only review queue).
import { FLEET, LOOKALIKES, FLEET_LIST } from "./_roster_lists.js";
import { RESEARCH, GOVERNMENT, OVERRIDES } from "./_vessel_groups.js";

export const GROUPS = [
  ["fleet", "Sportfishing fleet"],
  ["sport", "Possible sportfishers"],
  ["fish", "Commercial fishing"],
  ["dive", "Dive and charter"],
  ["research", "Research and survey"],
  ["gov", "Government and rescue"],
  ["work", "Tugs and work boats"],
  ["ship", "Ships"],
  ["private", "Private boats"],
  ["unknown", "Not identified yet"],
];
const KEYS = new Set(GROUPS.map((g) => g[0]));

export function normName(s) { return String(s || "").toUpperCase().replace(/[^A-Z0-9]/g, ""); }

const RESEARCH_NAME = /^(R\/V|RV|E\/V|NOAAS?)\s/;
// [pattern, who] for names that say whose boat it is
const GOV_NAMES = [
  [/^(USCGC?|CGC?)[\s\d]/, "the Coast Guard"],
  [/^(USNS|USS|USAV)\s|\b(U\.?S\.?|USS) ?GOV|\bUSGOV|\bGOVT? (VESSEL|VSL)\b|\bUS NAVY\b|\bNAVY UNIT\b|\bWARSHIP\b/, "the US government"],
  [/^(ARM|CNS|BAP|HMCS|JS|ROKS)\s/, "a foreign navy"],
  [/FIRE ?BOAT|\b(LBFD|LAFD|SDFD)\b/, "a fire department"],
  [/\bBAYWATCH|\bLIFEGUARD|\bRESCUE BOAT\b/, "lifeguards"],
  [/HARBOU?R PATROL|\bMARINE PATROL\b|\bPTRL|\bSHERIFF\b|\bPOLICE\b|\b(LBHP|LAPPD|SDPD|MLETC|CBP)\b/, "police or harbor patrol"],
];
const US_MID = /^(303|338|366|367|368|369)\d{6}$/;

const TYPE_TEXT = {
  30: "fishing", 31: "towing", 32: "towing", 33: "dredging", 34: "diving operations", 35: "military",
  36: "sailing", 37: "pleasure craft", 50: "pilot", 51: "search and rescue", 52: "tug", 53: "port tender",
  54: "anti-pollution", 55: "law enforcement", 56: "local vessel", 57: "local vessel", 58: "medical",
  59: "noncombatant",
};
function typeText(t) {
  if (TYPE_TEXT[t]) return TYPE_TEXT[t];
  if (t >= 40 && t < 50) return "high-speed craft";
  if (t >= 60 && t < 70) return "passenger";
  if (t >= 70 && t < 80) return "cargo";
  if (t >= 80 && t < 90) return "tanker";
  if (t >= 90 && t < 100) return "other";
  return null;
}
const m = (len) => (len ? `, ${len} m` : "");
const coded = (t, len) => `AIS type ${t} (${typeText(t) || "not a standard code"})${m(len)}`;

// x: {mmsi, n (name), ty (AIS type), len (m), cs (call sign)} as in vessels.json
export function classify(x) {
  const mmsi = String(x.mmsi), name = String(x.n || "").toUpperCase().trim();
  const t = x.ty == null ? null : +x.ty, len = x.len ? +x.len : null, cs = String(x.cs || "").toUpperCase();
  const g = (group, why, review = false) => ({ g: group, why, review });

  const o = OVERRIDES[mmsi];
  if (o && KEYS.has(o[0])) return g(o[0], o[1] || "set by hand");
  if (FLEET[mmsi]) return g("fleet", "on the fleet roster");
  if (RESEARCH[mmsi]) return g("research", RESEARCH[mmsi]);
  if (GOVERNMENT[mmsi]) return g("gov", GOVERNMENT[mmsi]);
  if (RESEARCH_NAME.test(name)) return g("research", "name says research vessel", true);
  const who = GOV_NAMES.find((p) => p[0].test(name));
  if (who) return g("gov", "name says " + who[1]);
  if (/^(3699|338926)/.test(mmsi)) return g("gov", "MMSI in a US federal block");
  if (US_MID.test(mmsi) && /^N[A-Z]{3}$/.test(cs)) return g("gov", `US federal call sign ${cs}`);

  const landing = LOOKALIKES[mmsi] || FLEET_LIST[normName(name)];
  const partyShape = (t == null || t === 0 || t === 6 || t === 30 || t === 37 || (t >= 60 && t < 70) || (t >= 90 && t < 100))
    && (len == null || (len >= 10 && len <= 45));
  if (landing && partyShape) return g("sport", `name is on ${landing}'s fleet list${t ? "; " + coded(t, len) : ""}`, true);

  if (t == null) return g("unknown", "no identity report heard yet");
  if (t === 0) return g("unknown", "AIS type not set");
  if (t === 6) return g(len != null && len >= 50 ? "ship" : "dive", `AIS type 6 (not a standard code, read as passenger)${m(len)}`, true);
  if (t === 30) {
    if (len != null && len < 12) return g("private", `${coded(t, len)}: most fishing-coded boats this small are private`);
    return g("fish", coded(t, len), true);
  }
  if (t === 34) return g("dive", coded(t, len), true);
  if ([35, 51, 55, 58, 59].includes(t)) return g("gov", coded(t, len), true);   // owners misuse these codes too
  if (t === 36 || t === 37) return g("private", coded(t, len));
  if ([31, 32, 33, 50, 52, 53, 54, 56, 57].includes(t)) return g("work", coded(t, len));
  if (t >= 40 && t < 50) return g("ship", coded(t, len) + ", mostly fast ferries");
  if (t >= 60 && t < 70) return len != null && len >= 50 ? g("ship", coded(t, len)) : g("dive", coded(t, len), true);
  if (t >= 70 && t < 90) return g("ship", coded(t, len));
  if (t >= 90 && t < 100) return len != null && len >= 100 ? g("ship", coded(t, len)) : g("work", coded(t, len), true);
  return g("unknown", coded(t, len), true);
}
