// Shared helpers for Ocean Report field reports: catches, sightings, bait at
// the barge, and dive conditions sent in by people on the water.
//
// Same storage, gate and privacy model as the paddy catch reports
// (functions/api/paddies/_lib.js), under its own "obs:" key prefix:
//   - submissions land in a pending queue; a moderator approves or rejects
//     them at /reports/review?key=<MODERATION_TOKEN>;
//   - locations are snapped to a 0.02 degree grid (~1.2 nm) before storage;
//   - the reporter's email (+ optional name) is for the moderator only and
//     is never returned by the public feed (see publicView);
//   - reporters earn reputation through approvals and independent
//     corroboration, and the public feed carries a non-PII trust tier.
// Bindings: REPORTS_KV (KV namespace), MODERATION_TOKEN (secret).
import {
  jsonResponse, snap, clip, emailOk, clientIp, sha256hex, nmBetween, withinDays, modGate, repScore,
} from "../paddies/_lib.js";

export { jsonResponse, clip, emailOk, clientIp, sha256hex, modGate, repScore };

export const BBOX = { latMin: 30.5, latMax: 34.8, lngMin: -121.5, lngMax: -116.8 };
export const MAX_AGE_DAYS = 21;      // shown for three weeks after the reported date
export const CAP = 800;
export const APPROVED_KEY = "obs:approved";
export const PENDING_PREFIX = "obs:pending:";
export const EMAIL_PREFIX = "obs:email:";
export const CORROBORATE_NM = 8;
export const CORROBORATE_DAYS = 3;
export const CONFIRM_MIN_SOURCES = 2;
export const TRUSTED_REP = 0.6;

export const TYPES = ["catch", "sighting", "bait", "dive"];
export const SPECIES = [
  "yellowfin", "bluefin", "skipjack", "dorado", "yellowtail", "wahoo", "striped marlin", "white seabass",
  "calico bass", "sand bass", "halibut", "sheephead", "bonito", "barracuda", "rockfish", "lingcod",
  "lobster", "mako", "other",
];
export const SIGHTINGS = [
  "kelp paddy", "birds working", "foamers or breezers", "porpoise", "whales", "bait ball",
  "marlin (tailer or sleeper)", "color or temperature break", "current line", "red tide",
];
export const BAITS = ["sardine", "anchovy", "mackerel", "squid", "mixed", "none"];
export const BARGES = {
  "San Diego Bay": [32.7166, -117.2282],
  "Mission Bay": [32.7631, -117.2366],
  "Oceanside": [33.2065, -117.3925],
  "Dana Point": [33.4605, -117.6985],
  "Newport Beach": [33.6045, -117.8890],
  "San Pedro / LA Harbor": [33.7110, -118.2720],
  "Redondo / King Harbor": [33.8450, -118.3980],
  "Marina del Rey": [33.9640, -118.4560],
  "Ensenada": [31.8590, -116.6250],
};
export const COLORS = ["blue", "blue-green", "green", "off-color", "red tide"];

function num(v, lo, hi) {
  if (v === "" || v == null) return null;
  const n = Number(v);
  return isFinite(n) && n >= lo && n <= hi ? Math.round(n * 10) / 10 : undefined;
}
function pick(v, list) {
  const k = String(v || "").trim().toLowerCase();
  return list.find((x) => x.toLowerCase() === k) || null;
}

export function bboxOk(lat, lng) {
  return typeof lat === "number" && typeof lng === "number" && isFinite(lat) && isFinite(lng) &&
    lat >= BBOX.latMin && lat <= BBOX.latMax && lng >= BBOX.lngMin && lng <= BBOX.lngMax;
}

export function dateOk(s, nowMs) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(s || ""))) return false;
  const t = Date.parse(s + "T12:00:00Z");
  if (isNaN(t)) return false;
  const age = (nowMs - t) / 86400000;
  return age >= -1 && age <= 14;
}

// Validate one submission body; returns {rec} or {error}.
export function validate(body, nowMs) {
  const type = pick(body.type, TYPES);
  if (!type) return { error: "unknown report type" };
  const date = String(body.date || "").slice(0, 10);
  if (!dateOk(date, nowMs)) return { error: "date must be within the last two weeks" };
  const rec = { type, date };
  if (type === "bait") {
    const barge = Object.keys(BARGES).find((b) => b === String(body.barge || ""));
    if (!barge) return { error: "pick a bait barge" };
    const bait = pick(body.bait, BAITS);
    if (!bait) return { error: "pick a bait" };
    rec.barge = barge;
    rec.bait = bait;
    rec.bait_size = clip(body.bait_size, 20) || null;
    rec.lat = BARGES[barge][0];
    rec.lng = BARGES[barge][1];
  } else {
    const lat = Number(body.lat), lng = Number(body.lng);
    if (!bboxOk(lat, lng)) return { error: "location is outside Southern California waters" };
    rec.lat = snap(lat);
    rec.lng = snap(lng);
  }
  if (type === "catch") {
    const sp = Array.isArray(body.species) ? body.species : [body.species];
    rec.species = [...new Set(sp.map((s) => pick(s, SPECIES)).filter(Boolean))].slice(0, 5);
    if (!rec.species.length) return { error: "pick at least one species" };
    rec.bait = pick(body.bait, BAITS);
  }
  if (type === "sighting") {
    rec.sighting = pick(body.sighting, SIGHTINGS);
    if (!rec.sighting) return { error: "pick what you saw" };
    const sp = Array.isArray(body.species) ? body.species : (body.species ? [body.species] : []);
    rec.species = [...new Set(sp.map((s) => pick(s, SPECIES)).filter(Boolean))].slice(0, 5);
  }
  if (type === "dive") {
    rec.vis_lo_ft = num(body.vis_lo_ft, 0, 150);
    rec.vis_hi_ft = num(body.vis_hi_ft, 0, 150);
    if (rec.vis_lo_ft == null && rec.vis_hi_ft == null) return { error: "visibility in feet is required for a dive report" };
    if (rec.vis_lo_ft === undefined || rec.vis_hi_ft === undefined) return { error: "visibility must be 0-150 ft" };
    if (rec.vis_lo_ft == null) rec.vis_lo_ft = rec.vis_hi_ft;
    if (rec.vis_hi_ft == null) rec.vis_hi_ft = rec.vis_lo_ft;
    if (rec.vis_hi_ft < rec.vis_lo_ft) [rec.vis_lo_ft, rec.vis_hi_ft] = [rec.vis_hi_ft, rec.vis_lo_ft];
    rec.depth_ft = num(body.depth_ft, 0, 200) ?? null;
    rec.surge = pick(body.surge, ["none", "light", "moderate", "heavy"]);
  }
  const t = num(body.water_temp_f, 40, 90);
  if (t === undefined) return { error: "water temperature must be 40-90 F" };
  rec.water_temp_f = t;
  rec.color = pick(body.color, COLORS);
  rec.notes = clip(body.notes, 280);
  return { rec };
}

export async function rateOk(env, id, limit, windowSec) {
  const key = `obs:rl:${id}`;
  const cur = parseInt((await env.REPORTS_KV.get(key)) || "0", 10);
  if (cur >= limit) return false;
  await env.REPORTS_KV.put(key, String(cur + 1), { expirationTtl: windowSec });
  return true;
}

export async function readApproved(env, nowMs) {
  const raw = await env.REPORTS_KV.get(APPROVED_KEY);
  let arr = [];
  try { arr = raw ? JSON.parse(raw) : []; } catch { arr = []; }
  return arr.filter((r) => {
    const t = Date.parse((r.date || "") + "T12:00:00Z");
    return !t || (nowMs - t) / 86400000 <= MAX_AGE_DAYS;
  });
}

export async function writeApproved(env, arr) {
  await env.REPORTS_KV.put(APPROVED_KEY, JSON.stringify(arr.slice(-CAP)));
}

export async function listPending(env) {
  const out = [];
  let cursor;
  do {
    const res = await env.REPORTS_KV.list({ prefix: PENDING_PREFIX, cursor, limit: 1000 });
    for (const k of res.keys) {
      const raw = await env.REPORTS_KV.get(k.name);
      if (raw) { try { out.push(JSON.parse(raw)); } catch { /* skip */ } }
    }
    cursor = res.list_complete ? null : res.cursor;
  } while (cursor);
  out.sort((a, b) => b.submittedAt - a.submittedAt);
  return out;
}

async function emailKey(email) {
  return EMAIL_PREFIX + (await sha256hex(String(email).toLowerCase())).slice(0, 16);
}

export async function recordEmail(env, email, name, type, ts) {
  const key = await emailKey(email);
  let rec = {};
  try { const raw = await env.REPORTS_KV.get(key); if (raw) rec = JSON.parse(raw); } catch { /* new */ }
  Object.assign(rec, { email, name: name || rec.name || "", lastType: type, lastTs: ts, firstTs: rec.firstTs || ts, count: (rec.count || 0) + 1 });
  await env.REPORTS_KV.put(key, JSON.stringify(rec));
}

export async function bumpReputation(env, email, deltas) {
  if (!email) return;
  const key = await emailKey(email);
  let rec = {};
  try { const raw = await env.REPORTS_KV.get(key); if (raw) rec = JSON.parse(raw); } catch { /* new */ }
  rec.email = email;
  for (const k of ["approved", "rejected", "corroborated"]) rec[k] = (rec[k] || 0) + (deltas[k] || 0);
  await env.REPORTS_KV.put(key, JSON.stringify(rec));
}

export async function listEmails(env) {
  const out = [];
  let cursor;
  do {
    const res = await env.REPORTS_KV.list({ prefix: EMAIL_PREFIX, cursor, limit: 1000 });
    for (const k of res.keys) {
      const raw = await env.REPORTS_KV.get(k.name);
      if (raw) { try { out.push(JSON.parse(raw)); } catch { /* skip */ } }
    }
    cursor = res.list_complete ? null : res.cursor;
  } while (cursor);
  return out;
}

// Two reports corroborate when they are the same kind of thing (same type,
// overlapping species or the same sighting/barge) close in space and time.
function sameThing(a, b) {
  if (a.type !== b.type) return false;
  if (a.type === "bait") return a.barge === b.barge;
  if (a.type === "sighting") return a.sighting === b.sighting;
  if (a.type === "catch") return (a.species || []).some((s) => (b.species || []).includes(s));
  return true;
}

export async function applyModeration(env, id, action) {
  const pkey = PENDING_PREFIX + id;
  const raw = await env.REPORTS_KV.get(pkey);
  if (!raw) return { ok: false, error: "not found", status: 404 };
  const rec = JSON.parse(raw);
  await env.REPORTS_KV.delete(pkey);
  if (action === "reject") {
    await bumpReputation(env, rec.email, { rejected: 1 });
    return { ok: true, action, id };
  }
  const nowMs = Date.now();
  const arr = await readApproved(env, nowMs);
  const me = (rec.email || "").toLowerCase();
  const near = arr.filter((o) => sameThing(o, rec) && (o.email || "").toLowerCase() !== me && o.ipHash !== rec.ipHash &&
    nmBetween(o, rec) <= CORROBORATE_NM && withinDays(o, rec, CORROBORATE_DAYS));
  rec.status = "approved";
  rec.approvedAt = nowMs;
  arr.push(rec);
  await writeApproved(env, arr);
  await bumpReputation(env, rec.email, { approved: 1, corroborated: near.length ? 1 : 0 });
  const credited = new Set();
  for (const o of near) {
    const e = (o.email || "").toLowerCase();
    if (e && !credited.has(e)) { credited.add(e); await bumpReputation(env, o.email, { corroborated: 1 }); }
  }
  return { ok: true, action, id };
}

export function attachConfidence(approved, repMap) {
  return approved.map((r) => {
    const cluster = approved.filter((o) => sameThing(o, r) && nmBetween(o, r) <= CORROBORATE_NM && withinDays(o, r, CORROBORATE_DAYS));
    const ips = new Set(cluster.map((o) => o.ipHash).filter(Boolean));
    const emails = new Set(cluster.map((o) => (o.email || "").toLowerCase()).filter(Boolean));
    const sources = Math.min(ips.size, emails.size);
    const maxRep = cluster.reduce((mx, o) => Math.max(mx, repMap[(o.email || "").toLowerCase()] || 0), 0);
    const tier = sources >= CONFIRM_MIN_SOURCES || maxRep >= TRUSTED_REP ? "confirmed" : "single report";
    return { ...r, _sources: sources, _tier: tier };
  });
}

// What anyone can read: never the email, name, device or IP hash.
export function publicView(r) {
  const out = { id: r.id, type: r.type, date: r.date, lat: r.lat, lng: r.lng, confidence: r._tier || "single report",
    sources: r._sources || 1 };
  for (const k of ["species", "sighting", "barge", "bait", "bait_size", "vis_lo_ft", "vis_hi_ft", "depth_ft", "surge",
    "water_temp_f", "color", "notes"]) {
    if (r[k] != null && r[k] !== "" && !(Array.isArray(r[k]) && !r[k].length)) out[k] = r[k];
  }
  return out;
}
