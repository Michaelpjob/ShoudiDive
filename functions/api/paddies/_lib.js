// Shared helpers for the /paddies/ crowdsourced catch-report API.
// Mirrors functions/api/analytics/_lib.js conventions: env bindings are
// dashboard-only, same-origin (no CORS).
//
// NOTE on PII: unlike the anonymous analytics path, the catch-report form
// deliberately collects a reporter email (+ optional name) that the submitter
// types in knowingly. That PII is stored server-side for the moderator only —
// it is NEVER returned by the public /reports feed (see publicView).
//
// Bindings used: REPORTS_KV (KV namespace), MODERATION_TOKEN (secret).

// Southern California Bight field bounds (must match the tool's FIELD_BBOX).
export const BBOX = { latMin: 31.0, latMax: 34.8, lngMin: -121.5, lngMax: -116.8 };

export const SNAP_DEG = 0.02;       // ~1.2 nm grid — honey-hole protection + dedupe
export const MAX_AGE_DAYS = 7;      // a catch must be within this; pending + approved expire on it (anchored to the CATCH date, not submission — paddies drift, so old intel ages out fast)
export const CAP = 500;             // ring-buffer: keep at most this many approved reports
export const APPROVED_KEY = "paddies:approved";
export const PENDING_PREFIX = "paddies:pending:";

// Species dropdown allowlist — accepting only these eliminates free-text abuse.
export const SPECIES = [
  "yellowtail", "dorado", "bluefin", "yellowfin", "white seabass",
  "bonito", "barracuda", "calico bass", "paddy",
];

// --- Trust / corroboration ----------------------------------------------
// A report is only "confirmed" once corroborated by genuinely DISTINCT sources
// (different IP AND different email) in the same zone+window, OR it comes from a
// reporter who's earned reputation. New/lone reports stay skeptical (faint).
// Weight is capped so even a trusted source can never dominate the model.
export const CORROBORATE_NM = 6;       // reports within this share a "zone"
export const CORROBORATE_DAYS = 4;     // ...and this time window
export const CONFIRM_MIN_SOURCES = 2;  // distinct (IP & email) sources to confirm
export const TRUSTED_REP = 0.6;        // a reporter at/above this confirms solo
export const REP_PRIOR_A = 1;          // Beta prior — skeptical: prior mean 0.2
export const REP_PRIOR_B = 4;
export const WEIGHT_CAP = 1.0;         // a single report's weight never exceeds this

export function jsonResponse(obj, init = {}) {
  return new Response(JSON.stringify(obj), {
    status: init.status || 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": init.cache || "no-store",
      ...(init.headers || {}),
    },
  });
}

export function snap(v) {
  return Math.round((Math.round(v / SNAP_DEG) * SNAP_DEG) * 1e6) / 1e6; // grid + drop FP noise
}

export function bboxOk(lat, lng) {
  return (
    typeof lat === "number" && typeof lng === "number" &&
    isFinite(lat) && isFinite(lng) &&
    lat >= BBOX.latMin && lat <= BBOX.latMax &&
    lng >= BBOX.lngMin && lng <= BBOX.lngMax
  );
}

export function normSpecies(s) {
  const k = String(s || "").trim().toLowerCase();
  return SPECIES.includes(k) ? k : null;
}

// Accept an ISO date (YYYY-MM-DD) within the last MAX_AGE_DAYS, not in the future.
export function dateOk(s, nowMs) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(s || ""))) return false;
  const t = Date.parse(s + "T12:00:00Z");
  if (isNaN(t)) return false;
  const ageDays = (nowMs - t) / 86400000;
  return ageDays >= -1 && ageDays <= MAX_AGE_DAYS;
}

// Collapse whitespace, trim, hard-cap length (user free-text fields).
export function clip(s, n) {
  return String(s == null ? "" : s).replace(/\s+/g, " ").trim().slice(0, n);
}

// Soft email check — enough to reject obvious junk, not RFC-perfect.
export function emailOk(s) {
  s = String(s || "").trim();
  return s.length >= 5 && s.length <= 120 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s);
}

export function clientIp(request) {
  return request.headers.get("CF-Connecting-IP") ||
         request.headers.get("x-forwarded-for") || "";
}

export async function sha256hex(str) {
  const data = new TextEncoder().encode(str);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Fixed-window rate limit via a KV counter. Returns true if OK to proceed.
export async function rateOk(env, id, limit, windowSec) {
  const key = `paddies:rl:${id}`;
  const cur = parseInt((await env.REPORTS_KV.get(key)) || "0", 10);
  if (cur >= limit) return false;
  await env.REPORTS_KV.put(key, String(cur + 1), { expirationTtl: windowSec });
  return true;
}

// The approved set lives in ONE JSON blob so the public GET is a single fast read.
export async function readApproved(env, nowMs) {
  const raw = await env.REPORTS_KV.get(APPROVED_KEY);
  let arr = [];
  try { arr = raw ? JSON.parse(raw) : []; } catch { arr = []; }
  // Drop anything past its hard expiry OR whose CATCH is older than the window.
  // The catch-age cap also retires legacy records written under the old
  // submission-anchored expiry, so nothing older than MAX_AGE_DAYS ever shows.
  return arr.filter((r) => {
    if (r.expiresAt && r.expiresAt <= nowMs) return false;
    const t = r.date ? Date.parse(r.date + "T12:00:00Z") : 0;
    return !t || (nowMs - t) / 86400000 <= MAX_AGE_DAYS;
  });
}

export async function writeApproved(env, arr) {
  const trimmed = arr.slice(-CAP); // ring-buffer: keep the newest CAP
  await env.REPORTS_KV.put(APPROVED_KEY, JSON.stringify(trimmed));
}

// Read every pending report (admin-only path, low volume).
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

// Approve (move pending -> approved blob) or reject (drop pending). Shared by
// the JSON moderate API and the server-rendered review page.
export async function applyModeration(env, id, action) {
  const pkey = PENDING_PREFIX + id;
  const raw = await env.REPORTS_KV.get(pkey);
  if (!raw) return { ok: false, error: "not found", status: 404 };
  const rec = JSON.parse(raw);
  await env.REPORTS_KV.delete(pkey);
  if (action === "reject") {
    await bumpReputation(env, rec.email, { rejected: 1 });
    return { ok: true, action: "reject", id };
  }
  const nowMs = Date.now();
  const arr = await readApproved(env, nowMs);
  // Corroboration on arrival: approved reports in the same zone+window from
  // genuinely DISTINCT sources (different IP AND different email).
  const myEmail = (rec.email || "").toLowerCase();
  const near = isPositive(rec) ? arr.filter((o) => isPositive(o)
    && (o.email || "").toLowerCase() !== myEmail && o.ipHash !== rec.ipHash
    && nmBetween(o, rec) <= CORROBORATE_NM && withinDays(o, rec, CORROBORATE_DAYS)) : [];
  rec.status = "approved";
  rec.approvedAt = nowMs;
  arr.push(rec);
  await writeApproved(env, arr);
  // Credit the reporter for the approval (+ a corroboration if independently
  // confirmed), and credit each distinct source it just corroborated.
  await bumpReputation(env, rec.email, { approved: 1, corroborated: near.length ? 1 : 0 });
  const credited = new Set();
  for (const o of near) {
    const e = (o.email || "").toLowerCase();
    if (e && !credited.has(e)) { credited.add(e); await bumpReputation(env, o.email, { corroborated: 1 }); }
  }
  return { ok: true, action: "approve", id };
}

// Durable reporter-email log (no TTL) — the moderator's saved list, kept even
// after a report expires/is rejected. Keyed by a hash of the email so re-submits
// update one entry (race-free across distinct emails).
export async function recordEmail(env, email, name, species, date, ts) {
  const key = "paddies:email:" + (await sha256hex(email.toLowerCase())).slice(0, 16);
  let rec = {};
  try { const raw = await env.REPORTS_KV.get(key); if (raw) rec = JSON.parse(raw); } catch { /* new */ }
  rec.email = email;
  rec.name = name || rec.name || "";
  rec.lastSpecies = species;
  rec.lastDate = date;
  rec.lastTs = ts;
  rec.firstTs = rec.firstTs || ts;
  rec.count = (rec.count || 0) + 1;
  await env.REPORTS_KV.put(key, JSON.stringify(rec));
}

export async function listEmails(env) {
  const out = [];
  let cursor;
  do {
    const res = await env.REPORTS_KV.list({ prefix: "paddies:email:", cursor, limit: 1000 });
    for (const k of res.keys) {
      const raw = await env.REPORTS_KV.get(k.name);
      if (raw) { try { out.push(JSON.parse(raw)); } catch { /* skip */ } }
    }
    cursor = res.list_complete ? null : res.cursor;
  } while (cursor);
  out.sort((a, b) => (b.lastTs || 0) - (a.lastTs || 0));
  return out;
}

// --- Trust math ----------------------------------------------------------
function toRad(d) { return d * Math.PI / 180; }
export function nmBetween(a, b) {
  const R = 3440.065; // Earth radius in nautical miles
  const dLat = toRad(b.lat - a.lat), dLng = toRad(b.lng - a.lng);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}
export function withinDays(a, b, d) {
  const ta = Date.parse((a.date || "") + "T12:00:00Z"), tb = Date.parse((b.date || "") + "T12:00:00Z");
  if (isNaN(ta) || isNaN(tb)) return true;
  return Math.abs(ta - tb) <= d * 86400000;
}
export function isPositive(r) { return !!r && r.species !== "no-paddies"; }

// Reporter reputation = Beta posterior mean with a deliberately skeptical prior.
// Successes = moderator approvals + independent corroborations; failures =
// rejections. New/anonymous reporters sit at the prior (~0.2) and must EARN trust.
export function repScore(rec) {
  const succ = (rec.approved || 0) + (rec.corroborated || 0);
  const fail = (rec.rejected || 0);
  return (succ + REP_PRIOR_A) / (succ + fail + REP_PRIOR_A + REP_PRIOR_B);
}

export function buildRepMap(emailRecs) {
  const m = {};
  for (const e of emailRecs) { if (e.email) m[e.email.toLowerCase()] = repScore(e); }
  return m;
}

// Attach a confidence tier + corroboration count + capped weight to each
// approved report. Corroboration counts only genuinely-distinct sources
// (min of distinct IPs and distinct emails in the zone), so one person can't
// self-corroborate by resubmitting. A high-rep reporter can confirm solo.
export function attachConfidence(approved, repMap) {
  return approved.map((r) => {
    const cluster = approved.filter((o) => isPositive(o) && nmBetween(o, r) <= CORROBORATE_NM && withinDays(o, r, CORROBORATE_DAYS));
    const ips = new Set(cluster.map((o) => o.ipHash).filter(Boolean));
    const emails = new Set(cluster.map((o) => (o.email || "").toLowerCase()).filter(Boolean));
    const sources = Math.min(ips.size, emails.size);   // distinct IP *and* email
    const maxRep = cluster.reduce((mx, o) => Math.max(mx, repMap[(o.email || "").toLowerCase()] || 0), 0);
    const confirmed = sources >= CONFIRM_MIN_SOURCES || maxRep >= TRUSTED_REP;
    const weight = Math.min(WEIGHT_CAP, 0.3 + 0.25 * Math.max(0, sources - 1) + 0.4 * maxRep);
    const tier = weight >= 0.75 ? "strong" : (confirmed ? "confirmed" : "unconfirmed");
    return { ...r, _sources: sources, _weight: Math.round(weight * 100) / 100, _tier: tier };
  });
}

// Update a reporter's reputation counters on the durable email record
// (preserves the name/last-seen fields recordEmail wrote).
export async function bumpReputation(env, email, deltas) {
  if (!email) return;
  const key = "paddies:email:" + (await sha256hex(email.toLowerCase())).slice(0, 16);
  let rec = {};
  try { const raw = await env.REPORTS_KV.get(key); if (raw) rec = JSON.parse(raw); } catch { /* new */ }
  rec.email = email;
  rec.approved = (rec.approved || 0) + (deltas.approved || 0);
  rec.rejected = (rec.rejected || 0) + (deltas.rejected || 0);
  rec.corroborated = (rec.corroborated || 0) + (deltas.corroborated || 0);
  await env.REPORTS_KV.put(key, JSON.stringify(rec));
}

// Admin gate — same shape as analytics gate(), but its own token.
export function modGate(request, env) {
  const expected = env && env.MODERATION_TOKEN;
  if (!expected) return "unconfigured";
  const url = new URL(request.url);
  const token = url.searchParams.get("key") || request.headers.get("x-mod-token") || "";
  if (token.length !== expected.length || token !== expected) return "denied";
  return null;
}

// What the public map gets — never leak deviceId / ipHash / email. Includes
// the derived (non-PII) trust signal: tier, distinct-source count, capped weight.
export function publicView(r) {
  return {
    id: r.id, lat: r.lat, lng: r.lng, species: r.species, date: r.date, source: "reported",
    confidence: r._tier || "unconfirmed",
    sources: r._sources || 1,
    weight: r._weight != null ? r._weight : 0.3,
  };
}
