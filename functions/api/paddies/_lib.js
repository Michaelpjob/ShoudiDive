// Shared helpers for the /paddies/ crowdsourced catch-report API.
// Mirrors functions/api/analytics/_lib.js conventions: env bindings are
// dashboard-only, no CORS (same-origin), no PII stored in clear.
//
// Bindings used: REPORTS_KV (KV namespace), MODERATION_TOKEN (secret).

// Southern California Bight field bounds (must match the tool's FIELD_BBOX).
export const BBOX = { latMin: 31.0, latMax: 34.8, lngMin: -121.5, lngMax: -116.8 };

export const SNAP_DEG = 0.02;       // ~1.2 nm grid — honey-hole protection + dedupe
export const MAX_AGE_DAYS = 14;     // a catch must be within this; pending + approved expire on it too
export const CAP = 500;             // ring-buffer: keep at most this many approved reports
export const APPROVED_KEY = "paddies:approved";
export const PENDING_PREFIX = "paddies:pending:";

// Species dropdown allowlist — accepting only these eliminates free-text abuse.
export const SPECIES = [
  "yellowtail", "dorado", "bluefin", "yellowfin", "white seabass",
  "bonito", "barracuda", "calico bass", "paddy",
];

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
  return Math.round(v / SNAP_DEG) * SNAP_DEG;
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
  return arr.filter((r) => !r.expiresAt || r.expiresAt > nowMs);
}

export async function writeApproved(env, arr) {
  const trimmed = arr.slice(-CAP); // ring-buffer: keep the newest CAP
  await env.REPORTS_KV.put(APPROVED_KEY, JSON.stringify(trimmed));
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

// What the public map gets — never leak deviceId / ipHash.
export function publicView(r) {
  return { id: r.id, lat: r.lat, lng: r.lng, species: r.species, date: r.date, source: "reported" };
}
